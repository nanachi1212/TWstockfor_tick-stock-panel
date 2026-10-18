"""访问认证 API。

端点:
  GET  /api/auth/status        — 是否已设密码、当前会话是否有效
  POST /api/auth/setup         — 首次设置密码(仅限本机/内网, 防公网抢占)
  POST /api/auth/login         — 登录(密码 → 会话 token, 含限流)
  POST /api/auth/logout        — 注销当前会话
  POST /api/auth/change-password — 改密码(需已登录)

安全:
  - setup 端点只接受本机/内网请求(request.client.host), 公网请求 403。
    否则黑客可比用户更早扫到域名, 抢先设密码, 反客为主。
  - login 限流: 同一来源 IP 连续失败 5 次, 锁 5 分钟(内存计数)。
  - 会话 token 通过 HttpOnly cookie 下发, 前端无需手动管理。
"""
from __future__ import annotations

import ipaddress
import logging
import time
from collections import defaultdict
from threading import Lock
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.services import auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "tf_session"
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 与 SESSION_TTL 一致

# 限流: { ip: (fail_count, lock_until_ts) }
_fail_counter: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
_fail_lock = Lock()
_MAX_FAILS = 5
_LOCK_SECONDS = 300


def _is_local_network(host: str | None) -> bool:
    """该地址是否本机或内网地址(判断整条请求来源请用 _is_local_request)。"""
    if not host:
        return False
    if host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return True

    # 内网网段: 10.x / 172.16-31.x / 192.168.x
    if host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
            if 16 <= second <= 31:
                return True
        except (IndexError, ValueError):
            pass
    return False


def _xff_hops(request: Request) -> list[str]:
    """X-Forwarded-For 各跳(左=最远, 右=相邻反代追加)。

    多行头按出现顺序合并: HAProxy 等会另起一行追加, 只读第一行会读到客户端自填的值。
    """
    joined = ",".join(request.headers.getlist("x-forwarded-for"))
    return [hop.strip() for hop in joined.split(",") if hop.strip()]


def _client_ip(request: Request) -> str:
    """取客户端 IP(登录限流键 / 日志)。

    仅当直连 peer 是本机/内网(可信反代)时采信 X-Forwarded-For: 从右往左跳过本机/内网
    反代追加的跳, 取第一个公网跳(其左侧可能是客户端自填, 不采信); 全是内网则取最左。
    多层反代时不会把所有人归到同一个反代 IP 上(否则一人失败即锁死全部登录)。
    """
    direct = request.client.host if request.client else ""
    hops = _xff_hops(request) if direct and _is_local_network(direct) else []
    for hop in reversed(hops):
        if not _is_local_network(hop):
            return hop
    return hops[0] if hops else (direct or "unknown")


# 不可公网注册的本地域名后缀(RFC 6761 / RFC 6762 附录 G / RFC 8375)。
# ponytail: 内网自有公网域名(如 nas.example.com)与 DNS rebinding 无法区分, 未设密码时会被拒;
# 设密码后走会话认证不受影响, 真有需求再加主机名白名单配置。
_LOCAL_NAME_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".home.arpa",
    ".lan", ".home", ".internal", ".intranet", ".private", ".corp",
)


def _hostname(netloc: str) -> str | None:
    try:
        return urlsplit("//" + netloc).hostname
    except ValueError:
        return None


def _ip(name: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(name or "")
    except ValueError:
        return None


def _is_local_name(name: str | None) -> bool:
    """单标签主机名或本地后缀: 外站无法注册, DNS rebinding 无法伪造。"""
    name = (name or "").rstrip(".")
    return bool(name) and ("." not in name or name.endswith(_LOCAL_NAME_SUFFIXES))


def _is_same_site_browser(request: Request) -> bool:
    """拒绝外站页面借用户浏览器发来的请求(CSRF)与 DNS rebinding。

    - Sec-Fetch-Site 为 cross-site/same-site: 浏览器明确标记的外站请求;
    - Host 须是 IP / 单标签 / 本地后缀: rebinding 时 Host 是攻击者的公网域名;
    - Origin 须与 Host 同源, 或(反代改写 Host 时)本身是内网 IP / 本地主机名。
    非浏览器客户端不带 Origin/Sec-Fetch-*, 仍由来源 IP 闸门把关。
    Host 与 Origin 都从原始头解析, 不用 request.url(Host 不合法时会回落到服务器地址)。
    """
    if request.headers.get("sec-fetch-site", "").lower() in ("cross-site", "same-site"):
        return False
    host = request.headers.get("host", "")
    host_name = _hostname(host)
    if host and not (_ip(host_name) is not None or _is_local_name(host_name)):
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return True
    try:
        parts = urlsplit(origin)
        origin_name = parts.hostname
    except ValueError:
        return False
    if host and parts.netloc.lower() == host.lower():
        return True
    origin_ip = _ip(origin_name)
    return origin_ip.is_private if origin_ip is not None else _is_local_name(origin_name)


def _is_local_request(request: Request) -> bool:
    """未设密码时的放行条件: 来源确为本机/内网, 且不是外站页面经浏览器发起。

    直连 peer 与可信反代转发的每一跳都须是本机/内网: 多层本机反代时最右一跳是
    上一层反代, 不能只看最右; 任一跳为公网即视为公网(自填公网 IP 只会拒掉自己)。
    """
    direct = request.client.host if request.client else ""
    return (
        _is_local_network(direct)
        and all(_is_local_network(hop) for hop in _xff_hops(request))
        and _is_same_site_browser(request)
    )


def _check_login_rate_limit(ip: str) -> None:
    """登录失败限流检查, 触发则抛 429。锁定过期后重置计数(重新给 5 次机会)。"""
    with _fail_lock:
        count, until = _fail_counter.get(ip, (0, 0.0))
        now = time.time()
        if until > now:
            wait = int(until - now)
            raise HTTPException(
                status_code=429,
                detail=f"登录失败次数过多, 请 {wait} 秒后重试",
            )
        if until and until <= now:
            # 锁定已过期: 清除旧计数, 否则之后每失败一次都会立刻再锁 5 分钟
            _fail_counter.pop(ip, None)


def _record_login_fail(ip: str) -> None:
    """记录一次登录失败, 达阈值则锁定。"""
    with _fail_lock:
        # 防内存膨胀: 条目过多时清掉已过锁定期的记录
        if len(_fail_counter) > 1000:
            now = time.time()
            for stale in [k for k, (_, u) in _fail_counter.items() if u <= now]:
                _fail_counter.pop(stale, None)
        count, until = _fail_counter.get(ip, (0, 0.0))
        count += 1
        if count >= _MAX_FAILS:
            until = time.time() + _LOCK_SECONDS
            logger.warning("auth login locked for %s after %d fails", ip, count)
        _fail_counter[ip] = (count, until)


def _clear_login_fails(ip: str) -> None:
    """登录成功后清除该 IP 的失败计数。"""
    with _fail_lock:
        _fail_counter.pop(ip, None)


# ================================================================
# 端点
# ================================================================

class PasswordIn(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


@router.get("/status")
def auth_status(request: Request) -> dict:
    """认证状态: 是否已设密码 + 当前请求是否已登录。"""
    token = request.cookies.get(COOKIE_NAME)
    return {
        "configured": auth.is_configured(),
        "authenticated": bool(token and auth.is_valid_session(token)),
    }


@router.post("/setup")
def setup_password(req: PasswordIn, request: Request) -> dict:
    """首次设置访问密码。仅限本机/内网请求(防公网抢占)。

    若已设置过密码, 返回 409(改密码走 /change-password)。
    """
    # 关键: 限制只有服务器主人(本机/内网)能设密码
    client_ip = _client_ip(request)
    if not _is_local_request(request):
        logger.warning("setup rejected from non-local ip: %s", client_ip)
        raise HTTPException(
            status_code=403,
            detail="首次设置密码仅允许本机或内网访问,请通过 SSH/本地浏览器操作",
        )

    if auth.is_configured():
        raise HTTPException(status_code=409, detail="密码已设置,如需修改请登录后使用改密码功能")

    auth.set_password(req.password)
    logger.info("access password set up from %s", client_ip)
    return {"ok": True, "configured": True}


@router.post("/login")
def login(req: LoginIn, request: Request, response: Response) -> dict:
    """登录: 密码 → 会话 token(写 HttpOnly cookie)。含失败限流。"""
    ip = _client_ip(request)
    _check_login_rate_limit(ip)

    if not auth.is_configured():
        raise HTTPException(status_code=409, detail="尚未设置访问密码")

    token = auth.verify_and_create_session(req.password)
    if not token:
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="密码错误")

    _clear_login_fails(ip)
    # HttpOnly: 防 XSS 窃取; SameSite=Lax: 防 CSRF; Path=/: 全站生效
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # 自托管可能无 HTTPS, 不强制 secure(建议反代加 HTTPS)
    )
    return {"ok": True, "authenticated": True}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    """注销当前会话。"""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/change-password")
def change_password(req: ChangePasswordIn, request: Request) -> dict:
    """修改密码: 需验证旧密码, 成功后所有会话失效(含当前, 需重新登录)。"""
    token = request.cookies.get(COOKIE_NAME)
    if not (token and auth.is_valid_session(token)):
        raise HTTPException(status_code=401, detail="请先登录")

    if not auth.is_configured():
        raise HTTPException(status_code=409, detail="尚未设置访问密码")

    # 验证旧密码
    new_token = auth.verify_and_create_session(req.old_password)
    if not new_token:
        ip = _client_ip(request)
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="旧密码错误")
    # 临时 token 用完即弃
    auth.revoke_session(new_token)

    # 改密码(set_password 会清空所有会话)
    auth.set_password(req.new_password)
    return {"ok": True, "message": "密码已修改, 请重新登录"}

"""訪問認證 API。

端點:
  GET  /api/auth/status        — 是否已設密碼、當前會話是否有效
  POST /api/auth/setup         — 首次設定密碼(僅限本機/內網, 防公網搶佔)
  POST /api/auth/login         — 登入(密碼 → 會話 token, 含限流)
  POST /api/auth/logout        — 註銷當前會話
  POST /api/auth/change-password — 改密碼(需已登入)

安全:
  - setup 端點只接受本機/內網請求(request.client.host), 公網請求 403。
    否則黑客可比使用者更早掃到域名, 搶先設密碼, 反客為主。
  - login 限流: 同一來源 IP 連續失敗 5 次, 鎖 5 分鐘(記憶體計數)。
  - 會話 token 通過 HttpOnly cookie 下發, 前端無需手動管理。
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
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 與 SESSION_TTL 一致

# 限流: { ip: (fail_count, lock_until_ts) }
_fail_counter: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))
_fail_lock = Lock()
_MAX_FAILS = 5
_LOCK_SECONDS = 300


def _is_local_network(host: str | None) -> bool:
    """該地址是否本機或內網地址(判斷整條請求來源請用 _is_local_request)。"""
    if not host:
        return False
    if host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return True

    # 內網網段: 10.x / 172.16-31.x / 192.168.x
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
    """X-Forwarded-For 各跳(左=最遠, 右=相鄰反代追加)。

    多行頭按出現順序合併: HAProxy 等會另起一行追加, 只讀第一行會讀到客戶端自填的值。
    """
    joined = ",".join(request.headers.getlist("x-forwarded-for"))
    return [hop.strip() for hop in joined.split(",") if hop.strip()]


def _client_ip(request: Request) -> str:
    """取客戶端 IP(登入限流鍵 / 日誌)。

    僅當直連 peer 是本機/內網(可信反代)時採信 X-Forwarded-For: 從右往左跳過本機/內網
    反代追加的跳, 取第一個公網跳(其左側可能是客戶端自填, 不採信); 全是內網則取最左。
    多層反代時不會把所有人歸到同一個反代 IP 上(否則一人失敗即鎖死全部登入)。
    """
    direct = request.client.host if request.client else ""
    hops = _xff_hops(request) if direct and _is_local_network(direct) else []
    for hop in reversed(hops):
        if not _is_local_network(hop):
            return hop
    return hops[0] if hops else (direct or "unknown")


# 不可公網註冊的本地域名後綴(RFC 6761 / RFC 6762 附錄 G / RFC 8375)。
# ponytail: 內網自有公網域名(如 nas.example.com)與 DNS rebinding 無法區分, 未設密碼時會被拒;
# 設密碼後走會話認證不受影響, 真有需求再加主機名白名單配置。
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
    """單標籤主機名或本地後綴: 外站無法註冊, DNS rebinding 無法偽造。"""
    name = (name or "").rstrip(".")
    return bool(name) and ("." not in name or name.endswith(_LOCAL_NAME_SUFFIXES))


def _is_same_site_browser(request: Request) -> bool:
    """拒絕外站頁面借使用者瀏覽器發來的請求(CSRF)與 DNS rebinding。

    - Sec-Fetch-Site 為 cross-site/same-site: 瀏覽器明確標記的外站請求;
    - Host 須是 IP / 單標籤 / 本地後綴: rebinding 時 Host 是攻擊者的公網域名;
    - Origin 須與 Host 同源, 或(反代改寫 Host 時)本身是內網 IP / 本地主機名。
    非瀏覽器客戶端不帶 Origin/Sec-Fetch-*, 仍由來源 IP 閘門把關。
    Host 與 Origin 都從原始頭解析, 不用 request.url(Host 不合法時會回落到伺服器地址)。
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
    """未設密碼時的放行條件: 來源確為本機/內網, 且不是外站頁面經瀏覽器發起。

    直連 peer 與可信反代轉發的每一跳都須是本機/內網: 多層本機反代時最右一跳是
    上一層反代, 不能只看最右; 任一跳為公網即視為公網(自填公網 IP 只會拒掉自己)。
    """
    direct = request.client.host if request.client else ""
    return (
        _is_local_network(direct)
        and all(_is_local_network(hop) for hop in _xff_hops(request))
        and _is_same_site_browser(request)
    )


def _is_loopback_request(request: Request) -> bool:
    """驗證專用密碼的放行條件: 直連來自本機回環、沒有任何反代轉發 (tailscale serve 會帶 X-Forwarded-For), 且非外站。"""
    direct = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(direct).is_loopback
    except ValueError:
        loopback = False
    return loopback and not _xff_hops(request) and _is_same_site_browser(request)


def _check_login_rate_limit(ip: str) -> None:
    """登入失敗限流檢查, 觸發則拋 429。鎖定過期後重置計數(重新給 5 次機會)。"""
    with _fail_lock:
        count, until = _fail_counter.get(ip, (0, 0.0))
        now = time.time()
        if until > now:
            wait = int(until - now)
            raise HTTPException(
                status_code=429,
                detail=f"登入失敗次數過多, 請 {wait} 秒後重試",
            )
        if until and until <= now:
            # 鎖定已過期: 清除舊計數, 否則之後每失敗一次都會立刻再鎖 5 分鐘
            _fail_counter.pop(ip, None)


def _record_login_fail(ip: str) -> None:
    """記錄一次登入失敗, 達閾值則鎖定。"""
    with _fail_lock:
        # 防記憶體膨脹: 條目過多時清掉已過鎖定期的記錄
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
    """登入成功後清除該 IP 的失敗計數。"""
    with _fail_lock:
        _fail_counter.pop(ip, None)


# ================================================================
# 端點
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
    """認證狀態: 是否已設密碼 + 當前請求是否已登入。"""
    token = request.cookies.get(COOKIE_NAME)
    return {
        "configured": auth.is_configured(),
        "authenticated": bool(token and auth.is_valid_session(token)),
    }


@router.post("/setup")
def setup_password(req: PasswordIn, request: Request) -> dict:
    """首次設定訪問密碼。僅限本機/內網請求(防公網搶佔)。

    若已設定過密碼, 返回 409(改密碼走 /change-password)。
    """
    # 關鍵: 限制只有伺服器主人(本機/內網)能設密碼
    client_ip = _client_ip(request)
    if not _is_local_request(request):
        logger.warning("setup rejected from non-local ip: %s", client_ip)
        raise HTTPException(
            status_code=403,
            detail="首次設定密碼僅允許本機或內網訪問,請通過 SSH/本地瀏覽器操作",
        )

    if auth.is_configured():
        raise HTTPException(status_code=409, detail="密碼已設定,如需修改請登入後使用改密碼功能")

    auth.set_password(req.password)
    logger.info("access password set up from %s", client_ip)
    return {"ok": True, "configured": True}


@router.post("/login")
def login(req: LoginIn, request: Request, response: Response) -> dict:
    """登入: 密碼 → 會話 token(寫 HttpOnly cookie)。含失敗限流。"""
    ip = _client_ip(request)
    _check_login_rate_limit(ip)

    if not auth.is_configured():
        raise HTTPException(status_code=409, detail="尚未設定訪問密碼")

    token = auth.verify_and_create_session(req.password)
    if token:
        _clear_login_fails(ip)
    elif _is_loopback_request(request):
        # 驗證專用密碼登入成功不清主密碼的失敗計數, 否則可藉它無限猜主密碼。
        token = auth.create_verify_session(req.password)
    if not token:
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="密碼錯誤")

    # HttpOnly: 防 XSS 竊取; SameSite=Lax: 防 CSRF; Path=/: 全站生效
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # 自託管可能無 HTTPS, 不強制 secure(建議反代加 HTTPS)
    )
    return {"ok": True, "authenticated": True}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    """註銷當前會話。"""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/change-password")
def change_password(req: ChangePasswordIn, request: Request) -> dict:
    """修改密碼: 需驗證舊密碼, 成功後所有會話失效(含當前, 需重新登入)。"""
    token = request.cookies.get(COOKIE_NAME)
    if not (token and auth.is_valid_session(token)):
        raise HTTPException(status_code=401, detail="請先登入")

    if not auth.is_configured():
        raise HTTPException(status_code=409, detail="尚未設定訪問密碼")

    # 驗證舊密碼
    new_token = auth.verify_and_create_session(req.old_password)
    if not new_token:
        ip = _client_ip(request)
        _record_login_fail(ip)
        raise HTTPException(status_code=401, detail="舊密碼錯誤")
    # 臨時 token 用完即棄
    auth.revoke_session(new_token)

    # 改密碼(set_password 會清空所有會話)
    auth.set_password(req.new_password)
    return {"ok": True, "message": "密碼已修改, 請重新登入"}

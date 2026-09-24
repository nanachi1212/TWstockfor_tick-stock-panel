"""告警触发记录存储 — JSONL 追加写 + 滚动清理。

职责:
  - 把每次触发的 AlertEvent 追加写入 data/user_data/alerts.jsonl
  - 提供查询 (按来源/类型过滤、时间倒序、限量)
  - 滚动清理: 保留近 N 天 + 上限 M 条 (取交集)

设计:
  - JSONL 每行一个 JSON 对象,便于增量追加和流式读取
  - 清理策略: 追加后按需 prune (按 ts 删旧),避免文件无限膨胀
  - 读时全量加载到内存过滤 (记录量受上限约束, 5000 条量级无压力)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# 保留策略
MAX_DAYS = 7
MAX_RECORDS = 5000
# 每隔多少次写入触发一次清理 (避免每次写都 prune)
PRUNE_EVERY = 20

_lock = threading.Lock()
_write_count = 0


def _identity(event: dict) -> str:
    alert_id = event.get("alert_id")
    if isinstance(alert_id, str) and alert_id:
        return alert_id
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "legacy_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _prepare_event(event: dict) -> dict:
    result = dict(event)
    result.setdefault("alert_id", f"alert_{uuid.uuid4().hex}")
    result.setdefault("is_read", False)
    return result


def _path(data_dir: Path) -> Path:
    p = data_dir / "user_data" / "alerts.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _rewrite_locked(path: Path, events: list[dict]) -> None:
    """Replace the JSONL file only after its complete replacement is written."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            for event in events:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("alert_store temporary rewrite cleanup failed: %s", temporary)


def append(data_dir: Path, event: dict) -> None:
    """追加一条触发记录。event 应含 ts(毫秒)、rule_id、source 等字段。"""
    line = json.dumps(_prepare_event(event), ensure_ascii=False)
    with _lock:
        p = _path(data_dir)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        global _write_count
        _write_count += 1
        if _write_count >= PRUNE_EVERY:
            _write_count = 0
            _prune_locked(p)


def append_many(data_dir: Path, events: list[dict]) -> None:
    """Append a complete batch atomically so failed writes cannot leave a prefix."""
    if not events:
        return
    lines = [json.dumps(_prepare_event(ev), ensure_ascii=False) for ev in events]
    with _lock:
        p = _path(data_dir)
        original = p.read_bytes() if p.exists() else b""
        payload = original
        if payload and not payload.endswith(b"\n"):
            payload += b"\n"
        payload += ("\n".join(lines) + "\n").encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=p.parent, prefix=f".{p.name}.",
                suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, p)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("alert_store temporary append cleanup failed: %s", temporary)
        global _write_count
        _write_count += len(events)
        if _write_count >= PRUNE_EVERY:
            _write_count = 0
            _prune_locked(p)


def list_recent(
    data_dir: Path,
    days: int = MAX_DAYS,
    limit: int = MAX_RECORDS,
    source: str | None = None,
    type: str | None = None,
) -> list[dict]:
    """读取近 N 天记录,按时间倒序,支持按 source/type 过滤。

    持锁读: prune/delete/clear 会整文件重写, 无锁读可能读到截断内容。
    """
    import time
    cutoff = (time.time() - days * 86400) * 1000  # 毫秒
    out: list[dict] = []
    p = _path(data_dir)
    if not p.exists():
        return []
    try:
        with _lock, p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("ts", 0) < cutoff:
                    continue
                if source and ev.get("source") != source:
                    continue
                if type and ev.get("type") != type:
                    continue
                ev.setdefault("alert_id", _identity(ev))
                ev.setdefault("is_read", False)
                out.append(ev)
    except Exception as e:
        logger.warning("alert_store read failed: %s", e)
        return []
    # 时间倒序 + 截断
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out[:limit]


def clear(data_dir: Path) -> int:
    """清空全部记录,返回清除的条数。"""
    with _lock:
        p = _path(data_dir)
        if not p.exists():
            return 0
        count = 0
        try:
            with p.open("r", encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip())
        except Exception:
            pass
        _rewrite_locked(p, [])
        return count


def delete_one(data_dir: Path, ts: int) -> bool:
    """删除指定 ts 的单条记录,返回是否删除成功。

    JSONL 无主键, 用 ts(毫秒时间戳) 作为标识。
    若存在多条同 ts, 只删第一条。
    """
    with _lock:
        p = _path(data_dir)
        if not p.exists():
            return False
        kept: list[dict] = []
        deleted = False
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    if not deleted and ev.get("ts") == ts:
                        deleted = True
                        continue
                    kept.append(ev)
        except Exception as e:
            logger.warning("alert_store delete_one read failed: %s", e)
            return False
        if not deleted:
            return False
        try:
            _rewrite_locked(p, kept)
        except Exception as e:
            logger.warning("alert_store delete_one write failed: %s", e)
            return False
        return True


def update_read(data_dir: Path, alert_id: str | None = None, *, read: bool = True) -> int:
    """Mark one alert or every alert as read/unread, preserving legacy identities."""
    with _lock:
        p = _path(data_dir)
        if not p.exists():
            return 0
        kept: list[dict] = []
        updated = 0
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    identity = _identity(event)
                    if alert_id is None or identity == alert_id:
                        event["alert_id"] = identity
                        if bool(event.get("is_read", False)) != read:
                            updated += 1
                        event["is_read"] = read
                    kept.append(event)
        except OSError as e:
            logger.warning("alert_store read status update failed: %s", e)
            raise
        if alert_id is not None and not any(_identity(event) == alert_id for event in kept):
            return 0
        try:
            _rewrite_locked(p, kept)
        except OSError as e:
            logger.warning("alert_store read status write failed: %s", e)
            raise
        return updated


def delete_by_id(data_dir: Path, alert_id: str) -> bool:
    """Delete one alert by its stable identity, including legacy records."""
    with _lock:
        p = _path(data_dir)
        if not p.exists():
            return False
        kept: list[dict] = []
        deleted = False
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    if not deleted and _identity(event) == alert_id:
                        deleted = True
                        continue
                    kept.append(event)
        except OSError as e:
            logger.warning("alert_store delete-by-id read failed: %s", e)
            raise
        if not deleted:
            return False
        try:
            _rewrite_locked(p, kept)
        except OSError as e:
            logger.warning("alert_store delete-by-id write failed: %s", e)
            raise
        return True


def count(data_dir: Path) -> int:
    """返回当前记录总数。持锁读, 防与整文件重写并发。"""
    p = _path(data_dir)
    if not p.exists():
        return 0
    try:
        with _lock, p.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _prune_locked(p: Path) -> None:
    """(调用方需持锁) 保留近 MAX_DAYS 天 + 上限 MAX_RECORDS 条。"""
    import time
    cutoff = (time.time() - MAX_DAYS * 86400) * 1000
    kept: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("ts", 0) >= cutoff:
                    kept.append(ev)
    except FileNotFoundError:
        return
    except Exception as e:
        logger.warning("alert_store prune read failed: %s", e)
        return
    # 上限截断 (保留最新的)
    if len(kept) > MAX_RECORDS:
        kept.sort(key=lambda x: x.get("ts", 0))
        kept = kept[-MAX_RECORDS:]
    # 重写文件
    try:
        _rewrite_locked(p, kept)
    except Exception as e:
        logger.warning("alert_store prune write failed: %s", e)

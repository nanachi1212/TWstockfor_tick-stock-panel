"""告警觸發記錄存儲 — JSONL 追加寫 + 滾動清理。

職責:
  - 把每次觸發的 AlertEvent 追加寫入 data/user_data/alerts.jsonl
  - 提供查詢 (按來源/類型過濾、時間倒序、限量)
  - 滾動清理: 保留近 N 天 + 上限 M 條 (取交集)

設計:
  - JSONL 每行一個 JSON 對象,便於增量追加和流式讀取
  - 清理策略: 追加後按需 prune (按 ts 刪舊),避免文件無限膨脹
  - 讀時全量載入到記憶體過濾 (記錄量受上限約束, 5000 條量級無壓力)
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
# 每隔多少次寫入觸發一次清理 (避免每次寫都 prune)
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


def _normalize_legacy_change_pct(event: dict) -> None:
    """Convert pre-ratio records to the ratio contract at read time; the file is never rewritten.

    Legacy writers stored percent points. Each case is identified by how that writer built
    the record, not by the size of the value:
      - change_pct_*: change_pct was a verbatim copy of trigger_value (percent points).
      - watchlist_anomaly: the message embeds the same value formatted as "{pct:+.2f}%".
    """
    pct = event.get("change_pct")
    if not isinstance(pct, (int, float)) or isinstance(pct, bool) or pct == 0:
        return
    event_type = str(event.get("type") or "")
    legacy = (
        event_type.startswith("change_pct_") and pct == event.get("trigger_value")
    ) or (
        event_type == "watchlist_anomaly" and f"{pct:+.2f}%" in str(event.get("message") or "")
    )
    # ponytail: dead once every pre-ratio record is past MAX_DAYS retention; delete then.
    if legacy:
        event["change_pct"] = round(pct / 100, 6)


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
    """追加一條觸發記錄。event 應含 ts(毫秒)、rule_id、source 等字段。"""
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


def append_many(data_dir: Path, events: list[dict]) -> list[str]:
    """Append a complete batch atomically so failed writes cannot leave a prefix."""
    if not events:
        return []
    with _lock:
        p = _path(data_dir)
        original = p.read_bytes() if p.exists() else b""
        existing_ids: set[str] = set()
        for line in original.decode("utf-8", errors="replace").splitlines():
            try:
                existing = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(existing, dict):
                existing_ids.add(str(existing.get("alert_id") or _identity(existing)))

        prepared: list[dict] = []
        for event in events:
            item = _prepare_event(event)
            alert_id = str(item["alert_id"])
            if alert_id not in existing_ids:
                prepared.append(item)
                existing_ids.add(alert_id)
        if not prepared:
            return []

        lines = [json.dumps(event, ensure_ascii=False) for event in prepared]
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
        _write_count += len(prepared)
        if _write_count >= PRUNE_EVERY:
            _write_count = 0
            _prune_locked(p)
        return [str(event["alert_id"]) for event in prepared]


def list_recent(
    data_dir: Path,
    days: int = MAX_DAYS,
    limit: int = MAX_RECORDS,
    source: str | None = None,
    type: str | None = None,
) -> list[dict]:
    """讀取近 N 天記錄,按時間倒序,支持按 source/type 過濾。

    持鎖讀: prune/delete/clear 會整文件重寫, 無鎖讀可能讀到截斷內容。
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
                _normalize_legacy_change_pct(ev)
                out.append(ev)
    except Exception as e:
        logger.warning("alert_store read failed: %s", e)
        return []
    # 時間倒序 + 截斷
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out[:limit]


def clear(data_dir: Path) -> int:
    """清空全部記錄,返回清除的條數。"""
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
    """刪除指定 ts 的單條記錄,返回是否刪除成功。

    JSONL 無主鍵, 用 ts(毫秒時間戳) 作為標識。
    若存在多條同 ts, 只刪第一條。
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
    """返回當前記錄總數。持鎖讀, 防與整文件重寫併發。"""
    p = _path(data_dir)
    if not p.exists():
        return 0
    try:
        with _lock, p.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _prune_locked(p: Path) -> None:
    """(呼叫方需持鎖) 保留近 MAX_DAYS 天 + 上限 MAX_RECORDS 條。"""
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
    # 上限截斷 (保留最新的)
    if len(kept) > MAX_RECORDS:
        kept.sort(key=lambda x: x.get("ts", 0))
        kept = kept[-MAX_RECORDS:]
    # 重寫文件
    try:
        _rewrite_locked(p, kept)
    except Exception as e:
        logger.warning("alert_store prune write failed: %s", e)

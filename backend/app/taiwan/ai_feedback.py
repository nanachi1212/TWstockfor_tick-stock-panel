"""AI 說明「準／不準」一鍵回饋。

只做兩件事: 把回饋追加到 data/user_data/ai_feedback.jsonl、算出各模型的準確感受統計。
不改變任何 AI 結果、不參與排名；目的是讓使用者一週後知道哪個模型值得用。
"""
# ruff: noqa: RUF002 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MAX_NOTE = 200


def _path(data_dir: Path | None = None) -> Path:
    root = data_dir or settings.data_dir
    p = Path(root) / "user_data" / "ai_feedback.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record(
    *,
    symbol: str,
    helpful: bool,
    record_id: str | None = None,
    model: str | None = None,
    note: str | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    entry = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "symbol": str(symbol or "").strip().upper(),
        "record_id": str(record_id or "").strip() or None,
        "model": str(model or "").strip() or None,
        "helpful": bool(helpful),
        "note": (str(note or "").strip()[:_MAX_NOTE] or None),
    }
    if not entry["symbol"]:
        raise ValueError("symbol is required")
    line = json.dumps(entry, ensure_ascii=False)
    with _LOCK, _path(data_dir).open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return entry


def _read(data_dir: Path | None = None) -> list[dict[str, Any]]:
    p = _path(data_dir)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    with _LOCK, p.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一則說明 (symbol + record_id) 重複按只算最後一次; 沒帶 record_id 的每筆都算。"""
    out: list[dict[str, Any]] = []
    index: dict[tuple[str, str], int] = {}
    for row in rows:
        rid = row.get("record_id")
        if not rid:
            out.append(row)
            continue
        key = (str(row.get("symbol") or ""), str(rid))
        if key in index:
            out[index[key]] = row
        else:
            index[key] = len(out)
            out.append(row)
    return out


def summary(data_dir: Path | None = None, limit_recent: int = 20) -> dict[str, Any]:
    """整體與各模型的準／不準計數, 以及最近幾筆回饋。"""
    rows = _dedupe(_read(data_dir))
    by_model: dict[str, dict[str, int]] = defaultdict(lambda: {"helpful": 0, "not_helpful": 0})
    total = {"helpful": 0, "not_helpful": 0}
    for row in rows:
        key = "helpful" if row.get("helpful") else "not_helpful"
        total[key] += 1
        by_model[str(row.get("model") or "unknown")][key] += 1
    models = []
    for model, counts in sorted(by_model.items(), key=lambda kv: -(kv[1]["helpful"] + kv[1]["not_helpful"])):
        n = counts["helpful"] + counts["not_helpful"]
        models.append({
            "model": model,
            "helpful": counts["helpful"],
            "not_helpful": counts["not_helpful"],
            "helpful_ratio": round(counts["helpful"] / n, 3) if n else None,
        })
    n_total = total["helpful"] + total["not_helpful"]
    return {
        "total": n_total,
        "helpful": total["helpful"],
        "not_helpful": total["not_helpful"],
        "helpful_ratio": round(total["helpful"] / n_total, 3) if n_total else None,
        "models": models,
        "recent": rows[-limit_recent:][::-1],
    }

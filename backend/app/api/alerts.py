"""Alert history API: query, mark read, and delete stored events."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.services import alert_store

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


@router.get("")
def list_alerts(
    request: Request,
    days: int = 7,
    limit: int = 5000,
    source: str | None = None,
    type: str | None = None,
    ext_columns: str | None = None,
):
    """查詢觸發記錄 (時間倒序)。

    ext_columns: 逗號分隔的 "configId.fieldName", 傳入後按 symbol 富化行業/概念等 ext 欄位,
    每條記錄附帶 {configId}__{fieldName} 鍵 (與 watchlist/screener 一致)。
    """
    events = alert_store.list_recent(
        _data_dir(request), days=days, limit=limit, source=source, type=type,
    )
    total = alert_store.count(_data_dir(request))
    return {"alerts": events, "total": total}


@router.delete("")
def clear_alerts(request: Request):
    """清空全部觸發記錄。"""
    n = alert_store.clear(_data_dir(request))
    return {"ok": True, "cleared": n}


@router.patch("/read")
def mark_all_alerts_read(request: Request):
    """Mark every stored alert as read."""
    try:
        updated = alert_store.update_read(_data_dir(request), read=True)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="提醒讀取狀態儲存失敗") from exc
    return {"ok": True, "updated": updated}


@router.patch("/{alert_id}/read")
def mark_alert_read(alert_id: str, request: Request):
    """Mark one stored alert as read."""
    try:
        updated = alert_store.update_read(_data_dir(request), alert_id, read=True)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="提醒讀取狀態儲存失敗") from exc
    if updated == 0:
        existing = any(event.get("alert_id") == alert_id for event in alert_store.list_recent(_data_dir(request)))
        if not existing:
            raise HTTPException(status_code=404, detail="記錄不存在")
    return {"ok": True}


@router.delete("/id/{alert_id}")
def delete_alert_by_id(alert_id: str, request: Request):
    """Delete one alert by stable ID."""
    try:
        deleted = alert_store.delete_by_id(_data_dir(request), alert_id)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="提醒刪除儲存失敗") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="記錄不存在")
    return {"ok": True}

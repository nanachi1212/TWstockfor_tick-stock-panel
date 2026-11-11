"""API 路由 — Phase 0 僅 /health 與 /api/capabilities。"""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.tickflow.policy import detect_capabilities, tier_label

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        # 台股官方模式
        "mode": "official",
    }


@router.get("/api/capabilities")
def capabilities() -> dict:
    """前端用來決定哪些功能可用、哪些灰顯。"""
    capset = detect_capabilities()
    return {
        "label": tier_label(),
        "capabilities": capset.to_dict(),
    }


@router.post("/api/capabilities/redetect")
def redetect() -> dict:
    """使用者在設定頁"重新檢測"按鈕。"""
    capset = detect_capabilities(force=True)
    return {
        "label": tier_label(),
        "capabilities": capset.to_dict(),
    }

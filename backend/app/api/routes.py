"""API 路由 — Phase 0 僅 /health 與 /api/capabilities。"""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.capabilities import detect_capabilities

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
        "label": "",
        "capabilities": capset.to_dict(),
    }

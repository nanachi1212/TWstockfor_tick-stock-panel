"""API 路由 — Phase 0 僅 /health 與 /api/capabilities。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import __version__
from app.api.auth import _is_loopback_request
from app.backend_identity import (
    DATA_CHECK_HEADER,
    after_close_ready,
    data_root_fingerprint,
)
from app.capabilities import detect_capabilities

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    result = {
        "status": "ok",
        "version": __version__,
        # 台股官方模式
        "mode": "official",
    }
    # Keep the public health response unchanged and never return a data path or its hash.
    supplied = request.headers.get(DATA_CHECK_HEADER)
    store = getattr(request.app.state, "datastore", None)
    if supplied and store is not None and _is_loopback_request(request):
        result["same_data_root"] = supplied == data_root_fingerprint(store.data_dir)
        result["after_close_ready"] = after_close_ready(request.app.state)
    return result


@router.get("/api/capabilities")
def capabilities() -> dict:
    """前端用來決定哪些功能可用、哪些灰顯。"""
    capset = detect_capabilities()
    return {
        "label": "",
        "capabilities": capset.to_dict(),
    }

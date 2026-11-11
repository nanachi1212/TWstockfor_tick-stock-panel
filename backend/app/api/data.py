"""Project version API (the old local-data profile endpoints had no caller and are gone)."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/version")
def get_version() -> dict:
    """Return the current project version defined by VERSION."""
    from app import __version__

    return {"version": f"v{__version__}"}

"""Canonical Taiwan data root (Phase 8B-5.0.6).

A single, tiny helper so every Taiwan storage module (TaiwanDailyStore,
TaiwanSecurityMaster, TaiwanMonitorEngine's rule file, ...) resolves its
default path the same way the rest of the app already does for A-share data
— anchored to ``app.config.settings.data_dir`` (itself anchored to the
project root in dev mode, or the exe-adjacent directory in a packaged
desktop build; see ``app/config.py::_user_data_root``), never to the
process's current working directory.

Root cause this fixes: several Taiwan modules previously defaulted to a
*bare* relative ``Path("data/taiwan/...")``. A bare relative ``Path`` is not
resolved until something actually touches the filesystem with it, and at
that point Python resolves it against whatever the process's CWD happens to
be — which differs between "run from the project root", "run from
backend/", and a packaged desktop build. This was directly observed in
Phase 8B-5.0.5: the same bare-default ``TaiwanDailyStore()`` wrote its
output under ``backend/data/taiwan/daily`` instead of the project's real
``data/taiwan/daily``, depending only on which directory the process had
been launched from.

This module intentionally does NOT introduce a second data-root concept
(no ``TAIWAN_DATA_ROOT`` / ``GLOBAL_DATA_ROOT`` env var, no new settings
field) — it is a one-line reuse of the existing ``settings.data_dir``.
"""
from __future__ import annotations

from pathlib import Path


def taiwan_data_root() -> Path:
    """Return the canonical, project-root-anchored Taiwan data directory.

    Always ``<settings.data_dir>/taiwan`` — the same absolute root every
    other market's data lives under (``settings.data_dir`` already holds
    A-share's ``instruments/``, ``kline_daily/``, etc. as siblings).
    """
    from app.config import settings

    return settings.data_dir / "taiwan"

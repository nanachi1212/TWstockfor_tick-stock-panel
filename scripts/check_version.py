#!/usr/bin/env python3
"""Verify the release version and required package metadata mirrors."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

if not re.fullmatch(r"v\d+\.\d+\.\d+", VERSION):
    raise SystemExit(f"VERSION must use vMAJOR.MINOR.PATCH, got {VERSION!r}")

expected = VERSION.removeprefix("v")
frontend = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
backend = tomllib.loads(
    (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]
lock = tomllib.loads((ROOT / "backend" / "uv.lock").read_text(encoding="utf-8"))
locked = next(
    package["version"]
    for package in lock["package"]
    if package["name"] == "tickflow-stock-panel-backend"
)

mirrors = {
    "frontend/package.json": frontend,
    "backend/pyproject.toml": backend,
    "backend/uv.lock": locked,
}
mismatches = {path: value for path, value in mirrors.items() if value != expected}
if mismatches:
    details = ", ".join(f"{path}={value}" for path, value in mismatches.items())
    raise SystemExit(f"Version mirrors do not match VERSION ({expected}): {details}")

print(VERSION)

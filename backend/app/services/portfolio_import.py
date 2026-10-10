"""持股匯入來源: 本機 OneDrive / Google Drive 同步資料夾的持股檔，或 Google 試算表連結。

只回傳檔案文字，解析與寫入仍由前端既有 Portfolio 流程處理 (持股存在瀏覽器)。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import httpx

ALLOWED_SUFFIXES = {".csv", ".tsv", ".txt"}
NAME_HINT = re.compile(r"持股|庫存|投資組合|持有|portfolio|holding", re.IGNORECASE)
MAX_DEPTH = 5
MAX_FILES = 30
MAX_BYTES = 2_000_000
_SHEET_ID = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_GID = re.compile(r"[#&?]gid=(\d+)")


def cloud_roots() -> dict[str, list[Path]]:
    onedrive = {os.environ.get(k) for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")}
    gdrive: set[str] = set()
    home = Path.home()
    for candidate in (home / "Google Drive", home / "My Drive", home / "我的雲端硬碟"):
        gdrive.add(str(candidate))
    if os.name == "nt":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            for name in ("My Drive", "我的雲端硬碟"):
                gdrive.add(f"{letter}:\\{name}")
    return {
        "onedrive": [Path(p) for p in onedrive if p and Path(p).is_dir()],
        "gdrive": [Path(p) for p in gdrive if Path(p).is_dir()],
    }


def list_files(source: str) -> list[dict]:
    roots = cloud_roots().get(source, [])
    found: list[Path] = []
    for root in roots:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [d for d in dirnames if not d.startswith(".")] if depth < MAX_DEPTH else []
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() in ALLOWED_SUFFIXES and NAME_HINT.search(name):
                    found.append(path)
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"path": str(p), "name": p.name, "folder": str(p.parent), "modified": p.stat().st_mtime}
        for p in found[:MAX_FILES]
    ]


def read_file(path: str) -> str:
    """Only files that list_files() would offer may be read (no arbitrary local reads)."""
    target = Path(path).resolve()
    roots = [r.resolve() for rs in cloud_roots().values() for r in rs]
    if (
        target.suffix.lower() not in ALLOWED_SUFFIXES
        or not NAME_HINT.search(target.name)
        or not any(target.is_relative_to(root) for root in roots)
        or not target.is_file()
    ):
        raise PermissionError("只能讀取 OneDrive / Google Drive 同步資料夾內的持股檔")
    if target.stat().st_size > MAX_BYTES:
        raise ValueError("檔案太大")
    raw = target.read_bytes()
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("無法辨識檔案編碼，請另存為 UTF-8 CSV")


def fetch_google_sheet(url: str) -> str:
    match = _SHEET_ID.match(url.strip())
    if not match:
        raise ValueError("請貼上 Google 試算表網址 (https://docs.google.com/spreadsheets/d/...)")
    gid = _GID.search(url)
    export = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv"
    if gid:
        export += f"&gid={gid.group(1)}"
    try:
        resp = httpx.get(export, timeout=20, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ValueError("無法連線到 Google 試算表，請稍後再試") from exc
    if resp.status_code in (401, 403) or "accounts.google.com" in str(resp.url):
        raise PermissionError(
            "這份試算表是私人的，無法直接讀取。請在試算表選「檔案 → 下載 → 逗號分隔值 (.csv)」，再用「本機檔案」匯入。"
        )
    if resp.status_code != 200:
        raise ValueError(f"Google 試算表回應 {resp.status_code}，請確認網址")
    return resp.content.decode("utf-8-sig")

"""備份與轉移 API — 家裡 / 辦公室電腦之間搬移使用者設定與私人資料。

密碼只在記憶體中用於衍生金鑰, 不寫入檔案、不記錄 log。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, SecretStr
from starlette.concurrency import run_in_threadpool

from app.services import device_transfer as dt

router = APIRouter(prefix="/api/device-transfer", tags=["device-transfer"])


class ExportRequest(BaseModel):
    preset: str | None = None
    categories: list[str] | None = None
    include_secrets: bool = False
    password: SecretStr | None = None
    browser_storage: dict[str, dict[str, Any]] = Field(default_factory=dict)


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(dt.MAX_ARCHIVE_BYTES + 1)
    if len(data) > dt.MAX_ARCHIVE_BYTES:
        raise HTTPException(413, "備份檔過大")
    return data


@router.get("/options")
def get_options() -> dict[str, Any]:
    return dt.options()


@router.post("/export")
def export_backup(req: ExportRequest) -> Response:
    try:
        categories = dt.resolve_categories(req.preset, req.categories)
        blob = dt.build_backup(
            categories,
            browser_storage=req.browser_storage,
            include_secrets=req.include_secrets,
            password=req.password.get_secret_value() if req.password else None,
        )
    except dt.TransferError as e:
        raise HTTPException(400, str(e)) from e
    name = f"twstock-{datetime.now():%Y%m%d-%H%M}{dt.FILE_EXTENSION}"
    return Response(
        blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/preview")
async def preview_backup(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    try:
        return await run_in_threadpool(dt.preview, await _read_upload(file))
    except dt.TransferError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/restore")
async def restore_backup(
    file: Annotated[UploadFile, File()],
    categories: Annotated[str, Form()],
    password: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    try:
        return await run_in_threadpool(
            dt.restore,
            await _read_upload(file),
            [c for c in categories.split(",") if c],
            password=password or None,
        )
    except dt.TransferError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/rollback/{restore_point_id}")
def rollback_restore(restore_point_id: str) -> dict[str, Any]:
    try:
        return dt.rollback(restore_point_id)
    except dt.TransferError as e:
        raise HTTPException(400, str(e)) from e

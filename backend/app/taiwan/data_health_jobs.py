"""Bounded single-flight jobs that call existing updaters, never copy their logic."""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel

from app.taiwan.data_health_center import (
    DATASETS,
    HealthAction,
    HealthReport,
    get_health_snapshot,
    safe_reason,
)
from app.taiwan.realtime.calendar import taipei_now

JobStatus = Literal["queued", "running", "completed", "partial", "failed"]
DAILY = {"daily", "institutional", "margin"}
SOCIAL = {"ptt", "social_ai"}


class HealthJob(BaseModel):
    job_id: str
    dataset: str
    action: HealthAction
    affected_datasets: list[str]
    status: JobStatus = "queued"
    queued_at: str
    started_at: str | None = None
    finished_at: str | None = None
    reason: str | None = None


def _ai_revision() -> str:
    from app.services.ai_provider import snapshot_ai_provider_config

    cfg = snapshot_ai_provider_config()
    # Ephemeral equality token only; never serialize credentials or this digest.
    return hashlib.sha256(
        str((cfg.provider, cfg.model, cfg.base_url, cfg.api_key, cfg.codex_command)).encode()
    ).hexdigest()


def execute_existing(dataset: str, action: HealthAction) -> tuple[JobStatus, str]:
    if dataset == "ai_provider":
        from app.services.ai_provider import (
            codex_cli_available,
            is_codex_cli_provider,
            probe_openai_profile_connection,
            snapshot_ai_provider_config,
        )

        cfg = snapshot_ai_provider_config()
        if is_codex_cli_provider(cfg.provider):
            ok = bool(cfg.model) and codex_cli_available()
            return (
                ("completed", "Codex CLI 可用；此驗證不代表研究請求已成功")
                if ok
                else ("failed", "Codex CLI 不可用或模型未設定")
            )
        response = asyncio.run(probe_openai_profile_connection(cfg))
        return (
            ("completed", "AI Provider/Profile 連線驗證成功")
            if response.get("ok")
            else (
                "failed",
                safe_reason(response.get("error_code"), "AI Provider/Profile 連線驗證失敗"),
            )
        )
    if action == "validate":
        row = next(r for r in get_health_snapshot().datasets if r.id == dataset)
        return ("failed" if row.status == "error" else "completed", row.reason)
    if dataset in DAILY:
        from app.taiwan.daily_update import TaiwanDailyUpdateService

        result = TaiwanDailyUpdateService().run_update()
        if result.overall_status == "failed":
            return "failed", "官方更新失敗，未取得有效資料；原有資料保留"
        rows = [r for r in get_health_snapshot().datasets if r.id in DAILY]
        if result.overall_status == "partial" or any(r.status != "current" for r in rows):
            return "partial", "更新已結束，部分交易日或交易所資料仍缺漏；原有資料保留"
        return "completed", "既有日資料、法人與融資融券更新完成"
    if dataset in SOCIAL:
        from app.taiwan.social_sentiment_jobs import get_social_sentiment_job_manager

        manager = get_social_sentiment_job_manager()
        started = manager.start_manual()
        if not started.get("job_id"):
            return "failed", "目前已有社群更新執行中，請等待既有任務完成"
        while True:
            job = manager.get_job(started["job_id"])
            if job and job["status"] not in {"queued", "running"}:
                status: JobStatus = (
                    "completed"
                    if job["status"] == "completed"
                    else "partial"
                    if job["status"] == "partial"
                    else "failed"
                )
                return (
                    status,
                    "既有社群更新完成"
                    if status == "completed"
                    else "社群來源或 AI 仍有缺漏，請查看各來源原因",
                )
            time.sleep(0.5)
    raise ValueError("unsafe_action")


class HealthJobManager:
    def __init__(
        self,
        *,
        runner: Callable[[str, HealthAction], tuple[JobStatus, str]] = execute_existing,
        reader: Callable[[], HealthReport] = get_health_snapshot,
    ) -> None:
        self._runner, self._reader = runner, reader
        self._lock = threading.Lock()
        self._jobs: dict[str, HealthJob] = {}
        self._active: dict[str, str] = {}
        self._ai_proof: tuple[str, JobStatus, str, str] | None = None

    @staticmethod
    def _group(dataset: str) -> str:
        return "daily" if dataset in DAILY else "social" if dataset in SOCIAL else dataset

    def start(self, dataset: str, action: HealthAction) -> HealthJob:
        if dataset not in DATASETS:
            raise KeyError(dataset)
        # Check duplicate before metadata IO and again under the insertion lock.
        group = self._group(dataset)
        with self._lock:
            if group in self._active:
                return self._jobs[self._active[group]].model_copy(deep=True)
        row = next(r for r in self._reader().datasets if r.id == dataset)
        if action not in row.actions:
            raise ValueError("此資料集沒有安全的自動修復入口")
        affected = sorted(
            DAILY
            if dataset in DAILY and action != "validate"
            else SOCIAL | {"dcard"}
            if dataset in SOCIAL and action != "validate"
            else {dataset}
        )
        with self._lock:
            if group in self._active:
                return self._jobs[self._active[group]].model_copy(deep=True)
            # Keep only bounded recent records; never evict active jobs.
            for job_id in list(self._jobs):
                if len(self._jobs) < 60:
                    break
                if self._jobs[job_id].status not in {"queued", "running"}:
                    del self._jobs[job_id]
            job = HealthJob(
                job_id=uuid.uuid4().hex,
                dataset=dataset,
                action=action,
                affected_datasets=affected,
                queued_at=taipei_now().isoformat(),
            )
            self._jobs[job.job_id] = job
            self._active[group] = job.job_id
            queued = job.model_copy(deep=True)
        try:
            threading.Thread(
                target=self._run, args=(job.job_id, group), daemon=True, name=f"health-{group}"
            ).start()
        except Exception:
            self._finish(job.job_id, group, "failed", "無法啟動背景任務")
        return queued

    def _run(self, job_id: str, group: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status, job.started_at = "running", taipei_now().isoformat()
            dataset, action = job.dataset, job.action
        try:
            revision = _ai_revision() if dataset == "ai_provider" else None
            status, reason = self._runner(dataset, action)
            if revision is not None and revision == _ai_revision():
                with self._lock:
                    self._ai_proof = (revision, status, reason, taipei_now().isoformat())
        except Exception:
            status, reason = "failed", "既有更新器執行失敗，原有資料保留；請重試或檢查來源設定"
        self._finish(job_id, group, status, reason)

    def _finish(self, job_id: str, group: str, status: JobStatus, reason: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status, job.reason, job.finished_at = status, reason, taipei_now().isoformat()
            self._active.pop(group, None)

    def get(self, job_id: str) -> HealthJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list_jobs(self) -> list[HealthJob]:
        with self._lock:
            return [j.model_copy(deep=True) for j in reversed(list(self._jobs.values()))]

    def snapshot(self) -> HealthReport:
        report = self._reader()
        jobs = self.list_jobs()
        with self._lock:
            proof = deepcopy(self._ai_proof)
        for row in report.datasets:
            latest = next((j for j in jobs if row.id in j.affected_datasets), None)
            if row.id == "ai_provider" and proof and proof[0] == _ai_revision():
                row.status = "current" if proof[1] == "completed" else "error"
                row.reason, row.last_attempt = proof[2], proof[3]
                row.freshness = "本次設定已驗證；驗證記錄於服務重啟後清除"
                if proof[1] == "completed":
                    row.last_success = proof[3]
            if latest:
                row.last_attempt = latest.started_at or latest.queued_at
                if latest.status in {"queued", "running"}:
                    row.status = "updating"
                elif latest.status == "failed" and row.id not in {"dcard", "ai_provider"}:
                    row.status, row.reason = "error", latest.reason or "更新失敗"
                elif latest.status == "partial" and row.id == latest.dataset:
                    row.reason = f"{row.reason}。{latest.reason}"
                if (
                    latest.status == "completed"
                    and latest.action != "validate"
                    and row.status == "current"
                ):
                    row.last_success = latest.finished_at
        report.current_count = sum(r.status == "current" for r in report.datasets)
        return report


_manager = HealthJobManager()


def get_health_job_manager() -> HealthJobManager:
    return _manager

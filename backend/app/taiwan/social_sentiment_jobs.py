"""In-process job registry for manual Social Sentiment runs.

The collector single-flight guarantee itself is cross-process and lives in
``social_sentiment.py`` so Task Scheduler and this API manager share it.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.social_sentiment import (
    SocialSentimentRunLock,
    SocialSentimentService,
)

logger = logging.getLogger(__name__)


class SocialSentimentJobManager:
    def __init__(
        self,
        *,
        service_factory: Callable[[], SocialSentimentService] = SocialSentimentService,
        output_dir: Path | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._output_dir = Path(output_dir or settings.data_dir / "social_sentiment")
        self._guard = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._payloads: dict[str, dict[str, Any]] = {}

    def start_manual(self) -> dict[str, Any]:
        run_lock = SocialSentimentRunLock(self._output_dir / "run.lock")
        if not run_lock.acquire():
            return {"job_id": None, "status": "already_running"}

        job_id = uuid.uuid4().hex[:12]
        with self._guard:
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "started_at": None,
                "finished_at": None,
                "ptt_status": "not_queried",
                "dcard_status": "not_queried",
                "ai_status": "not_queried",
                "ai_skipped_symbols": 0,
                "posts": 0,
                "comments": 0,
                "symbols_identified": 0,
                "error_summary": None,
                "snapshot_id": None,
            }
        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, run_lock),
            daemon=True,
            name=f"social-sentiment-{job_id[:8]}",
        )
        try:
            thread.start()
        except Exception:
            run_lock.release()
            with self._guard:
                self._jobs.pop(job_id, None)
            raise
        return {"job_id": job_id, "status": "running"}

    def _run_job(self, job_id: str, run_lock: SocialSentimentRunLock) -> None:
        self._update(job_id, status="running", started_at=datetime.now(TAIPEI).isoformat())
        try:
            payload = self._service_factory().run(trigger="manual", run_lock=run_lock)
            status = "failed" if payload.get("status") == "unavailable" else payload.get("status", "completed")
            if status == "available":
                status = "completed"
            sources = payload.get("sources", {})
            errors = [
                f"{source}: {error}"
                for source, detail in sources.items()
                if isinstance(detail, dict)
                for error in detail.get("errors", [])[:3]
            ]
            errors.extend(str(error) for error in payload.get("ai", {}).get("errors", [])[:3])
            with self._guard:
                self._payloads[job_id] = payload
            self._update(
                job_id,
                status=status,
                started_at=payload.get("started_at"),
                finished_at=payload.get("finished_at"),
                ptt_status=sources.get("ptt", {}).get("status", "unavailable"),
                dcard_status=sources.get("dcard", {}).get("status", "unavailable"),
                ai_status=payload.get("ai", {}).get("status", "unavailable"),
                ai_skipped_symbols=int(payload.get("ai", {}).get("skipped_symbols", 0)),
                posts=sum(int(detail.get("posts", 0)) for detail in sources.values() if isinstance(detail, dict)),
                comments=sum(int(detail.get("comments", 0)) for detail in sources.values() if isinstance(detail, dict)),
                symbols_identified=int(payload.get("identified_symbols", 0)),
                error_summary="; ".join(errors)[:1000] or None,
                snapshot_id=payload.get("snapshot_id"),
            )
        except Exception as exc:
            run_lock.release()
            logger.exception("manual social sentiment job failed: %s", job_id)
            self._update(
                job_id,
                status="failed",
                finished_at=datetime.now(TAIPEI).isoformat(),
                error_summary=type(exc).__name__,
            )

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._guard:
            if job_id in self._jobs:
                self._jobs[job_id].update(changes)

    def get_job(
        self,
        job_id: str,
        *,
        source: str | None = None,
        symbol: str | None = None,
        keyword: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any] | None:
        with self._guard:
            state = deepcopy(self._jobs.get(job_id))
            payload = deepcopy(self._payloads.get(job_id))
        if state is None:
            return None

        rankings = payload.get("rankings", []) if payload else []
        discussions = payload.get("discussions", []) if payload else []
        if source:
            discussions = [item for item in discussions if item.get("source") == source]
        if symbol:
            normalized = symbol.upper()
            discussions = [
                item
                for item in discussions
                if normalized in {str(value).upper() for value in item.get("symbols", [])}
                or normalized in {str(value).split(".", 1)[0].upper() for value in item.get("symbols", [])}
            ]
        if keyword:
            needle = keyword.casefold()
            discussions = [item for item in discussions if needle in _discussion_search_text(item)]
        page = discussions[offset : offset + limit]
        state["rankings"] = rankings
        state["discussions"] = {
            "items": page,
            "total": len(discussions),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(page) < len(discussions),
        }
        return state


def _discussion_search_text(item: dict[str, Any]) -> str:
    values = [
        item.get("title"),
        item.get("excerpt"),
        *item.get("representative_comments", []),
        *item.get("stock_names", []),
        *item.get("symbols", []),
    ]
    return " ".join(str(value) for value in values if value).casefold()


_manager: SocialSentimentJobManager | None = None
_manager_guard = threading.Lock()


def get_social_sentiment_job_manager() -> SocialSentimentJobManager:
    global _manager
    with _manager_guard:
        if _manager is None:
            _manager = SocialSentimentJobManager()
        return _manager

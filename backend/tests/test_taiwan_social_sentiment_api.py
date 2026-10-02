from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import taiwan

app = FastAPI()
app.include_router(taiwan.router)
client = TestClient(app, client=("127.0.0.1", 50000))


def test_social_sentiment_snapshot_query(monkeypatch):
    payload = {
        "as_of": "2026-09-29",
        "snapshot_slot": "after_close",
        "rankings": [],
    }
    monkeypatch.setattr(
        taiwan,
        "load_social_sentiment_snapshot",
        lambda target_date, slot: payload
        if (target_date.isoformat(), slot) == ("2026-09-29", "after_close")
        else None,
    )

    response = client.get(
        "/api/taiwan/social-sentiment",
        params={"target_date": "2026-09-29", "snapshot_slot": "after_close"},
    )

    assert response.status_code == 200
    assert response.json() == payload


def test_social_sentiment_rejects_invalid_snapshot_slot():
    response = client.get(
        "/api/taiwan/social-sentiment",
        params={"target_date": "2026-09-29", "snapshot_slot": "midday"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "無效的快照時段: midday"


def test_social_sentiment_history_is_read_only_metadata(monkeypatch):
    items = [
        {
            "as_of": "2026-09-29",
            "generated_at": "2026-09-29T15:30:00+08:00",
            "snapshot_slot": "after_close",
            "status": "partial",
            "identified_symbols": 65,
        }
    ]
    monkeypatch.setattr(taiwan, "list_social_sentiment_history", lambda limit: items if limit == 7 else [])

    response = client.get("/api/taiwan/social-sentiment/history", params={"limit": 7})

    assert response.status_code == 200
    assert response.json() == {"items": items}


class _FakeManualJobManager:
    def __init__(self, start_result=None, job_result=None):
        self.start_result = start_result
        self.job_result = job_result
        self.filters = None

    def start_manual(self):
        return self.start_result

    def get_job(self, job_id, *, source=None, symbol=None, keyword=None, offset=0, limit=50):
        self.filters = (job_id, source, symbol, keyword, offset, limit)
        return self.job_result


def test_manual_social_sentiment_trigger_returns_running_job(monkeypatch):
    manager = _FakeManualJobManager(start_result={"job_id": "manual-1", "status": "running"})
    monkeypatch.setattr(taiwan, "get_social_sentiment_job_manager", lambda: manager)

    response = client.post("/api/taiwan/social-sentiment/run", json={"mode": "manual"})

    assert response.status_code == 200
    assert response.json() == {"job_id": "manual-1", "status": "running"}


def test_manual_social_sentiment_trigger_reports_already_running(monkeypatch):
    manager = _FakeManualJobManager(start_result={"job_id": None, "status": "already_running"})
    monkeypatch.setattr(taiwan, "get_social_sentiment_job_manager", lambda: manager)

    response = client.post("/api/taiwan/social-sentiment/run", json={"mode": "manual"})

    assert response.status_code == 200
    assert response.json()["status"] == "already_running"


def test_manual_job_query_supports_bounded_discussion_filters(monkeypatch):
    job = {
        "job_id": "manual-1",
        "status": "partial",
        "started_at": "2026-09-30T09:38:12+08:00",
        "finished_at": "2026-09-30T09:42:00+08:00",
        "ptt_status": "available",
        "dcard_status": "unavailable",
        "ai_status": "degraded",
        "posts": 41,
        "comments": 6875,
        "symbols_identified": 226,
        "error_summary": "Dcard unavailable",
        "rankings": [],
        "discussions": {"items": [], "total": 0, "offset": 50, "limit": 50, "has_more": False},
    }
    manager = _FakeManualJobManager(job_result=job)
    monkeypatch.setattr(taiwan, "get_social_sentiment_job_manager", lambda: manager)

    response = client.get(
        "/api/taiwan/social-sentiment/jobs/manual-1",
        params={"source": "ptt", "symbol": "2330.TWSE", "q": "台積電", "offset": 50, "limit": 50},
    )

    assert response.status_code == 200
    assert response.json() == job
    assert manager.filters == ("manual-1", "ptt", "2330.TWSE", "台積電", 50, 50)


def test_manual_job_query_does_not_expose_unknown_job(monkeypatch):
    manager = _FakeManualJobManager(job_result=None)
    monkeypatch.setattr(taiwan, "get_social_sentiment_job_manager", lambda: manager)

    response = client.get("/api/taiwan/social-sentiment/jobs/missing")

    assert response.status_code == 404

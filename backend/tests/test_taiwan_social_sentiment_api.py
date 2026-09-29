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

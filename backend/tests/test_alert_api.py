from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.alerts import delete_alert_by_id, mark_alert_read, mark_all_alerts_read
from app.services import alert_store


def _request(data_dir):
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


@pytest.mark.parametrize("handler,args", [
    (mark_all_alerts_read, ()),
    (mark_alert_read, ("alert-1",)),
    (delete_alert_by_id, ("alert-1",)),
])
def test_alert_storage_failure_is_reported_to_client(tmp_path, monkeypatch, handler, args):
    def fail_storage(*_args, **_kwargs):
        raise OSError("alerts file is unavailable")

    monkeypatch.setattr(alert_store, "delete_by_id", fail_storage)
    monkeypatch.setattr(alert_store, "update_read", fail_storage)

    with pytest.raises(HTTPException) as exc_info:
        handler(*args, _request(tmp_path))

    assert exc_info.value.status_code == 503
    assert "儲存失敗" in exc_info.value.detail


def test_demo_seed_is_removed_and_taiwan_alert_routes_still_work(tmp_path):
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.alerts import router

    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    alert_store.append_many(tmp_path, [{
        "ts": int(time.time() * 1000), "symbol": "2330.TWSE", "source": "price",
        "type": "price_above", "message": "Taiwan price alert",
    }])
    path = tmp_path / "user_data" / "alerts.jsonl"
    before = path.read_bytes()

    with TestClient(app) as client:
        assert client.post("/api/alerts/seed").status_code in {404, 405}
        assert path.read_bytes() == before
        response = client.get("/api/alerts", params={"days": 999999})
        assert response.status_code == 200
        event, = response.json()["alerts"]
        assert event["symbol"] == "2330.TWSE"
        assert client.patch("/api/alerts/read").status_code == 200
        assert client.patch(f"/api/alerts/{event['alert_id']}/read").status_code == 200
        assert client.delete(f"/api/alerts/id/{event['alert_id']}").status_code == 200
        assert client.delete("/api/alerts").json() == {"ok": True, "cleared": 0}

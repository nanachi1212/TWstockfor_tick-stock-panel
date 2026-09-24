from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.alerts import mark_alert_read, mark_all_alerts_read
from app.services import alert_store


def _request(data_dir):
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


@pytest.mark.parametrize("handler,args", [
    (mark_all_alerts_read, ()),
    (mark_alert_read, ("alert-1",)),
])
def test_read_status_write_failure_is_reported_to_client(tmp_path, monkeypatch, handler, args):
    def fail_update(*_args, **_kwargs):
        raise OSError("alerts file is unavailable")

    monkeypatch.setattr(alert_store, "update_read", fail_update)

    with pytest.raises(HTTPException) as exc_info:
        handler(*args, _request(tmp_path))

    assert exc_info.value.status_code == 503
    assert "儲存失敗" in exc_info.value.detail

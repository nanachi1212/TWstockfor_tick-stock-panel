from __future__ import annotations

import json
import threading

from app import secrets_store
from app.config import settings


def test_concurrent_saves_keep_every_key_and_leave_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    threads = [
        threading.Thread(target=secrets_store.save, args=({f"key_{i}": f"value_{i}"},))
        for i in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    path = tmp_path / "user_data" / "secrets.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {f"key_{i}": f"value_{i}" for i in range(20)}
    assert not list(path.parent.glob("*.tmp"))

    secrets_store.clear("key_0")
    assert "key_0" not in json.loads(path.read_text(encoding="utf-8"))
    assert not list(path.parent.glob("*.tmp"))

from __future__ import annotations

import json
import threading

import pytest

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


@pytest.mark.parametrize("original", [b'{"other": "fixture",', b'[]'])
@pytest.mark.parametrize("operation", ["save", "clear_one"])
def test_damaged_settings_preserved_during_updates(tmp_path, monkeypatch, original, operation):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    target = tmp_path / "user_data" / "secrets.json"
    target.parent.mkdir()
    target.write_bytes(original)
    with pytest.raises(ValueError):
        if operation == "save":
            secrets_store.save({"ai_model": "fixture"})
        else:
            secrets_store.clear("ai_model")
    assert target.read_bytes() == original

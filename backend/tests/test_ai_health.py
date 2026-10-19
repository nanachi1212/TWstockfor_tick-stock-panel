from unittest.mock import patch

import httpx

from app.services import ai_health
from app.services.ai_provider import AIProviderConfigSnapshot


def _cfg(base_url: str) -> AIProviderConfigSnapshot:
    return AIProviderConfigSnapshot(provider="openai_compat", model="qwen", api_key="k", base_url=base_url)


def test_local_model_not_loaded_is_red_and_autoload_runs_lms_load():
    states = iter(["not-loaded", "loaded"])
    with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=_cfg("http://127.0.0.1:1234/v1")), \
         patch.object(ai_health, "_lmstudio_state", side_effect=lambda *_: next(states)), \
         patch.object(ai_health, "_run_lms", return_value=True) as run:
        assert ai_health.ensure_local_model_loaded() == "loaded"
    run.assert_called_once_with("load", "qwen", "--yes")

    with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=_cfg("http://127.0.0.1:1234/v1")), \
         patch.object(ai_health, "_lmstudio_state", return_value="not-loaded"):
        assert ai_health.ai_status()["status"] == "error"


def test_autoload_reloads_small_context_with_configured_length():
    cfg = AIProviderConfigSnapshot(provider="openai_compat", model="qwen", api_key="k",
                                   base_url="http://127.0.0.1:1234/v1", context_window=50000)
    states = iter(["small-context", "loaded"])
    with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=cfg), \
         patch.object(ai_health, "_lmstudio_state", side_effect=lambda *_: next(states)), \
         patch.object(ai_health, "_run_lms", return_value=True) as run:
        assert ai_health.ensure_local_model_loaded() == "loaded"
    assert [c.args for c in run.call_args_list] == [
        ("unload", "qwen"), ("load", "qwen", "--yes", "--context-length", "50000")]


def test_lmstudio_state_compares_loaded_context():
    payload = {"data": [{"id": "qwen", "state": "loaded", "loaded_context_length": 16384}]}
    resp = httpx.Response(200, json=payload)
    with patch.object(ai_health.httpx, "get", return_value=resp):
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "qwen", 50000) == "small-context"
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "qwen", 16384) == "loaded"
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "qwen") == "loaded"


def test_cloud_provider_skips_autoload():
    with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=_cfg("https://api.deepseek.com")), \
         patch.object(ai_health, "_run_lms") as run:
        assert ai_health.ensure_local_model_loaded() == "skip"
    run.assert_not_called()

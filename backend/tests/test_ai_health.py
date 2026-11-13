from unittest.mock import patch

import httpx

from app.services import ai_health
from app.services.ai_provider import AIProviderConfigSnapshot

LOADED_16K = {"id": "research-model", "state": "loaded", "loaded_context_length": 16384,
              "max_context_length": 262144}
LMS_ENTRY = {"identifier": "research-model", "modelKey": "qwen/qwen3.5-9b", "contextLength": 16384}


def _cfg(base_url: str, model: str = "qwen", context_window: int = 0) -> AIProviderConfigSnapshot:
    return AIProviderConfigSnapshot(provider="openai_compat", model=model, api_key="k",
                                    base_url=base_url, context_window=context_window)


def _autoload(cfg, item, *, lms_entry=None, run_results=None, final="loaded"):
    state = "loaded" if item.get("state") == "loaded" else "not-loaded"
    with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=cfg), \
         patch.object(ai_health, "_lmstudio_model", return_value=(state, item)), \
         patch.object(ai_health, "_lmstudio_state", return_value=final), \
         patch.object(ai_health, "_lms_loaded", return_value=lms_entry), \
         patch.object(ai_health, "_run_lms", side_effect=run_results or (lambda *_: True)) as run:
        result = ai_health.ensure_local_model_loaded()
    return result, [c.args for c in run.call_args_list]


def test_local_model_not_loaded_is_red_and_autoload_runs_lms_load():
    result, calls = _autoload(_cfg("http://127.0.0.1:1234/v1"), {"id": "qwen", "state": "not-loaded"})
    assert result == "loaded"
    assert calls == [("load", "qwen", "--yes")]

    with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=_cfg("http://127.0.0.1:1234/v1")), \
         patch.object(ai_health, "_lmstudio_state", return_value="not-loaded"):
        assert ai_health.ai_status()["status"] == "error"


def test_remote_ai_status_only_counts_reachable_and_not_throttled_as_ok():
    cfg = _cfg("https://api.example.com/v1")
    expected = {200: "ok", 404: "ok", 401: "error", 403: "error", 429: "error", 500: "error", 503: "error"}
    for code, status in expected.items():
        response = httpx.Response(code, request=httpx.Request("GET", "https://api.example.com/v1/models"))
        with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=cfg), \
             patch.object(ai_health.httpx, "get", return_value=response):
            assert ai_health.ai_status()["status"] == status, code


def test_autoload_reloads_alias_by_model_key_with_configured_length():
    cfg = _cfg("http://127.0.0.1:1234/v1", "research-model", 50000)
    result, calls = _autoload(cfg, LOADED_16K, lms_entry=LMS_ENTRY)
    assert result == "loaded"
    assert calls == [
        ("unload", "research-model"),
        ("load", "qwen/qwen3.5-9b", "--identifier", "research-model", "--yes", "--context-length", "50000"),
    ]


def test_failed_reload_restores_previous_context():
    cfg = _cfg("http://127.0.0.1:1234/v1", "research-model", 50000)
    results = iter([True, False, True])
    _, calls = _autoload(cfg, LOADED_16K, lms_entry=LMS_ENTRY,
                         run_results=lambda *_: next(results), final="small-context")
    assert calls[-1] == ("load", "qwen/qwen3.5-9b", "--identifier", "research-model", "--yes",
                         "--context-length", "16384")


def test_reload_skips_instance_lms_cannot_see():
    cfg = _cfg("http://127.0.0.1:5678/v1", "research-model", 50000)
    other_instance = {**LMS_ENTRY, "contextLength": 4096}
    for entry in (None, other_instance):
        _, calls = _autoload(cfg, LOADED_16K, lms_entry=entry, final="small-context")
        assert calls == []


def test_unsupported_context_never_unloads():
    cfg = _cfg("http://127.0.0.1:1234/v1", "research-model", 32768)
    small_model = {**LOADED_16K, "max_context_length": 16384}
    result, calls = _autoload(cfg, small_model, lms_entry=LMS_ENTRY, final="context-unsupported")
    assert (result, calls) == ("context-unsupported", [])

    _, calls = _autoload(cfg, {**small_model, "state": "not-loaded"}, final="context-unsupported")
    assert calls == [("load", "research-model", "--yes")]


def test_lmstudio_state_compares_loaded_and_max_context():
    resp = httpx.Response(200, json={"data": [LOADED_16K]})
    with patch.object(httpx.Client, "get", return_value=resp):
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "research-model", 50000) == "small-context"
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "research-model", 16384) == "loaded"
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "research-model") == "loaded"
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "research-model", 300000) == "context-unsupported"


def test_cloud_provider_skips_autoload():
    with patch("app.services.ai_provider.snapshot_ai_provider_config", return_value=_cfg("https://api.deepseek.com")), \
         patch.object(ai_health, "_run_lms") as run:
        assert ai_health.ensure_local_model_loaded() == "skip"
    run.assert_not_called()


def test_downloaded_quantized_model_matches_before_first_load():
    item = {"id": "research-model", "state": "not-loaded", "quantization": "Q4_K_M"}
    with patch.object(httpx.Client, "get", return_value=httpx.Response(200, json={"data": [item]})):
        assert ai_health._lmstudio_model("http://127.0.0.1:1234", "research-model@q4_k_m") == ("not-loaded", item)
        assert ai_health._lmstudio_model("http://127.0.0.1:1234", "research-model@q8_0") == ("missing", {})


def test_quantized_model_loaded_without_suffix_reloads_with_configured_context():
    item = {"id": "research-model", "state": "loaded", "quantization": "Q4_K_M",
            "loaded_context_length": 16384, "max_context_length": 262144}
    response = httpx.Response(200, json={"data": [item]}, request=httpx.Request("GET", "http://x"))
    with patch.object(httpx.Client, "get", return_value=response):
        assert ai_health._lmstudio_model("http://127.0.0.1:1234", "research-model@q4_k_m") == ("loaded", item)
        assert ai_health._lmstudio_state("http://127.0.0.1:1234", "research-model@q4_k_m", 50000) == "small-context"

    cfg = _cfg("http://127.0.0.1:1234/v1", "research-model@q4_k_m", 50000)
    _, calls = _autoload(cfg, item, lms_entry=LMS_ENTRY)
    assert calls[0] == ("unload", "research-model")
    assert calls[1][-2:] == ("--context-length", "50000")


def test_quantized_autoload_uses_exact_downloaded_file_and_preserves_identifier():
    cfg = _cfg("http://127.0.0.1:1234/v1", "research-model@q4_k_m", 50000)
    item = {"id": "research-model", "state": "not-loaded", "quantization": "Q4_K_M"}
    inventory = [{"modelKey": "research-model", "quantization": {"name": "Q4_K_M"},
                  "path": "publisher/research-model/research-model-Q4_K_M.gguf"}]
    import json
    from types import SimpleNamespace
    with patch.object(ai_health, "_lms", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(inventory))):
        result, calls = _autoload(cfg, item)
    assert result == "loaded"
    assert calls == [("load", inventory[0]["path"], "--exact", "--identifier", cfg.model,
                      "--yes", "--context-length", "50000")]


def test_quantized_autoload_does_not_guess_missing_or_ambiguous_file():
    import json
    from types import SimpleNamespace
    cfg = _cfg("http://127.0.0.1:1234/v1", "research-model@q4_k_m", 50000)
    item = {"id": "research-model", "state": "not-loaded", "quantization": "Q4_K_M"}
    entry = {"modelKey": "research-model", "quantization": {"name": "Q4_K_M"}, "path": "model.gguf"}
    for inventory in ([], [entry, {**entry, "path": "other.gguf"}]):
        with patch.object(ai_health, "_lms", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(inventory))):
            _, calls = _autoload(cfg, item, final="not-loaded")
        assert calls == []

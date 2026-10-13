"""Claude Code / Codex subscription providers: isolation, errors, and key-less profiles."""
from __future__ import annotations

import json
from unittest import mock

import pytest

from app.services import ai_provider
from app.services.ai_provider import AIProviderConfigSnapshot, generate_ai_text


def _claude(model: str = "sonnet") -> AIProviderConfigSnapshot:
    return AIProviderConfigSnapshot(provider="claude_cli", model=model, api_key="", profile_name="sub")


@pytest.mark.asyncio
async def test_claude_cli_runs_isolated_on_subscription(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    calls = []

    def fake_run(args, prompt, env, timeout, label, cwd):
        calls.append((args, prompt, env, timeout))
        return 0, json.dumps({"type": "result", "is_error": False, "result": " 多方論點 "}).encode(), b""

    with (
        mock.patch.object(ai_provider, "_claude_base_command", return_value=["claude"]),
        mock.patch.object(ai_provider, "_run_cli_process", side_effect=fake_run),
    ):
        text = await generate_ai_text(
            [{"role": "user", "content": "中文證據"}], max_tokens=None, timeout=30, config_snapshot=_claude(),
        )

    assert text == "多方論點"
    args, prompt, env, timeout = calls[0]
    for flag in ("--tools", "--setting-sources", "--strict-mcp-config", "--disable-slash-commands",
                 "--no-session-persistence"):
        assert flag in args
    assert args[args.index("--tools") + 1] == ""  # all tools off
    assert args[args.index("--model") + 1] == "sonnet"
    assert "--bare" not in args  # --bare never reads the subscription login
    assert "ANTHROPIC_API_KEY" not in env
    assert "中文證據" in prompt
    assert timeout >= 300


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "payload", "message"),
    [
        (1, {"is_error": True, "result": "Invalid API key · Please run /login"}, "重新登入"),
        (0, {"is_error": True, "result": "model overloaded"}, "model overloaded"),
        (0, {"is_error": False, "result": "  "}, "未返回內容"),
    ],
)
async def test_claude_cli_errors_are_explicit(returncode, payload, message):
    with (
        mock.patch.object(ai_provider, "_claude_base_command", return_value=["claude"]),
        mock.patch.object(ai_provider, "_run_cli_process",
                          return_value=(returncode, json.dumps(payload).encode(), b"")),
        pytest.raises(RuntimeError, match=message),
    ):
        await generate_ai_text([{"role": "user", "content": "x"}], timeout=30, config_snapshot=_claude())


def test_subscription_profiles_need_no_key_and_keep_their_model(tmp_path):
    import app.services.ai_key_profiles as mod
    from app.services.ai_provider import ai_configured, snapshot_ai_provider_config

    with (
        mock.patch.object(mod, "_profiles_path", lambda: tmp_path / "p.json"),
        mock.patch.object(mod, "_secrets_path", lambda: tmp_path / "s.json"),
    ):
        with pytest.raises(ValueError, match="API key"):
            mod.create_profile(name="api", provider="openai_compat", api_key="")
        codex = mod.create_profile(name="gpt", provider="codex_cli", api_key="", model="gpt-5.5")
        claude = mod.create_profile(name="claude", provider="claude_cli", api_key="", model="opus")
        assert (codex["has_key"], claude["provider"]) == (False, "claude_cli")

        cfg = snapshot_ai_provider_config()
        assert (cfg.provider, cfg.model) == ("codex_cli", "gpt-5.5")  # profile model, not legacy ai_codex_model

        mod.set_active_profile(claude["id"])
        with mock.patch.object(ai_provider, "claude_cli_available", return_value=True):
            assert ai_configured() is True
        assert snapshot_ai_provider_config().model == "opus"


@pytest.mark.parametrize("bad", ["--dangerously-skip-permissions", "-p", "opus extra", "sonnet\nx", "a;b", "x" * 200])
def test_model_names_that_could_be_cli_options_are_rejected(tmp_path, bad):
    import app.services.ai_key_profiles as mod

    with (
        mock.patch.object(mod, "_profiles_path", lambda: tmp_path / "p.json"),
        mock.patch.object(mod, "_secrets_path", lambda: tmp_path / "s.json"),
        pytest.raises(ValueError, match="Model name"),
    ):
        mod.create_profile(name="bad", provider="claude_cli", api_key="", model=bad)


def test_valid_model_names_pass_validation():
    from app.services.ai_key_profiles import validate_model_name

    for ok in ("sonnet", "gpt-5.5", "gpt-5.6-sol", "qwen/qwen3.5:9b", ""):
        assert validate_model_name(ok) == ok


@pytest.mark.asyncio
async def test_claude_runner_refuses_option_like_model_before_spawning():
    with (
        mock.patch.object(ai_provider, "_claude_base_command", return_value=["claude"]),
        mock.patch.object(ai_provider, "_run_cli_process") as spawn,
        pytest.raises(ValueError, match="Model name"),
    ):
        await generate_ai_text([{"role": "user", "content": "x"}], timeout=30,
                               config_snapshot=_claude("--dangerously-skip-permissions"))
    spawn.assert_not_called()


def test_profile_api_accepts_keyless_subscription_and_probes_cli(tmp_path):
    from fastapi.testclient import TestClient

    import app.services.ai_key_profiles as mod
    from app.main import app

    probe = mock.AsyncMock(return_value={"ok": True, "responded": True})
    with (
        mock.patch.object(mod, "_profiles_path", lambda: tmp_path / "p.json"),
        mock.patch.object(mod, "_secrets_path", lambda: tmp_path / "s.json"),
        mock.patch.object(ai_provider, "probe_subscription_cli", probe),
        TestClient(app) as client,
    ):
        created = client.post("/api/settings/ai-key-profiles",
                              json={"name": "Claude Max", "provider": "claude_cli", "model": "sonnet"})
        assert created.status_code == 200, created.text
        rejected = client.post("/api/settings/ai-key-profiles", json={"name": "x", "provider": "openai"})
        assert rejected.status_code == 400
        profile_id = created.json()["profile"]["id"]
        tested = client.post(f"/api/settings/ai-key-profiles/{profile_id}/test")

    assert tested.json()["ok"] is True
    probe.assert_awaited_once_with("claude_cli", "sonnet")

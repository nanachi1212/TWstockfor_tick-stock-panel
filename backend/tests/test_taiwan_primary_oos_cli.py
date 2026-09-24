from __future__ import annotations

from app.taiwan.quant.data_health import evaluate_data_health
from app.taiwan.quant.primary_oos_runner import PrimaryOosPreflight
from scripts import taiwan_primary_oos_evaluation as cli


def _blocked_preflight():
    health = evaluate_data_health(
        census_sessions=2850, census_total_sessions=2850,
        twse_codes_observed=100, twse_codes_classified=50,
    )
    progress = {
        "completed_jobs": 220, "pending_jobs": 258,
        "failed_jobs": 0, "unique_first_seen_dates": 478,
    }
    from datetime import date

    return PrimaryOosPreflight(health, progress, "running", date(2026, 9, 23))


def test_cli_refuses_before_runner_when_gate_is_not_ready(monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_primary_oos_preflight", _blocked_preflight)

    def must_not_run(*args, **kwargs):
        raise AssertionError("CLI must stop at the readiness gate")

    monkeypatch.setattr(cli, "run_primary_oos_evaluation", must_not_run)
    assert cli.main(["--json"]) == 2
    output = capsys.readouterr().out
    assert '"status": "waiting_for_data_health"' in output
    assert '"pending_jobs": 258' in output


def test_cli_does_not_accept_gate_bypass_options():
    import pytest

    with pytest.raises(SystemExit) as exc:
        cli.main(["--force"])
    assert exc.value.code == 2

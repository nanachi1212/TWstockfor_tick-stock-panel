from __future__ import annotations

from pathlib import Path

import polars as pl

from app.release_privacy import audit_release_paths


def test_public_market_symbols_do_not_trigger_private_record_gate(tmp_path: Path):
    public = tmp_path / "data" / "taiwan" / "security_master.parquet"
    public.parent.mkdir(parents=True)
    pl.DataFrame(
        {"symbol": ["8358.TPEX", "2344.TWSE"], "name": ["金居", "華邦電"]}
    ).write_parquet(public)

    report = audit_release_paths([tmp_path / "data"], scope="seed")

    assert report.result == "PASS"
    assert report.portfolio_records_found == 0
    assert report.watchlist_records_found == 0


def test_private_path_and_portfolio_schema_fail_gate(tmp_path: Path):
    private = tmp_path / "user_data" / "portfolio.parquet"
    private.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["8358"], "cost_basis": [123.0]}).write_parquet(private)

    report = audit_release_paths([tmp_path], scope="seed")

    assert report.result == "FAIL"
    assert report.private_user_files_found > 0
    assert report.portfolio_records_found > 0


def test_credential_and_maintainer_path_fail_without_echoing_values(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("USERNAME", "Maintainer")
    suspect = tmp_path / "metadata.json"
    suspect.write_text(
        '{"api_key":"sk-example0123456789abcdef","build":"C:\\\\Users\\\\Maintainer\\\\repo"}',
        encoding="utf-8",
    )

    report = audit_release_paths([suspect], scope="artifact")

    assert report.result == "FAIL"
    assert report.credentials_found > 0
    assert report.maintainer_paths_found > 0
    assert all("sk-example" not in finding["detail"] for finding in report.findings)


def test_github_runner_profile_is_not_maintainer_personal_data(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("USERNAME", "runneradmin")
    metadata = tmp_path / "metadata.txt"
    metadata.write_text(
        r"C:\Users\runneradmin\AppData\Local\pyinstaller\build",
        encoding="utf-8",
    )

    report = audit_release_paths([metadata], scope="artifact")

    assert report.result == "PASS"
    assert report.maintainer_paths_found == 0

    private_build = tmp_path / "private-build.txt"
    private_build.write_text(
        r"F:\Projects\Codex project\tick-stock-panel\backend",
        encoding="utf-8",
    )
    private_report = audit_release_paths([private_build], scope="artifact")
    assert private_report.result == "FAIL"
    assert private_report.maintainer_paths_found > 0

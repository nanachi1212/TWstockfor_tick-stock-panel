import pytest

from app.services import portfolio_import


def test_lists_and_reads_only_holdings_files_inside_sync_roots(tmp_path, monkeypatch):
    root = tmp_path / "OneDrive"
    (root / "股票").mkdir(parents=True)
    holdings = root / "股票" / "個人投資組合追蹤表.csv"
    holdings.write_text("證券代號,持有股數\n2330,1000\n", encoding="utf-8-sig")
    (root / "股票" / "notes.csv").write_text("x", encoding="utf-8")
    outside = tmp_path / "持股.csv"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(portfolio_import, "cloud_roots", lambda: {"onedrive": [root], "gdrive": []})

    assert [f["name"] for f in portfolio_import.list_files("onedrive")] == ["個人投資組合追蹤表.csv"]
    assert portfolio_import.read_file(str(holdings)).startswith("證券代號")
    for blocked in (outside, root / "股票" / "notes.csv", root / ".." / "持股.csv"):
        with pytest.raises(PermissionError):
            portfolio_import.read_file(str(blocked))


def test_google_sheet_url_must_be_a_sheets_link():
    with pytest.raises(ValueError):
        portfolio_import.fetch_google_sheet("http://127.0.0.1:8000/admin")


def test_file_scan_stops_after_maximum_matches(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    for index in range(4):
        (root / f"portfolio-{index}.csv").write_text("code,shares\n2330,1", encoding="utf-8")
    monkeypatch.setattr(portfolio_import, "cloud_roots", lambda: {"onedrive": [root], "gdrive": []})
    monkeypatch.setattr(portfolio_import, "MAX_FILES", 2)

    assert len(portfolio_import.list_files("onedrive")) == 2


def test_google_sheet_response_is_bounded(monkeypatch):
    class Response:
        status_code = 200
        url = "https://docs.google.com/spreadsheets/export"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_bytes(self):
            yield b"x" * 4

    monkeypatch.setattr(portfolio_import, "MAX_BYTES", 3)
    monkeypatch.setattr(portfolio_import.httpx, "stream", lambda *_args, **_kwargs: Response())

    with pytest.raises(ValueError, match="太大"):
        portfolio_import.fetch_google_sheet("https://docs.google.com/spreadsheets/d/abcdefghijklmnopqrst")

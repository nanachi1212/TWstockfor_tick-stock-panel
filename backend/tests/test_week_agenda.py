from datetime import datetime

import pytest

from app.taiwan import week_agenda
from app.taiwan.realtime.calendar import TAIPEI_TZ


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch):
    monkeypatch.setattr(week_agenda, "_cache", None)


def test_week_switches_on_sunday_noon_taipei():
    assert week_agenda.week_range(datetime(2026, 11, 15, 11, 0, tzinfo=TAIPEI_TZ))[0].isoformat() == "2026-11-09"
    assert week_agenda.week_range(datetime(2026, 11, 15, 12, 0, tzinfo=TAIPEI_TZ))[0].isoformat() == "2026-11-16"


def test_agenda_filters_translates_and_keeps_last_good_data_on_failure():
    now = datetime(2026, 11, 13, 9, 0, tzinfo=TAIPEI_TZ)
    macro = [
        {"title": "Core CPI m/m", "country": "USD", "date": "2026-11-12T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
        {"title": "Fed Chair Powell Speaks", "country": "USD", "date": "2026-11-13T10:00:00-05:00", "impact": "Medium"},
        {"title": "Bank Holiday", "country": "JPY", "date": "2026-11-10T00:00:00-05:00", "impact": "Holiday"},
        {"title": "Bank Holiday", "country": "EUR", "date": "2026-11-10T00:00:00-05:00", "impact": "Holiday"},
        {"title": "RBA Speaks", "country": "AUD", "date": "2026-11-10T00:00:00-05:00", "impact": "High"},
        {"title": "Retail Sales m/m", "country": "USD", "date": "2026-11-12T08:30:00-05:00", "impact": "Low"},
    ]
    rows = [
        {"symbol": "NVDA", "name": "NVIDIA", "marketCap": "$4,000,000,000,000", "time": "time-after-hours", "epsForecast": "$1.20"},
        {"symbol": "JPM", "name": "JPMorgan", "marketCap": "$700,000,000,000", "time": "time-pre-market"},
        {"symbol": "TINY", "name": "Tiny", "marketCap": "$1,000,000", "time": "time-pre-market"},
    ]

    def fetch(url, **_kw):
        if "faireconomy" in url:
            return macro
        return {"data": {"rows": rows if url.endswith("2026-11-12") else None}}

    out = week_agenda.week_agenda(now=now, fetch=fetch)
    assert out["errors"] == [] and (out["start"], out["end"]) == ("2026-11-09", "2026-11-15")
    assert [e["title"] for e in out["macro"]] == ["金融市場休假", "核心 CPI（月增）", "Fed 主席 Powell 談話"]
    assert out["macro"][1]["time"] == "2026-11-12T21:30:00+08:00"  # converted to Taipei time
    assert [(e["symbol"], e["timing"]) for e in out["earnings"]] == [("JPM", "盤前"), ("NVDA", "盤後")]

    def down(*_a, **_kw):
        raise TimeoutError

    week_agenda._cache["_fetched_mono"] -= week_agenda._FRESH_SECONDS + 1
    again = week_agenda.week_agenda(now=now, fetch=down)
    assert len(again["errors"]) == 2 and "顯示上次資料" in again["errors"][0]
    assert len(again["macro"]) == 3 and len(again["earnings"]) == 2

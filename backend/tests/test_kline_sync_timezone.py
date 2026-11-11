"""Minute requests use the Taiwan session and an explicit Asia/Taipei timezone."""
from __future__ import annotations

from datetime import date, datetime

from app.services import kline_sync
from app.taiwan.realtime.calendar import TAIPEI_TZ


def test_fetch_minute_single_window_is_taipei_wall_clock(monkeypatch):
    captured: dict[str, datetime] = {}

    def _fake_try_custom_minute(symbols, start_time, end_time, asset_type, freq="1m", **kwargs):
        captured["start"] = start_time
        captured["end"] = end_time
        return (None, True)  # 未配自定義源 → 繼續後續分支

    monkeypatch.setattr(kline_sync, "_try_custom_minute", _fake_try_custom_minute)

    kline_sync.fetch_minute_single("2330.TWSE", date(2026, 8, 14))

    # 必須帶時區, 否則下游 _datetime_to_ms 會按伺服器本地時區解釋。
    assert captured["start"].tzinfo is not None
    assert captured["end"].tzinfo is not None

    # 經 _datetime_to_ms 往返後仍是台北時間的 09:00 / 13:30。
    start = datetime.fromtimestamp(kline_sync._datetime_to_ms(captured["start"]) / 1000, tz=TAIPEI_TZ)
    end = datetime.fromtimestamp(kline_sync._datetime_to_ms(captured["end"]) / 1000, tz=TAIPEI_TZ)
    assert (start.date(), start.hour, start.minute) == (date(2026, 8, 14), 9, 0)
    assert (end.date(), end.hour, end.minute) == (date(2026, 8, 14), 13, 30)

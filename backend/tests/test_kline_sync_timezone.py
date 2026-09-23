"""时区契约测试 — 分时拉取窗口必须按北京时间解释, 与服务器本地时区无关。

fetch_minute_single 若构造 naive datetime, _datetime_to_ms 的 .timestamp() 会按
服务器本地时区解释: UTC 容器 (Docker 默认) 上窗口偏移 8 小时, 补拉必为空。

窗口在 _try_custom_minute 之前构造, 因此直接在该入口断言。f8aef18 起
minute provider 为 taiwan/tickflow 时会在 _try_custom_minute 之后短路返回空,
不再走 tf.klines.batch, 该分支已不是可观测的默认路径。
"""
from __future__ import annotations

from datetime import date, datetime

from app.market_time import CN_TZ
from app.services import kline_sync


def test_fetch_minute_single_window_is_beijing_wall_clock(monkeypatch):
    captured: dict[str, datetime] = {}

    def _fake_try_custom_minute(symbols, start_time, end_time, asset_type, freq="1m", **kwargs):
        captured["start"] = start_time
        captured["end"] = end_time
        return (None, True)  # 未配自定义源 → 继续后续分支

    monkeypatch.setattr(kline_sync, "_try_custom_minute", _fake_try_custom_minute)

    kline_sync.fetch_minute_single("600000.SH", date(2026, 8, 14))

    # 必须带时区, 否则下游 _datetime_to_ms 会按服务器本地时区解释。
    assert captured["start"].tzinfo is not None
    assert captured["end"].tzinfo is not None

    # 经 _datetime_to_ms 往返后仍是北京时间的 09:25 / 15:05。
    start = datetime.fromtimestamp(kline_sync._datetime_to_ms(captured["start"]) / 1000, tz=CN_TZ)
    end = datetime.fromtimestamp(kline_sync._datetime_to_ms(captured["end"]) / 1000, tz=CN_TZ)
    assert (start.date(), start.hour, start.minute) == (date(2026, 8, 14), 9, 25)
    assert (end.date(), end.hour, end.minute) == (date(2026, 8, 14), 15, 5)

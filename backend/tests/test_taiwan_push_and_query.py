"""早晚一句話推播、自選異常摘要、Telegram 查詢、AI 回饋、啟動補更新的單元測試 (全部離線)。"""
# ruff: noqa: RUF001 -- Traditional Chinese fixtures are intentional.
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services import preferences
from app.taiwan import ai_feedback, push_digest, startup_catchup, telegram_bot, watchlist_anomaly


@pytest.fixture
def isolated_prefs(monkeypatch, tmp_path):
    store: dict = {}
    monkeypatch.setattr(preferences, "load", lambda: dict(store))
    monkeypatch.setattr(preferences, "save", lambda updates: store.update(updates))
    monkeypatch.setattr(preferences, "get_external_notification_channels", lambda: store.get("channels"))
    monkeypatch.setattr(preferences, "get_line_channel_access_token", lambda: "line-token")
    monkeypatch.setattr(preferences, "get_line_target_id", lambda: "U123")
    monkeypatch.setattr(preferences, "get_telegram_bot_token", lambda: "tg-token")
    monkeypatch.setattr(preferences, "get_telegram_chat_id", lambda: "42")
    return store


def _plan(entry="pullback"):
    return SimpleNamespace(
        entry_semantics="breakout_stop" if entry == "breakout" else "pullback_zone",
        entry_zone_low=95.0, entry_zone_high=98.0, breakout_trigger=105.0, stop_price=90.0,
    )


def _candidate(symbol="2330.TWSE", name="台積電", state="observable", plan=None):
    plan = _plan() if plan is None else plan
    return SimpleNamespace(
        symbol=symbol, name=name, selection_state=state, trade_plan=plan, as_of="2026-10-08",
        action_summary="站上均線且量能放大，可觀察承接。", invalidation="跌破 90 視為失效。",
    )


class _FakeSelectionService:
    def __init__(self, candidates=None, status="ready"):
        self._candidates = candidates if candidates is not None else [_candidate()]
        self._status = status

    def build(self, limit=20):
        market = SimpleNamespace(
            as_of="2026-10-08", headline="今日市場偏強", advance_count=900, decline_count=500,
            strongest_industries=["半導體", "航運"],
        )
        return SimpleNamespace(status=self._status, market=market, candidates=self._candidates[:limit])

    def evaluate_symbol(self, symbol):
        return SimpleNamespace(candidate=_candidate(symbol=symbol))


def _quote(symbol, price, pct, name="台積電", source="twse_mis", stale=False):
    """pct 為百分點 (與 TaiwanRealtimeQuote.change_pct 契約一致: 4.5 = +4.50%)。"""
    return SimpleNamespace(
        symbol=symbol, name=name, last_price=price, change_pct=pct,
        quote_time=datetime(2026, 10, 8, 10, 5), source_meta=SimpleNamespace(source=source, is_stale=stale),
    )


# ── push_digest ───────────────────────────────────────────────────────────


def test_morning_text_lists_market_and_top_picks(monkeypatch):
    import app.taiwan.beginner_selection as bs

    monkeypatch.setattr(bs, "BeginnerSelectionService", lambda: _FakeSelectionService([
        _candidate(), _candidate("2454.TWSE", "聯發科", "wait_breakout", _plan("breakout")),
    ]))
    text = push_digest.build_morning_text()
    assert "今日市場偏強" in text
    assert "漲 900 家／跌 500 家" in text
    assert "台積電（2330）可觀察，承接 95.00～98.00／失效 90.00" in text
    assert "聯發科（2454）等突破，突破 105.00／失效 90.00" in text
    assert text.endswith("規則整理，非投資建議。")


def test_morning_text_states_when_no_candidates(monkeypatch):
    import app.taiwan.beginner_selection as bs

    monkeypatch.setattr(bs, "BeginnerSelectionService", lambda: _FakeSelectionService([], status="unavailable"))
    assert "今日選股資料不足" in push_digest.build_morning_text()


def test_evening_text_sorts_watchlist_and_reports_missing(monkeypatch):
    import app.taiwan.realtime as rt
    from app.services import watchlist

    monkeypatch.setattr(watchlist, "list_symbols", lambda: [
        {"symbol": "2330.TWSE"}, {"symbol": "2454.TWSE"}, {"symbol": "600000.SH"}, {"symbol": "0050.TWSE"},
    ])
    quotes = {"2330.TWSE": _quote("2330.TWSE", 1000.0, -1.2), "2454.TWSE": _quote("2454.TWSE", 1200.0, 3.4, "聯發科")}
    monkeypatch.setattr(rt, "get_realtime_service", lambda: SimpleNamespace(get_quotes=lambda symbols: quotes))
    text = push_digest.build_evening_text("2026-10-08")
    lines = text.split("\n")
    assert lines[0] == "【收盤一句話】2026-10-08"
    assert "自選 2 檔：漲 1、跌 1" in text
    assert lines.index("• 聯發科（2454）+3.40%，收 1200") < lines.index("• 台積電（2330）-1.20%，收 1000")
    assert "今日無報價：0050" in text
    assert "600000" not in text  # 非台股符號不進摘要


def test_run_respects_switch_and_channels(monkeypatch, isolated_prefs):
    sent = []
    import app.taiwan.realtime as rt
    from app.services import webhook_adapter

    monkeypatch.setattr(rt, "taipei_now", lambda: datetime(2026, 10, 8, 8, 45))
    monkeypatch.setattr(push_digest, "build_morning_text", lambda: "hello")
    monkeypatch.setattr(webhook_adapter, "send_line", lambda *a: sent.append(("line", a[-1])) or True)
    monkeypatch.setattr(webhook_adapter, "send_telegram", lambda *a: sent.append(("telegram", a[-1])) or True)

    assert push_digest.run("morning")["status"] == "disabled"
    push_digest.set_enabled(True)
    assert push_digest.run("morning")["status"] == "no_channels"
    isolated_prefs["channels"] = ["telegram"]
    result = push_digest.run("morning")
    assert result["status"] == "sent" and result["channels"] == ["telegram"]
    assert sent == [("telegram", "hello")]
    assert push_digest.last_run()["kind"] == "morning"


# ── watchlist_anomaly ────────────────────────────────────────────────────


def _patch_anomaly_env(monkeypatch, quotes, status="open"):
    import app.taiwan.realtime as rt
    from app.services import watchlist

    monkeypatch.setattr(watchlist, "list_symbols", lambda: [{"symbol": s} for s in quotes])
    monkeypatch.setattr(rt, "taipei_now", lambda: datetime(2026, 10, 8, 10, 5))
    monkeypatch.setattr(rt, "get_market_status", lambda *a, **k: SimpleNamespace(value=status))
    monkeypatch.setattr(rt, "get_realtime_service", lambda: SimpleNamespace(get_quotes=lambda symbols: quotes))


def test_anomaly_scan_flags_once_per_direction(monkeypatch, isolated_prefs, tmp_path):
    from app.services import alert_store

    watchlist_anomaly.reset_memory()
    watchlist_anomaly.set_enabled(True)
    appended: list[list[dict]] = []
    monkeypatch.setattr(alert_store, "append_many", lambda data_dir, events: appended.append(events) or [])
    quotes = {
        "2330.TWSE": _quote("2330.TWSE", 1000.0, 4.5),
        "2454.TWSE": _quote("2454.TWSE", 1200.0, -1.0, "聯發科"),
        "0050.TWSE": _quote("0050.TWSE", 200.0, 8.0, "元大台灣50", stale=True),  # 非即時來源不報
    }
    _patch_anomaly_env(monkeypatch, quotes)
    app_state = SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)), quote_service=None)

    first = watchlist_anomaly.scan(app_state)
    assert first["status"] == "ok" and first["symbols"] == ["2330.TWSE"]
    assert appended[0][0]["message"].startswith("自選股大漲 +4.50%")
    assert appended[0][0]["source"] == "watchlist_anomaly"
    assert isinstance(appended[0][0]["ts"], int) and appended[0][0]["ts"] > 1_600_000_000_000  # 毫秒 epoch
    # 同一天同方向不重複
    assert watchlist_anomaly.scan(app_state) == {"status": "ok", "flagged": 0}
    # 反向才再提醒
    quotes["2330.TWSE"] = _quote("2330.TWSE", 900.0, -5.0)
    assert watchlist_anomaly.scan(app_state)["symbols"] == ["2330.TWSE"]


def test_anomaly_scan_skips_outside_open_session(monkeypatch, isolated_prefs):
    watchlist_anomaly.set_enabled(True)
    _patch_anomaly_env(monkeypatch, {"2330.TWSE": _quote("2330.TWSE", 1000.0, 9.0)}, status="closed")
    assert watchlist_anomaly.scan(None) == {"status": "market_closed"}


def test_anomaly_threshold_is_clamped(isolated_prefs):
    assert watchlist_anomaly.set_threshold_pct(0.2) == 1.0
    assert watchlist_anomaly.set_threshold_pct(50) == 10.0
    assert watchlist_anomaly.set_threshold_pct(4.5) == 4.5


# ── telegram_bot ─────────────────────────────────────────────────────────


def test_handle_text_routes_commands(monkeypatch):
    monkeypatch.setattr(telegram_bot, "answer_picks", lambda: "PICKS")
    monkeypatch.setattr(telegram_bot, "answer_watchlist", lambda: "WATCH")
    monkeypatch.setattr(telegram_bot, "answer_symbol", lambda code: f"SYMBOL:{code}")
    assert telegram_bot.handle_text("/picks") == "PICKS"
    assert telegram_bot.handle_text("自選股") == "WATCH"
    assert telegram_bot.handle_text(" 2330 ") == "SYMBOL:2330"
    assert telegram_bot.handle_text("/00878") == "SYMBOL:00878"
    assert "可用指令" in telegram_bot.handle_text("/help")
    assert telegram_bot.handle_text("hello world").startswith("看不懂這個指令")


def test_answer_symbol_combines_quote_plan_and_ai(monkeypatch):
    import app.taiwan.auto_ai_explain as auto
    import app.taiwan.beginner_selection as bs
    import app.taiwan.realtime as rt
    import app.taiwan.universe as universe

    monkeypatch.setattr(universe, "get_security_master", lambda: SimpleNamespace(
        search=lambda q, limit=5: [{"symbol": "2330.TWSE", "code": "2330", "name": "台積電"}],
    ))
    monkeypatch.setattr(rt, "get_realtime_service", lambda: SimpleNamespace(
        get_quotes=lambda symbols: {"2330.TWSE": _quote("2330.TWSE", 1000.0, 1.2)},
    ))
    monkeypatch.setattr(bs, "BeginnerSelectionService", lambda: _FakeSelectionService())
    seen = []
    monkeypatch.setattr(auto, "stored_explanation", lambda symbol, min_as_of: seen.append(min_as_of) or {
        "report": {"beginner_answer": {"can_buy": "規則判斷可觀察，尚未進承接區。"}, "overview": "略",
                   "evidence_as_of": "2026-10-08"},
    })
    text = telegram_bot.answer_symbol("2330")
    assert "台積電（2330）現價 1000（+1.20%）" in text
    assert "規則判斷：可觀察。" in text
    assert "承接區 95.00～98.00／失效位 90.00" in text
    assert "AI 一句話（資料截至 2026-10-08）：規則判斷可觀察" in text
    assert seen == ["2026-10-08"]  # 以今日規則結果的 as_of 作為最低證據日期


def test_answer_symbol_unknown_code(monkeypatch):
    import app.taiwan.universe as universe

    monkeypatch.setattr(universe, "get_security_master", lambda: SimpleNamespace(search=lambda q, limit=5: []))
    assert telegram_bot.answer_symbol("9999").startswith("找不到代號 9999")


def test_bot_does_not_start_without_credentials(monkeypatch):
    monkeypatch.setattr(preferences, "get_telegram_bot_token", lambda: "")
    monkeypatch.setattr(preferences, "get_telegram_chat_id", lambda: "42")
    assert telegram_bot.TelegramQueryBot().start() is False


def test_evening_text_only_summarises_queried_symbols(monkeypatch):
    import app.taiwan.realtime as rt
    from app.services import watchlist

    symbols = [f"{1000 + i}.TWSE" for i in range(35)]
    monkeypatch.setattr(watchlist, "list_symbols", lambda: [{"symbol": s} for s in symbols])
    asked = []
    monkeypatch.setattr(rt, "get_realtime_service", lambda: SimpleNamespace(
        get_quotes=lambda syms: asked.append(list(syms)) or {s: _quote(s, 10.0, 0.5, name=s) for s in syms},
    ))
    text = push_digest.build_evening_text("2026-10-08")
    assert asked == [symbols[:30]]
    assert "自選 30 檔" in text
    assert "今日無報價" not in text  # 沒查的不能當成無報價
    assert "其餘 5 檔未納入本則摘要" in text


def test_feedback_summary_counts_last_vote_per_explanation(tmp_path):
    ai_feedback.record(symbol="2330.TWSE", helpful=True, record_id="r1", model="m", data_dir=tmp_path)
    ai_feedback.record(symbol="2330.TWSE", helpful=False, record_id="r1", model="m", data_dir=tmp_path)
    ai_feedback.record(symbol="2454.TWSE", helpful=True, model="m", data_dir=tmp_path)
    s = ai_feedback.summary(data_dir=tmp_path)
    assert (s["total"], s["helpful"], s["not_helpful"]) == (2, 1, 1)


# ── ai_feedback ──────────────────────────────────────────────────────────


def test_feedback_record_and_summary(tmp_path):
    ai_feedback.record(symbol="2330.twse", helpful=True, model="qwen3.6", data_dir=tmp_path)
    ai_feedback.record(symbol="2454.TWSE", helpful=False, model="qwen3.6", note="x" * 500, data_dir=tmp_path)
    ai_feedback.record(symbol="0050.TWSE", helpful=True, model="gpt-5.5", data_dir=tmp_path)
    s = ai_feedback.summary(tmp_path)
    assert (s["total"], s["helpful"], s["not_helpful"]) == (3, 2, 1)
    assert s["models"][0] == {"model": "qwen3.6", "helpful": 1, "not_helpful": 1, "helpful_ratio": 0.5}
    assert s["recent"][0]["symbol"] == "0050.TWSE"
    assert len(s["recent"][1]["note"]) == 200
    with pytest.raises(ValueError):
        ai_feedback.record(symbol="  ", helpful=True, data_dir=tmp_path)


# ── ai_research: 新手三問只能轉述規則 ───────────────────────────────────


def test_grounded_beginner_answer_rejects_invented_stop_and_buy_calls():
    from app.taiwan.ai_research import _grounded_beginner_answer

    selection = {"selection_state": "avoid", "action_summary": "量能不足，不符合承接條件。",
                 "invalidation": "跌破 90 視為失效。", "trade_plan": {"stop_price": 90.0}}
    out = _grounded_beginner_answer(
        {"can_buy": "可以買，現在就進場。", "stop_loss": "停損設在 85 元。", "give_up": ""}, selection,
    )
    assert out.stop_loss == "計畫失效位 90.00：收盤跌破就離場。"  # 停損沒引用計畫價位 → 退回規則句
    assert out.can_buy.startswith("規則判斷為「不追」")
    assert out.give_up == "跌破 90 視為失效。"
    kept = _grounded_beginner_answer({"can_buy": "規則判斷不追。", "stop_loss": "計畫失效位 90.00 元。", "give_up": "x"}, selection)
    assert kept.stop_loss == "計畫失效位 90.00 元。"
    none_plan = _grounded_beginner_answer({"stop_loss": "停損 85"}, {"selection_state": "observable", "trade_plan": None})
    assert none_plan.stop_loss.startswith("目前計畫價位資料不足")


# ── startup_catchup ──────────────────────────────────────────────────────


def test_needs_catchup_skips_during_session_and_when_current(monkeypatch):
    import app.taiwan.daily_update as du
    import app.taiwan.realtime as rt

    monkeypatch.setattr(rt, "taipei_now", lambda: datetime(2026, 10, 8, 10, 0))
    monkeypatch.setattr(rt, "get_market_status", lambda *a, **k: SimpleNamespace(value="open"))
    assert startup_catchup.needs_catchup() == (False, "market_open")

    monkeypatch.setattr(rt, "get_market_status", lambda *a, **k: SimpleNamespace(value="closed"))
    monkeypatch.setattr(du, "TaiwanDailyUpdateService", lambda: SimpleNamespace(
        get_freshness=lambda: SimpleNamespace(daily_status="current", daily_as_of="2026-10-08"),
    ))
    assert startup_catchup.needs_catchup() == (False, "current")

    monkeypatch.setattr(du, "TaiwanDailyUpdateService", lambda: SimpleNamespace(
        get_freshness=lambda: SimpleNamespace(daily_status="stale", daily_as_of="2026-10-06"),
    ))
    assert startup_catchup.needs_catchup() == (True, "daily_stale:2026-10-06")


def test_schedule_is_skipped_under_pytest_and_never_hits_network(monkeypatch):
    # pytest 會設 PYTEST_CURRENT_TEST; 啟動補跑不得在測試環境排程 (會連網並寫入共用資料目錄)。
    startup_catchup._STARTED.clear()
    startup_catchup._LAST.clear()
    assert startup_catchup.schedule(SimpleNamespace(), delay_seconds=0) is False
    assert startup_catchup._STARTED.is_set() is False
    assert startup_catchup.last_result() == {"status": "skipped", "reason": "pytest"}
    startup_catchup.cancel()

"""VCP 掃描服務：型態選股頁的 VCP 區塊與 CLI 共用的資料載入與彙整。

掃描範圍與型態選股相同（上市櫃、正常交易、四碼普通股）；交易日、除權息覆蓋與資料品質一律 fail-closed，
未通過的股票按原因計數，不補值。偵測與評分見 detect.py（移植來源與授權見 NOTICE.md）。
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

import polars as pl

from app.taiwan.adjust import adjust_prices_as_of, volume_window_status
from app.taiwan.corporate_actions import CorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.pattern_scan import _clean, _valid_bar
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.research_metrics import research_sessions
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.vcp.detect import LOOKBACK_DAYS, MIN_BARS, analyze, rs_percentiles, weighted_return

PRICE_COLUMNS = ("open", "high", "low", "close")
# 260 根有成交 K 棒，另留 40 個交易日給停牌、無成交日
READ_SESSIONS = 300
RULES = {"min_bars": MIN_BARS, "lookback_days": LOOKBACK_DAYS, "prefilter_min_price": 10.0,
         "prefilter_min_avg_volume_shares": 200_000, "max_above_pivot_pct": 3.0, "max_risk_pct": 15.0}


class TaiwanVcpScanService:
    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        evidence_store: ObservedUniverseStore | None = None,
        actions: CorporateActionStore | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self.calendar = calendar or TaiwanTradingCalendar()
        self.security_master = security_master or get_security_master()
        self.evidence_store = evidence_store or ObservedUniverseStore()
        self.actions = actions or CorporateActionStore()

    def universe(self) -> pl.DataFrame:
        return self.security_master.to_dataframe(supported_only=True).filter(
            (pl.col("instrument_type") == "stock") & (pl.col("listing_status") == "active")
            & pl.col("exchange").is_in(["TWSE", "TPEX"]) & pl.col("code").str.contains(r"^[1-9]\d{3}$")
        )

    def resolve_target(self, target_date: date | None) -> date:
        return target_date or resolve_target_latest_trading_date(self.calendar, evidence_store=self.evidence_store)

    def evaluate(self, target: date, symbols: list[str], universe: list[str]) -> tuple[dict[str, dict], int, str | None]:
        """回傳 ({symbol: 結果}, RS 排名母體數, 整體不可用原因)。

        結果不是 analyze() 的輸出，就是 {"status": "excluded", "reason": 原因代碼}。
        """
        available = set(self.daily_store.available_dates())
        if target not in available:
            return {}, 0, "no_daily_data"
        coverage = self.actions.read_verified_coverage()
        if coverage is None or coverage[1] < target:
            return {}, 0, "corporate_action_coverage_unavailable"
        # 應有的交易日（只跳過確認休市日，與型態選股同一套）；個股缺任何一天就排除，不往前滑補
        sessions = research_sessions(target, READ_SESSIONS, available, self.calendar, self.evidence_store)
        wanted = list(dict.fromkeys(universe + symbols))
        first = sessions[0]
        raw = self.daily_store.read_range(wanted, first, target)
        events_by_symbol: dict[str, list] = {}
        for event in coverage[2]:
            if first < event.effective_date <= target:
                events_by_symbol.setdefault(event.symbol, []).append(event)
        prepared: dict[str, dict] = {}
        for (symbol,), group in raw.sort("symbol", "date").group_by("symbol", maintain_order=True):
            prepared[str(symbol)] = self._prepare(str(symbol), group, target, coverage[0],
                                                  events_by_symbol.get(str(symbol), []), sessions)

        returns = {}
        for symbol in universe:
            item = prepared.get(symbol, {})
            if "bars" in item:  # 價格已驗證（成交量是否可比不影響報酬排名）
                r = weighted_return([b["close"] for b in reversed(item["bars"])])
                if r is not None:
                    returns[symbol] = r
        pct = rs_percentiles(returns)

        out: dict[str, dict] = {}
        for symbol in symbols:
            item = prepared.get(symbol, {"reason": "missing_target_session"})
            if "reason" in item:
                out[symbol] = {"status": "excluded", "reason": item["reason"]}
            else:
                out[symbol] = {**analyze(item["bars"], pct.get(symbol)), "no_trade_skipped": item["no_trade_skipped"]}
        return out, len(returns), None

    def _prepare(self, symbol: str, group: pl.DataFrame, target: date, coverage_start: date,
                 symbol_events: list, sessions: list[date]) -> dict:
        rows = {r["date"]: r for r in group.select("date", *PRICE_COLUMNS, "volume").to_dicts()}
        if target not in rows:
            return {"reason": "missing_target_session"}
        if _no_trade(rows[target]):
            return {"reason": "no_trade_on_target"}
        listed_from = min(rows)
        window: list[dict] = []
        skipped = 0
        for session in reversed(sessions):
            row = rows.get(session)
            if row is None:  # 沒有紀錄是未知資料，不能當成無成交略過
                return {"reason": "insufficient_history" if session < listed_from else "incomplete_sessions"}
            if _no_trade(row):  # 正常盤無成交（交易所顯示「--」）有明確紀錄：略過，不算 K 棒
                skipped += 1
                continue
            window.append(row)
            if len(window) == MIN_BARS:
                break
        else:
            return {"reason": "insufficient_history"}
        window.reverse()
        if not all(_valid_bar(b) for b in window):
            return {"reason": "invalid_ohlc"}
        start = window[0]["date"]
        if coverage_start > start:
            return {"reason": "corporate_action_unverified"}
        events = [e for e in symbol_events if start < e.effective_date]
        if events:
            frame = pl.DataFrame([{"symbol": symbol, **b} for b in window], schema_overrides={"volume": pl.Float64})
            adjusted = adjust_prices_as_of(frame, as_of=target, events=events, price_columns=PRICE_COLUMNS)
            if adjusted.status != "verified":
                return {"reason": "corporate_action_unverified"}
            bars = adjusted.to_frame().sort("date").select("date", *PRICE_COLUMNS, "volume").to_dicts()
        else:  # 覆蓋已驗證且視窗內無事件：原始價本身就一致（同型態選股）
            bars = window
        # 量能指標只看最近 LOOKBACK_DAYS 根；這段內股數變動會讓前後成交量不可比
        volume_ok = volume_window_status(events, symbol=symbol, start_session=bars[-LOOKBACK_DAYS]["date"],
                                         end_session=target, measure="volume") == "verified"
        return {"bars": bars, "no_trade_skipped": skipped,
                **({} if volume_ok else {"reason": "share_count_event_in_volume_window"})}

    def snapshot(self, target_date: date | None = None) -> dict[str, Any]:
        target = self.resolve_target(target_date)
        meta = {row["symbol"]: row for row in self.universe().to_dicts()}
        out: dict[str, Any] = {"date": str(target), "status": "unavailable", "reason": None, "rules": RULES,
                               "universe": len(meta), "eligible": 0, "rs_universe": 0, "matched": 0,
                               "signals": 0, "excluded": {}, "items": []}
        results, rs_universe, reason = self.evaluate(target, sorted(meta), sorted(meta))
        if reason:
            out["reason"] = reason
            return out
        excluded: Counter[str] = Counter()
        items = []
        for symbol, r in results.items():
            if r["status"] == "excluded":
                excluded[r["reason"]] += 1
                continue
            if r["status"] != "ok":  # insufficient_data / invalid_data / data_unverified（RS 不可用）
                excluded[r["status"]] += 1
                continue
            out["eligible"] += 1
            if r["valid_vcp"]:
                row = meta[symbol]
                items.append({"symbol": symbol, "code": row["code"], "name": row["name"],
                              "industry": row.get("industry"), **_item(r)})
        items.sort(key=lambda i: (not i["signal"], -i["composite_score"], i["symbol"]))
        out.update(status="available", rs_universe=rs_universe, matched=len(items),
                   signals=sum(i["signal"] for i in items), excluded=dict(sorted(excluded.items())),
                   items=_clean(items))
        return out


def _item(r: dict) -> dict:
    tt, vol = r["trend_template"], r["volume_pattern"]
    return {
        "price": r["price"], "signal": r["signal"], "entry_ready": r["entry_ready"],
        "composite_score": r["composite_score"], "rating": r["rating"], "quality_rating": r["quality_rating"],
        "cap_reason": r["cap_reason"], "execution_state": r["execution_state"],
        "execution_state_reason": r["execution_state_reason"], "pattern_type": r["pattern_type"],
        "pivot": r["pivot"], "stop": r["stop"], "risk_pct": r["risk_pct"],
        "distance_from_pivot_pct": r["distance_from_pivot_pct"], "trade_status": r["trade_status"],
        "rs_percentile": r["relative_strength"]["rs_percentile"],
        "dry_up_ratio": vol["dry_up_ratio"], "breakout_volume_ratio": vol["bar0_volume_ratio"],
        "breakout_volume_detected": vol["breakout_volume_detected"],
        "trend_criteria": tt["criteria"], "trend_passed": tt["passed"], "prefilter_passed": r["gates"]["prefilter_passed"],
        "contractions": [{k: c[k] for k in ("label", "high_date", "high_price", "low_date", "low_price",
                                            "depth_pct", "duration_days")} for c in r["vcp_pattern"]["contractions"]],
        "component_scores": r["component_scores"], "no_trade_skipped": r["no_trade_skipped"],
    }


def _no_trade(row: dict) -> bool:
    return all(row[c] is None for c in PRICE_COLUMNS)

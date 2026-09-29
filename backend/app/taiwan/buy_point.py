"""Deterministic buy-point strategies for Taiwan watchlists.

This module deliberately contains no provider calls and no trading action.  It
evaluates a typed, point-in-time market snapshot and returns an explainable
signal.  API and scheduled callers are responsible for assembling that snapshot
from the repository's existing data stores.
"""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese labels are intentional.
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

BuyPointStatus = Literal["waiting", "approaching", "triggered", "blocked", "unavailable"]


class BuyPointConditions(BaseModel):
    quant_min: float | None = None
    pullback_min_pct: float | None = None
    pullback_max_pct: float | None = None
    breakout_window: int | None = None
    volume_multiplier: float | None = None
    revenue_yoy_min: float | None = None
    pe_min: float | None = None
    pe_max: float | None = None
    pb_min: float | None = None
    pb_max: float | None = None
    eps_min: float | None = None
    institutional_required: bool = False
    foreign_shareholding_change_min: float | None = None
    max_price_extension_pct: float | None = None
    resonance_min_categories: int = 3
    approaching_distance_pct: float = 1.0
    cooldown_minutes: int = 60


class BuyPointRiskFilters(BaseModel):
    exclude_disposition: bool = True
    exclude_suspension: bool = True
    exclude_delisting: bool = True
    exclude_capital_reduction_critical: bool = True
    exclude_regulatory_unknown: bool = True
    exclude_severe_event: bool = True


class BuyPointStrategy(BaseModel):
    id: str
    name: str
    description: str
    category: str
    enabled: bool = True
    preset: bool = False
    conditions: BuyPointConditions = Field(default_factory=BuyPointConditions)
    risk_filters: BuyPointRiskFilters = Field(default_factory=BuyPointRiskFilters)
    alert_channels: list[Literal["app", "line", "telegram"]] = Field(default_factory=lambda: ["app"])
    created_at: str
    updated_at: str


class BuyPointMarketData(BaseModel):
    """Inputs needed by the deterministic evaluator.

    ``None`` means unavailable.  Boolean risk fields are optional so a caller
    can distinguish a verified clear state from missing regulatory coverage.
    """

    symbol: str
    name: str = ""
    data_as_of: str | None = None
    freshness: str = "unknown"
    price: float | None = None
    quant_score: float | None = None
    quant_universe: bool | None = None
    daily: list[dict[str, Any]] = Field(default_factory=list)
    volume: float | None = None
    volume_average_20d: float | None = None
    foreign_net: float | None = None
    foreign_net_5d: float | None = None
    investment_trust_net: float | None = None
    institutional_trend: list[float] = Field(default_factory=list)
    foreign_shareholding_change_20d: float | None = None
    revenue_yoy: float | None = None
    revenue_mom: float | None = None
    eps: float | None = None
    net_income: float | None = None
    pe: float | None = None
    pb: float | None = None
    positive_event: bool | None = None
    price_extension_pct: float | None = None
    risk_data_available: bool | None = None
    disposition: bool | None = None
    suspended: bool | None = None
    delisted: bool | None = None
    capital_reduction_critical: bool | None = None
    regulatory_unknown: bool | None = None
    severe_event_risk: bool | None = None


class BuyPointSignal(BaseModel):
    strategy_id: str
    symbol: str
    name: str = ""
    detected_at: str
    data_as_of: str | None = None
    status: BuyPointStatus
    triggered_conditions: list[str] = Field(default_factory=list)
    failed_conditions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    risk_status: Literal["clear", "unknown"] = "unknown"
    price: float | None = None
    quant_score: float | None = None
    explanation: str
    freshness: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _preset(
    preset_id: str,
    name: str,
    description: str,
    category: str,
    conditions: BuyPointConditions,
) -> BuyPointStrategy:
    now = _now()
    return BuyPointStrategy(
        id=preset_id, name=name, description=description, category=category,
        preset=True, conditions=conditions, created_at=now, updated_at=now,
    )


def builtin_presets() -> list[BuyPointStrategy]:
    """Return the stable built-in catalogue, with no user-data dependency."""
    return [
        _preset("quant_pullback", "Quant 強勢回檔", "等待 Quant 維持高檔的強勢股回檔，不追高。", "價格／Quant", BuyPointConditions(quant_min=70, pullback_min_pct=3, pullback_max_pct=6)),
        _preset("breakout_high", "突破前高", "觀察整理後突破近期前高，需有 Quant 支持。", "價格突破", BuyPointConditions(quant_min=60, breakout_window=20, volume_multiplier=1.0)),
        _preset("volume_breakout", "放量突破", "價格與成交量同時轉強，尋找有量能確認的突破。", "價格／量能", BuyPointConditions(quant_min=60, breakout_window=10, volume_multiplier=1.5)),
        _preset("institutional_turn", "法人轉買", "觀察法人籌碼由弱轉強，不把單日買超直接當訊號。", "籌碼", BuyPointConditions(quant_min=60, institutional_required=True)),
        _preset("foreign_holding", "外資持股增加", "尋找外資持股逐步增加，而非單日突然買超。", "籌碼", BuyPointConditions(quant_min=60, foreign_shareholding_change_min=0)),
        _preset("revenue_pullback", "營收成長回檔", "基本面成長仍在、股價短線回檔時提醒。", "基本面／價格", BuyPointConditions(quant_min=60, revenue_yoy_min=20, pullback_min_pct=3, pullback_max_pct=8)),
        _preset("earnings_confirmed", "財報確認型", "等獲利資料確認後觀察價格，不提前猜財報。", "基本面", BuyPointConditions(quant_min=60, eps_min=0, max_price_extension_pct=8)),
        _preset("value_growth", "低估值＋成長", "同時要求估值與成長，不因單純低本益比就提醒。", "估值／基本面", BuyPointConditions(quant_min=55, pe_min=0, pe_max=20, pb_min=0, pb_max=3, revenue_yoy_min=0, eps_min=0)),
        _preset("event_confirmed", "事件後確認", "正式事件發生後確認市場反應，不預測事件結果。", "事件", BuyPointConditions(quant_min=60, max_price_extension_pct=8)),
        _preset("resonance", "多條件共振", "同時等待價格、Quant 與籌碼共振，訊號較少但條件較完整。", "綜合", BuyPointConditions(quant_min=70, pullback_min_pct=3, pullback_max_pct=6, breakout_window=20, institutional_required=True, resonance_min_categories=3)),
    ]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _close_rows(data: BuyPointMarketData) -> list[dict[str, Any]]:
    return [row for row in data.daily if _number(row.get("close")) is not None]


def _risk_gate(strategy: BuyPointStrategy, data: BuyPointMarketData) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    flags: list[str] = []
    if strategy.risk_filters.exclude_regulatory_unknown and data.risk_data_available is None:
        missing.append("監管風險資料")
    elif strategy.risk_filters.exclude_regulatory_unknown and data.risk_data_available is False:
        missing.append("監管風險資料不可用")
    elif strategy.risk_filters.exclude_regulatory_unknown and data.regulatory_unknown is not False:
        missing.append("標的監管風險狀態")
    checks = (
        ("處置", data.disposition, strategy.risk_filters.exclude_disposition),
        ("暫停交易", data.suspended, strategy.risk_filters.exclude_suspension),
        ("下市", data.delisted, strategy.risk_filters.exclude_delisting),
        ("減資重大狀態", data.capital_reduction_critical, strategy.risk_filters.exclude_capital_reduction_critical),
        ("重大事件風險", data.severe_event_risk, strategy.risk_filters.exclude_severe_event),
    )
    for label, value, enabled in checks:
        if not enabled:
            continue
        if value is True:
            flags.append(label)
        elif value is None and data.risk_data_available is not True:
            missing.append(f"{label}風險資料")
    return flags, missing


def evaluate_buy_point(strategy: BuyPointStrategy, data: BuyPointMarketData) -> BuyPointSignal:
    """Evaluate one strategy with explicit true/false/missing semantics."""
    triggered: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    risks, risk_missing = _risk_gate(strategy, data)
    missing.extend(risk_missing)
    observed_risks = [
        label
        for label, active in (
            ("處置", data.disposition),
            ("暫停交易", data.suspended),
            ("下市", data.delisted),
            ("減資重大狀態", data.capital_reduction_critical),
            ("重大事件風險", data.severe_event_risk),
        )
        if active is True
    ]
    risk_status: Literal["clear", "unknown"] = (
        "clear"
        if data.risk_data_available is True
        and data.regulatory_unknown is False
        and not observed_risks
        else "unknown"
    )
    c = strategy.conditions
    near_failures: set[str] = set()
    category_pass: dict[str, bool] = {}

    def require(label: str, actual: Any, predicate) -> bool:
        if actual is None:
            missing.append(label)
            return False
        ok = bool(predicate(actual))
        (triggered if ok else failed).append(label)
        return ok

    if c.quant_min is not None:
        quant_ok = require(f"Quant >= {c.quant_min:g}", data.quant_score, lambda v: v >= c.quant_min)
        if data.quant_universe is False:
            failed.append("仍在 Quant universe")
            quant_ok = False
        elif data.quant_universe is None:
            missing.append("Quant universe")
            quant_ok = False
        category_pass["quant"] = quant_ok

    rows = _close_rows(data)
    closes = [_number(row.get("close")) for row in rows]
    closes = [v for v in closes if v is not None]
    price = data.price if data.price is not None else (closes[-1] if closes else None)
    recent_high = max(closes[-21:-1], default=None)
    if c.pullback_min_pct is not None or c.pullback_max_pct is not None:
        if price is None or recent_high is None or recent_high <= 0:
            missing.append("近期高點與價格")
        else:
            pullback = (recent_high - price) / recent_high * 100
            lower = c.pullback_min_pct if c.pullback_min_pct is not None else float("-inf")
            upper = c.pullback_max_pct if c.pullback_max_pct is not None else float("inf")
            ok = lower <= pullback <= upper
            pullback_label = f"回檔 {pullback:.1f}%"
            (triggered if ok else failed).append(pullback_label)
            category_pass["pullback"] = ok
            if not ok and c.approaching_distance_pct > 0:
                near = lower - c.approaching_distance_pct <= pullback <= upper + c.approaching_distance_pct
                if near:
                    triggered.append("接近回檔區")
                    near_failures.add(pullback_label)
    if c.breakout_window is not None:
        if price is None or len(closes) < c.breakout_window + 1:
            missing.append(f"{c.breakout_window}D 高點")
        else:
            prior_high = max(closes[-c.breakout_window - 1:-1])
            ok = price >= prior_high
            breakout_label = f"突破 {c.breakout_window}D 高點"
            (triggered if ok else failed).append(breakout_label)
            category_pass["breakout"] = ok
            if not ok and prior_high > 0 and (prior_high - price) / prior_high * 100 <= c.approaching_distance_pct:
                triggered.append("接近突破價")
                near_failures.add(breakout_label)
    if c.volume_multiplier is not None:
        if data.volume is None or data.volume_average_20d is None or data.volume_average_20d <= 0:
            missing.append("成交量與 20D 均量")
        else:
            require(f"成交量 >= 20D 均量 × {c.volume_multiplier:g}", data.volume, lambda v: v >= data.volume_average_20d * c.volume_multiplier)
    if c.institutional_required:
        if not data.institutional_trend:
            missing.append("法人趨勢")
            category_pass["institutional"] = False
        else:
            category_pass["institutional"] = require(
                "法人由賣轉買或淨買超改善",
                data.institutional_trend,
                lambda v: len(v) >= 2 and v[-1] > v[0],
            )
    if c.foreign_shareholding_change_min is not None:
        require(f"外資持股變化 >= {c.foreign_shareholding_change_min:g}", data.foreign_shareholding_change_20d, lambda v: v >= c.foreign_shareholding_change_min)
    if c.revenue_yoy_min is not None:
        require(f"營收 YoY >= {c.revenue_yoy_min:g}%", data.revenue_yoy, lambda v: v >= c.revenue_yoy_min)
    if c.eps_min is not None:
        require(f"EPS >= {c.eps_min:g}", data.eps, lambda v: v >= c.eps_min)
    if c.pe_min is not None or c.pe_max is not None:
        require("PE 在設定範圍", data.pe, lambda v: (c.pe_min is None or v >= c.pe_min) and (c.pe_max is None or v <= c.pe_max))
    if c.pb_min is not None or c.pb_max is not None:
        require("PB 在設定範圍", data.pb, lambda v: (c.pb_min is None or v >= c.pb_min) and (c.pb_max is None or v <= c.pb_max))
    if c.max_price_extension_pct is not None:
        require(f"價格追高幅度 <= {c.max_price_extension_pct:g}%", data.price_extension_pct, lambda v: v <= c.max_price_extension_pct)
    if strategy.id == "event_confirmed":
        require("正式事件已發生", data.positive_event, lambda v: v is True)

    if strategy.id == "resonance" and not missing and not risks:
        price_ok = category_pass.get("pullback", False) or category_pass.get("breakout", False)
        passed_categories = sum((category_pass.get("quant", False), price_ok, category_pass.get("institutional", False)))
        needed = max(1, c.resonance_min_categories)
        price_labels = [label for label in failed if label.startswith(("回檔 ", "突破 "))]
        failed = [label for label in failed if label not in price_labels]
        if price_ok:
            near_failures.difference_update(price_labels)
            triggered = [label for label in triggered if not label.startswith("接近")]
            triggered.append("價格回檔或突破成立")
        else:
            failed.append("價格回檔或突破")
        resonance_label = f"共振類別 {passed_categories}/{needed}"
        (triggered if passed_categories >= needed else failed).append(resonance_label)

    if risks:
        status: BuyPointStatus = "blocked"
        explanation = f"買點條件部分成立，但因 {', '.join(risks)} 暫不提醒。"
    elif missing:
        status = "unavailable"
        explanation = f"資料不足，暫不判定：{', '.join(dict.fromkeys(missing))}。"
    else:
        hard_fail = bool(failed)
        near = bool(near_failures)
        non_near_failures = [label for label in failed if label not in near_failures]
        if not hard_fail:
            status = "triggered" if not near else "approaching"
        else:
            status = "approaching" if near and not non_near_failures else "waiting"
        explanation = {
            "triggered": "條件已成立，提醒使用者自行研究與決定。",
            "approaching": "目前接近買點條件，尚未完全成立。",
            "waiting": "尚未符合買點條件。",
        }[status]
    return BuyPointSignal(
        strategy_id=strategy.id, symbol=data.symbol, name=data.name,
        detected_at=_now(), data_as_of=data.data_as_of, status=status,
        triggered_conditions=triggered, failed_conditions=failed + missing,
        risk_flags=list(dict.fromkeys(observed_risks)), risk_status=risk_status,
        price=price, quant_score=data.quant_score,
        explanation=explanation, freshness=data.freshness,
    )


class BuyPointStrategyStore:
    """Small atomic JSON store for user strategy definitions and assignments."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"strategies": [], "assignments": {}, "states": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {"strategies": [], "assignments": {}, "states": {}}
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("買點策略資料無法讀取") from exc

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(raw_path, self.path)
        finally:
            Path(raw_path).unlink(missing_ok=True)

    def list_custom(self) -> list[BuyPointStrategy]:
        with self._lock:
            return [BuyPointStrategy(**item) for item in self._read().get("strategies", []) if isinstance(item, dict)]

    def save(self, strategy: BuyPointStrategy) -> BuyPointStrategy:
        with self._lock:
            payload = self._read()
            rows = [item for item in payload.get("strategies", []) if item.get("id") != strategy.id]
            rows.append(strategy.model_dump())
            payload["strategies"] = rows
            self._write(payload)
        return strategy

    def delete(self, strategy_id: str) -> bool:
        with self._lock:
            payload = self._read()
            before = len(payload.get("strategies", []))
            payload["strategies"] = [item for item in payload.get("strategies", []) if item.get("id") != strategy_id]
            payload["assignments"] = {s: [x for x in ids if x != strategy_id] for s, ids in payload.get("assignments", {}).items()}
            self._write(payload)
            return before != len(payload["strategies"])

    def assign(self, symbol: str, strategy_ids: list[str]) -> list[str]:
        with self._lock:
            payload = self._read()
            payload.setdefault("assignments", {})[symbol] = list(dict.fromkeys(strategy_ids))
            self._write(payload)
            return payload["assignments"][symbol]

    def assignments(self, symbol: str | None = None) -> dict[str, list[str]] | list[str]:
        with self._lock:
            values = self._read().get("assignments", {})
            if symbol is not None:
                return list(values.get(symbol, []))
            return {str(key): list(value) for key, value in values.items()}

    def state(self, key: str) -> str | None:
        with self._lock:
            value = self._read().get("states", {}).get(key)
            if isinstance(value, dict):
                value = value.get("status")
            return str(value) if value is not None else None

    def set_state(self, key: str, state: str) -> None:
        with self._lock:
            payload = self._read()
            payload.setdefault("states", {})[key] = state
            self._write(payload)

    def last_triggered_at(self, key: str) -> float | None:
        with self._lock:
            value = self._read().get("last_triggered", {}).get(key)
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

    def mark_triggered(self, key: str, timestamp: float) -> None:
        with self._lock:
            payload = self._read()
            payload.setdefault("last_triggered", {})[key] = timestamp
            self._write(payload)


def strategy_catalog(store: BuyPointStrategyStore) -> list[BuyPointStrategy]:
    persisted = {item.id: item for item in store.list_custom()}
    catalog: list[BuyPointStrategy] = []
    for preset in builtin_presets():
        catalog.append(persisted.pop(preset.id, preset))
    catalog.extend(persisted.values())
    return catalog

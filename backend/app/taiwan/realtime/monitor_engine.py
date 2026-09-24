"""Taiwan Realtime Monitor & Alert Engine.

Evaluates configured TaiwanMonitorRules against live TaiwanRealtimeQuotes.
Features:
  - Strict Data Quality Gate (rejects stale data, daily fallbacks, delayed feeds)
  - Strict Market Status Gate (only evaluates during verified MarketStatus.OPEN)
  - Canonical Price Limits Awareness (via MarketProfileBridge & PriceLimitModel; rejects NO_LIMIT)
  - State-based Edge Deduplication (armed -> triggered -> armed)
  - Configurable Cooldown & Hysteresis
  - Batch Symbol Grouping (1 batch quote request per evaluation round)
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from app.taiwan.realtime.calendar import MarketStatus, taipei_now
from app.taiwan.realtime.models import RealtimeStatus, TaiwanRealtimeQuote
from app.taiwan.realtime.monitor_models import (
    EvaluationStatus,
    TaiwanAlertEvent,
    TaiwanAlertSeverity,
    TaiwanMonitorRule,
    TaiwanRuleType,
)
from app.taiwan.realtime.service import TaiwanRealtimeService, get_realtime_service
from app.taiwan.universe import get_security_master
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument

logger = logging.getLogger(__name__)

class TaiwanMonitorEngine:
    """Intraday Real-time Alert & Rule Evaluation Engine for Taiwan Markets."""

    def __init__(
        self,
        realtime_service: TaiwanRealtimeService | None = None,
        storage_path: Path | None = None,
        alert_handler: Callable[[TaiwanAlertEvent], None] | None = None,
    ) -> None:
        # Phase 8B-5.0.6: 省略 storage_path 时锚定到 settings.data_dir, 不再是
        # cwd-dependent 的裸相对路径(见 app/taiwan/data_root.py)。显式传入
        # storage_path(既有 deterministic test 都这样做)时行为不变。未发现
        # repo 内任何既有 monitor_rules.json(见 8B-5.0.6 报告 Data Safety),
        # 此改动不会让任何既有规则文件失联。
        if storage_path is None:
            from app.taiwan.data_root import taiwan_data_root

            storage_path = taiwan_data_root() / "monitor_rules.json"
        self.realtime_service = realtime_service or get_realtime_service()
        self.storage_path = Path(storage_path)
        self.state_path = self.storage_path.with_suffix(".state.json")
        self.alert_handler = alert_handler

        self._rules: dict[str, TaiwanMonitorRule] = {}
        self._rules_lock = threading.Lock()

        # Deduplication & Cooldown runtime states
        # dedup_key -> is_currently_triggered (bool)
        self._trigger_states: dict[str, bool] = {}
        # dedup_key -> last_fired_monotonic_timestamp (float)
        self._last_fire_time: dict[str, float] = {}
        self._state_lock = threading.RLock()
        try:
            saved_state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(saved_state, dict):
                self._trigger_states = {
                    str(key): value for key, value in saved_state.items()
                    if isinstance(key, str) and isinstance(value, bool)
                }
        except (FileNotFoundError, OSError, ValueError):
            pass

        # Load persisted rules if available
        self.load_rules()

    # ── Rule Persistence & CRUD ──────────────────────────────────

    def load_rules(self) -> int:
        """Load persistent rules from JSON storage."""
        with self._rules_lock:
            if not self.storage_path.exists():
                self._rules.clear()
                return 0
            try:
                raw_text = self.storage_path.read_text(encoding="utf-8")
                if not raw_text.strip():
                    self._rules.clear()
                    return 0
                data = json.loads(raw_text)
                loaded = {}
                for item in data:
                    rule = TaiwanMonitorRule.from_dict(item)
                    loaded[rule.rule_id] = rule
                self._rules = loaded
                logger.info("Loaded %d Taiwan monitor rules from %s", len(loaded), self.storage_path)
                return len(loaded)
            except Exception as e:
                logger.warning("Failed to load Taiwan monitor rules from %s: %s", self.storage_path, e)
                return 0

    def save_rules(self) -> None:
        """Persist in-memory rules to JSON file."""
        with self._rules_lock:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            serializable = [rule.to_dict() for rule in self._rules.values()]
            self.storage_path.write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def get_rule(self, rule_id: str) -> TaiwanMonitorRule | None:
        with self._rules_lock:
            return self._rules.get(rule_id)

    def list_rules(self) -> list[TaiwanMonitorRule]:
        with self._rules_lock:
            return sorted(list(self._rules.values()), key=lambda r: r.created_at, reverse=True)

    def add_rule(self, rule: TaiwanMonitorRule) -> TaiwanMonitorRule:
        """Validate and add/update rule."""
        self.validate_rule(rule)
        with self._rules_lock:
            self._rules[rule.rule_id] = rule
        self.save_rules()
        return rule

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> bool:
        with self._rules_lock:
            rule = self._rules.get(rule_id)
            if not rule:
                return False
            rule.enabled = enabled
            rule.updated_at = datetime.now().isoformat()
        self.save_rules()
        return True

    def delete_rule(self, rule_id: str) -> bool:
        with self._rules_lock:
            if rule_id in self._rules:
                del self._rules[rule_id]
                deleted = True
            else:
                deleted = False
        if deleted:
            self.save_rules()
            with self._state_lock:
                keys_to_del = [
                    key for key in self._trigger_states
                    if key.startswith(f"{rule_id}:") or key.startswith(f"quant:{rule_id}:")
                ]
                for k in keys_to_del:
                    self._trigger_states.pop(k, None)
                    self._last_fire_time.pop(k, None)
                self._save_trigger_states_locked()
        return deleted

    def clear_rules(self) -> None:
        with self._rules_lock:
            self._rules.clear()
        self.save_rules()
        with self._state_lock:
            self._trigger_states.clear()
            self._last_fire_time.clear()
            self._save_trigger_states_locked()

    def _save_trigger_states_locked(self) -> None:
        """Persist edge state so a held condition does not fire again after restart."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._trigger_states), encoding="utf-8")
        temporary.replace(self.state_path)

    def evaluate_quant_top10(
        self, signals: list[dict], session: str, *, available: bool = True,
        persist_events: Callable[[list[dict]], None] | None = None,
    ) -> list[dict]:
        """Evaluate symbol-specific Top 10 entry/exit rules from a verified live run."""
        if not available:
            return []
        ranked = {
            str(signal["symbol"]): signal
            for signal in signals
            if isinstance(signal, dict)
            and isinstance(signal.get("symbol"), str)
            and isinstance(signal.get("rank"), int)
            and signal["rank"] <= 10
        }
        with self._rules_lock:
            rules = [
                rule for rule in self._rules.values()
                if rule.enabled and rule.rule_type in (
                    TaiwanRuleType.QUANT_TOP10_ENTER, TaiwanRuleType.QUANT_TOP10_EXIT,
                )
            ]

        events: list[dict] = []
        with self._state_lock:
            prior_states = dict(self._trigger_states)
            changed_keys: set[str] = set()
            changed = False
            for rule in rules:
                key = f"quant:{rule.rule_id}:{rule.symbol}"
                previous = self._trigger_states.get(key)
                signal = ranked.get(rule.symbol)
                current = signal is not None
                is_entry = rule.rule_type == TaiwanRuleType.QUANT_TOP10_ENTER
                should_fire = (
                    current and (previous is False or previous is None)
                    if is_entry else previous is True and not current
                )
                if previous != current:
                    self._trigger_states[key] = current
                    changed = True
                    changed_keys.add(key)
                if not should_fire:
                    continue

                instrument = get_security_master().get_instrument(rule.symbol)
                rank = signal.get("rank") if signal else None
                score = signal.get("score") if signal else None
                action = "進入" if is_entry else "離開"
                detail = f", 目前排名第 {rank} 名" if rank is not None and is_entry else ""
                if isinstance(score, (int, float)) and is_entry:
                    detail += f", Quant 分數 {score * 100:.1f}%"
                stock_name = instrument.name if instrument else rule.symbol
                events.append({
                    "alert_id": f"tw_alert_{uuid.uuid4().hex[:12]}",
                    "ts": int(time.time() * 1000),
                    "rule_id": rule.rule_id,
                    "rule_name": rule.name,
                    "source": "quant",
                    "type": "quant_top10_enter" if is_entry else "quant_top10_exit",
                    "symbol": rule.symbol,
                    "name": stock_name,
                    "message": f"{stock_name}{action}今日 Live Quant Top 10{detail}",
                    "price": None,
                    "change_pct": None,
                    "signals": [],
                    "severity": "info",
                    "conditions": [],
                    "quant_status": action,
                    "quant_rank": rank,
                    "quant_score": score,
                    "quant_session": session,
                })
            try:
                if events and persist_events is not None:
                    persist_events(events)
                if changed:
                    self._save_trigger_states_locked()
            except Exception:
                for key in changed_keys:
                    if key in prior_states:
                        self._trigger_states[key] = prior_states[key]
                    else:
                        self._trigger_states.pop(key, None)
                raise
        return events

    # ── Rule Validation & Constraints ────────────────────────────

    def validate_rule(self, rule: TaiwanMonitorRule) -> None:
        """Validate rule parameters and instrument support."""
        if not rule.rule_id or len(rule.rule_id) > 64:
            raise ValueError(f"Invalid rule_id: {rule.rule_id!r}")
        if not rule.name or not rule.name.strip():
            raise ValueError("Rule name must not be empty")

        # Symbol validation via Security Master
        sec_master = get_security_master()
        inst: TaiwanInstrument | None = sec_master.get_instrument(rule.symbol)
        if inst is None:
            raise ValueError(f"Symbol {rule.symbol} does not exist in Security Master")
        if not inst.is_supported:
            raise ValueError(
                f"Symbol {rule.symbol} is not a supported trading asset "
                f"(type: {inst.instrument_type}, listing_status: {inst.listing_status})"
            )

        # Rule parameters validation
        rtype = rule.rule_type if isinstance(rule.rule_type, TaiwanRuleType) else TaiwanRuleType(rule.rule_type)
        if rtype in (TaiwanRuleType.PRICE_ABOVE, TaiwanRuleType.PRICE_BELOW):
            if rule.threshold <= 0:
                raise ValueError(f"{rtype.value} threshold must be strictly positive (got {rule.threshold})")
        elif rtype in (TaiwanRuleType.VOLUME_ABOVE,):
            if rule.threshold <= 0:
                raise ValueError(f"volume_above threshold must be positive shares (got {rule.threshold})")
        elif rtype in (TaiwanRuleType.VOLUME_SPIKE,):
            if rule.threshold <= 0:
                raise ValueError(f"volume_spike threshold multiple must be positive (got {rule.threshold})")
            if rule.reference_volume is None or rule.reference_volume <= 0:
                raise ValueError("volume_spike requires a strictly positive reference_volume in shares")
        elif rtype in (TaiwanRuleType.NEAR_UPPER_LIMIT, TaiwanRuleType.NEAR_LOWER_LIMIT):
            if rule.threshold <= 0 or rule.threshold > 100:
                raise ValueError(f"{rtype.value} distance_pct threshold must be between 0 and 100% (got {rule.threshold})")
            # Verify instrument is not NO_LIMIT
            limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
            if limit_pct is None:
                raise ValueError(
                    f"Symbol {rule.symbol} ({inst.name}) has NO_LIMIT trading rules; "
                    f"{rtype.value} is not applicable"
                )

        if rule.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")

    # ── Evaluation Engine Core ───────────────────────────────────

    def evaluate_all(
        self,
        force_quotes: dict[str, TaiwanRealtimeQuote] | None = None,
        now_mono: float | None = None,
        persist_events: Callable[[list[TaiwanAlertEvent]], None] | None = None,
    ) -> list[TaiwanAlertEvent]:
        """Evaluate rules and persist emitted alerts before committing crossing state."""
        with self._rules_lock:
            active_rules = [r for r in self._rules.values() if r.enabled]

        if not active_rules:
            return []

        symbols = list({r.symbol for r in active_rules})
        if force_quotes is not None:
            quotes = force_quotes
        else:
            # Batch fetch from Realtime Service (only unique symbols requested)
            quotes = self.realtime_service.get_quotes(symbols)

        alerts: list[TaiwanAlertEvent] = []
        cur_mono = time.monotonic() if now_mono is None else now_mono

        with self._state_lock:
            prior_states = dict(self._trigger_states)
            prior_fire_times = dict(self._last_fire_time)
            try:
                for rule in active_rules:
                    quote = quotes.get(rule.symbol)
                    alert, _status, _reason = self.evaluate_single_rule(
                        rule, quote, now_mono=cur_mono, persist_state=False,
                    )
                    if alert:
                        alerts.append(alert)

                if alerts and persist_events is not None:
                    persist_events(alerts)
                if (self._trigger_states != prior_states
                        or self._last_fire_time != prior_fire_times):
                    self._save_trigger_states_locked()
            except Exception:
                self._trigger_states.clear()
                self._trigger_states.update(prior_states)
                self._last_fire_time.clear()
                self._last_fire_time.update(prior_fire_times)
                raise

        if self.alert_handler:
            for alert in alerts:
                try:
                    self.alert_handler(alert)
                except Exception as ex:
                    logger.warning("Alert handler error for %s: %s", alert.alert_id, ex)

        return alerts

    def evaluate_single_rule(
        self,
        rule: TaiwanMonitorRule,
        quote: TaiwanRealtimeQuote | None,
        now_mono: float | None = None,
        *,
        persist_state: bool = True,
    ) -> tuple[TaiwanAlertEvent | None, EvaluationStatus, str]:
        """Evaluate one rule against a quote adhering strictly to quality and status gates.

        Returns:
            (TaiwanAlertEvent or None, EvaluationStatus, reason_string)
        """
        cur_mono = time.monotonic() if now_mono is None else now_mono
        now_dt = taipei_now()

        # Gate 1: Quote existence
        if quote is None:
            return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "No quote available"

        # Gate 2: Market Status Gate (Only regular verified OPEN session)
        if quote.market_status == MarketStatus.SCHEDULED_OPEN_UNVERIFIED.value:
            return None, EvaluationStatus.SKIPPED_MARKET_UNVERIFIED, "Market session is scheduled but unverified"
        if quote.market_status != MarketStatus.OPEN.value:
            return None, EvaluationStatus.SKIPPED_MARKET_CLOSED, f"Market session is not open ({quote.market_status})"

        # Gate 3: Data Quality Gate (Stale check, Daily fallback, Delayed check)
        meta = quote.source_meta
        if meta.is_stale:
            return None, EvaluationStatus.SKIPPED_STALE_DATA, "Quote is stale"
        if meta.status == RealtimeStatus.DAILY_FALLBACK.value or meta.source_type == "local_store":
            return None, EvaluationStatus.SKIPPED_DAILY_FALLBACK, "Quote fell back to daily cached storage"
        if meta.freshness_class in ("delayed_15m", "unknown") or "delayed" in meta.freshness_class:
            return None, EvaluationStatus.SKIPPED_DELAYED_SOURCE, f"Quote feed is delayed ({meta.freshness_class})"

        # Gate 4: Price Limit Applicability & Calculation
        sec_master = get_security_master()
        inst = sec_master.get_instrument(rule.symbol)

        rtype = rule.rule_type if isinstance(rule.rule_type, TaiwanRuleType) else TaiwanRuleType(rule.rule_type)

        # Evaluate Condition & Calculate Distance
        trigger_value: float | None = None
        field_name = ""
        is_condition_met = False
        rearm_threshold: float | None = None

        if rtype == TaiwanRuleType.PRICE_ABOVE:
            field_name = "last_price"
            if quote.last_price is None:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "last_price is None"
            trigger_value = quote.last_price
            is_condition_met = trigger_value >= rule.threshold
            if rule.hysteresis is not None:
                rearm_threshold = rule.threshold - rule.hysteresis

        elif rtype == TaiwanRuleType.PRICE_BELOW:
            field_name = "last_price"
            if quote.last_price is None:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "last_price is None"
            trigger_value = quote.last_price
            is_condition_met = trigger_value <= rule.threshold
            if rule.hysteresis is not None:
                rearm_threshold = rule.threshold + rule.hysteresis

        elif rtype == TaiwanRuleType.CHANGE_PCT_ABOVE:
            field_name = "change_pct"
            if quote.change_pct is None:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "change_pct is None"
            trigger_value = round(quote.change_pct, 4)
            is_condition_met = trigger_value >= rule.threshold
            if rule.hysteresis is not None:
                rearm_threshold = rule.threshold - rule.hysteresis

        elif rtype == TaiwanRuleType.CHANGE_PCT_BELOW:
            field_name = "change_pct"
            if quote.change_pct is None:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "change_pct is None"
            trigger_value = round(quote.change_pct, 4)
            is_condition_met = trigger_value <= rule.threshold
            if rule.hysteresis is not None:
                rearm_threshold = rule.threshold + rule.hysteresis


        elif rtype == TaiwanRuleType.VOLUME_ABOVE:
            field_name = "volume"
            if quote.volume is None:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "volume is None"
            trigger_value = float(quote.volume)
            is_condition_met = trigger_value >= rule.threshold

        elif rtype == TaiwanRuleType.VOLUME_SPIKE:
            field_name = "volume_multiple"
            if quote.volume is None or not rule.reference_volume:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "volume or reference_volume missing"
            multiple = round(quote.volume / rule.reference_volume, 2)
            trigger_value = multiple
            is_condition_met = trigger_value >= rule.threshold

        elif rtype in (TaiwanRuleType.NEAR_UPPER_LIMIT, TaiwanRuleType.NEAR_LOWER_LIMIT):
            field_name = "distance_to_limit_pct"
            if inst is None or quote.last_price is None or quote.prev_close is None:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "Instrument or price data missing"

            limit_pct = MarketProfileBridge.get_price_limit_pct(inst)
            if limit_pct is None:
                return None, EvaluationStatus.NOT_APPLICABLE, "Instrument is NO_LIMIT; near limit rule not applicable"

            limit_up, limit_down = MarketProfileBridge.calc_limits(quote.prev_close, inst)
            if rtype == TaiwanRuleType.NEAR_UPPER_LIMIT:
                if limit_up is None:
                    return None, EvaluationStatus.NOT_APPLICABLE, "Upper limit not applicable"
                # Distance percentage: (limit_up - last_price) / limit_up * 100
                distance_pct = round(max(0.0, (limit_up - quote.last_price) / limit_up) * 100.0, 2)
                trigger_value = distance_pct
                # Alert when price is within threshold % of upper limit
                is_condition_met = distance_pct <= rule.threshold
            else:
                if limit_down is None:
                    return None, EvaluationStatus.NOT_APPLICABLE, "Lower limit not applicable"
                # Distance percentage: (last_price - limit_down) / limit_down * 100
                distance_pct = round(max(0.0, (quote.last_price - limit_down) / limit_down) * 100.0, 2)
                trigger_value = distance_pct
                is_condition_met = distance_pct <= rule.threshold

        elif rtype in (TaiwanRuleType.QUANT_TOP10_ENTER, TaiwanRuleType.QUANT_TOP10_EXIT):
            return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "Quant rank is evaluated from a verified live snapshot"

        # Deduplication & Cooldown Gate
        dedup_key = f"{rule.rule_id}:{rule.symbol}:{rtype.value}"

        with self._state_lock:
            prev_triggered = self._trigger_states.get(dedup_key, False)
            # 尚未触发过用 None 表示: time.monotonic() 的原点是任意的 (Linux 上是开机时间),
            # 若用 0.0 当哨兵, 刚开机/容器刚启动时 cur_mono - 0.0 会小于 cooldown_seconds,
            # 所有规则都会被误判成 cooldown 中而永不触发。
            last_fire = self._last_fire_time.get(dedup_key)

            # Re-arm state check with optional hysteresis
            if not is_condition_met:
                should_rearm = False
                if rearm_threshold is not None:
                    # Check if price moved sufficiently past rearm_threshold
                    should_rearm = (
                        rtype in (TaiwanRuleType.PRICE_ABOVE, TaiwanRuleType.CHANGE_PCT_ABOVE)
                        and trigger_value < rearm_threshold
                    ) or (
                        rtype in (TaiwanRuleType.PRICE_BELOW, TaiwanRuleType.CHANGE_PCT_BELOW)
                        and trigger_value > rearm_threshold
                    )
                else:
                    should_rearm = prev_triggered

                if prev_triggered and should_rearm:
                    self._trigger_states[dedup_key] = False
                    if persist_state:
                        try:
                            self._save_trigger_states_locked()
                        except Exception:
                            self._trigger_states[dedup_key] = True
                            raise

                return None, EvaluationStatus.NOT_TRIGGERED, "Condition not met"

            # Condition IS met here
            # 1. Edge-triggered check: if previously triggered and not re-armed, suppress
            if prev_triggered:
                return None, EvaluationStatus.DEDUP_SUPPRESSED, "Duplicate suppressed (already triggered)"

            # 2. Cooldown check
            if last_fire is not None and (cur_mono - last_fire) < rule.cooldown_seconds:
                return None, EvaluationStatus.COOLDOWN_ACTIVE, f"In cooldown ({int(rule.cooldown_seconds - (cur_mono - last_fire))}s left)"

            # Mark state as triggered and record fire time
            previous_state = self._trigger_states.get(dedup_key)
            previous_fire_time = self._last_fire_time.get(dedup_key)
            self._trigger_states[dedup_key] = True
            self._last_fire_time[dedup_key] = cur_mono
            if persist_state:
                try:
                    self._save_trigger_states_locked()
                except Exception:
                    if previous_state is None:
                        self._trigger_states.pop(dedup_key, None)
                    else:
                        self._trigger_states[dedup_key] = previous_state
                    if previous_fire_time is None:
                        self._last_fire_time.pop(dedup_key, None)
                    else:
                        self._last_fire_time[dedup_key] = previous_fire_time
                    raise

        # Generate Explainable Alert Message
        inst_name = inst.name if inst else quote.name
        message = self._build_message(rule, quote, inst_name, trigger_value)

        sev = rule.severity.value if isinstance(rule.severity, TaiwanAlertSeverity) else str(rule.severity)

        alert = TaiwanAlertEvent(
            alert_id=f"tw_alert_{uuid.uuid4().hex[:12]}",
            rule_id=rule.rule_id,
            rule_name=rule.name,
            symbol=rule.symbol,
            name=inst_name,
            rule_type=rtype.value,
            triggered_at=now_dt,
            quote_time=quote.quote_time,
            trigger_value=trigger_value,
            threshold=rule.threshold,
            message=message,
            source=meta.source,
            source_status=meta.status,
            market_status=quote.market_status,
            severity=sev,
            field_name=field_name,
            dedup_key=dedup_key,
        )

        return alert, EvaluationStatus.TRIGGERED, "Alert triggered successfully"

    def _build_message(
        self,
        rule: TaiwanMonitorRule,
        quote: TaiwanRealtimeQuote,
        name: str,
        value: float,
    ) -> str:
        rtype = rule.rule_type if isinstance(rule.rule_type, TaiwanRuleType) else TaiwanRuleType(rule.rule_type)

        if rtype == TaiwanRuleType.PRICE_ABOVE:
            return f"{name} ({rule.symbol}) 現價 {value:.2f} 已突破設定閾值 {rule.threshold:.2f}"
        elif rtype == TaiwanRuleType.PRICE_BELOW:
            return f"{name} ({rule.symbol}) 現價 {value:.2f} 已跌破設定閾值 {rule.threshold:.2f}"
        elif rtype == TaiwanRuleType.CHANGE_PCT_ABOVE:
            return f"{name} ({rule.symbol}) 漲跌幅 {value:+.2f}% 已超過上漲閾值 {rule.threshold:+.2f}%"
        elif rtype == TaiwanRuleType.CHANGE_PCT_BELOW:
            return f"{name} ({rule.symbol}) 漲跌幅 {value:+.2f}% 已跌破設定閾值 {rule.threshold:+.2f}%"
        elif rtype == TaiwanRuleType.VOLUME_ABOVE:
            return f"{name} ({rule.symbol}) 累積成交量 {int(value):,} 股 已達設定量能門檻 {int(rule.threshold):,} 股"
        elif rtype == TaiwanRuleType.VOLUME_SPIKE:
            return f"{name} ({rule.symbol}) 目前量能為基準量能之 {value:.1f} 倍 (設定: {rule.threshold:.1f} 倍)"
        elif rtype == TaiwanRuleType.NEAR_UPPER_LIMIT:
            return f"{name} ({rule.symbol}) 現價 {quote.last_price:.2f} 距漲停價僅 {value:.1f}% (設定 ≤ {rule.threshold:.1f}%)"
        elif rtype == TaiwanRuleType.NEAR_LOWER_LIMIT:
            return f"{name} ({rule.symbol}) 現價 {quote.last_price:.2f} 距跌停價僅 {value:.1f}% (設定 ≤ {rule.threshold:.1f}%)"
        return f"{name} ({rule.symbol}) 觸發監控規則 {rule.name}"


_default_engine: TaiwanMonitorEngine | None = None


def get_monitor_engine() -> TaiwanMonitorEngine:
    """Get or instantiate default TaiwanMonitorEngine singleton."""
    global _default_engine
    if _default_engine is None:
        _default_engine = TaiwanMonitorEngine()
    return _default_engine

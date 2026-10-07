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
import math
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

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

# Auto Watch rule name -> the live_plan_status() value that fires it.
_PLAN_ROLE_STATUS = {"進入承接區": "in_zone", "突破": "breakout", "跌破失效位": "below_stop"}

if TYPE_CHECKING:
    from app.taiwan.beginner_selection import BeginnerCandidate


def _watched_plan_symbols() -> frozenset[str]:
    """Current Taiwan watchlist. A plan rule for any other symbol must not fire,
    even before the background re-sync has removed it."""
    from app.taiwan import auto_watch

    try:
        return auto_watch.watched_symbols()
    except Exception as exc:
        logger.warning("Auto Watch watchlist unavailable: %s", type(exc).__name__)
        return frozenset()  # fail closed: no plan alert without a readable watchlist


def quote_quality_gate(quote: TaiwanRealtimeQuote) -> tuple[EvaluationStatus, str] | None:
    """Return the skip status when a quote must not drive a live decision.

    Alerts and the beginner entry radar share this gate, so a quote that cannot
    trigger an alert also cannot show a live entry status.
    """
    # Market Status Gate (only the regular verified OPEN session)
    if quote.market_status == MarketStatus.SCHEDULED_OPEN_UNVERIFIED.value:
        return EvaluationStatus.SKIPPED_MARKET_UNVERIFIED, "Market session is scheduled but unverified"
    if quote.market_status != MarketStatus.OPEN.value:
        return EvaluationStatus.SKIPPED_MARKET_CLOSED, f"Market session is not open ({quote.market_status})"

    # Data Quality Gate (stale check, daily fallback, delayed check)
    meta = quote.source_meta
    if meta.is_stale:
        return EvaluationStatus.SKIPPED_STALE_DATA, "Quote is stale"
    if meta.status == RealtimeStatus.DAILY_FALLBACK.value or meta.source_type == "local_store":
        return EvaluationStatus.SKIPPED_DAILY_FALLBACK, "Quote fell back to daily cached storage"
    if meta.freshness_class in ("delayed_15m", "unknown") or "delayed" in meta.freshness_class:
        return EvaluationStatus.SKIPPED_DELAYED_SOURCE, f"Quote feed is delayed ({meta.freshness_class})"
    return None


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
        self._rules_lock = threading.RLock()

        # Deduplication & Cooldown runtime states
        # dedup_key -> is_currently_triggered (bool)
        self._trigger_states: dict[str, bool] = {}
        # dedup_key -> last_fired_monotonic_timestamp (float)
        self._last_fire_time: dict[str, float] = {}
        self._state_lock = threading.RLock()
        self._state_dirty = False
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
            self._save_rules_locked(self._rules)

    def _save_rules_locked(self, rules: dict[str, TaiwanMonitorRule]) -> None:
        """Publish one complete rule snapshot; callers hold the rule lock."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps([rule.to_dict() for rule in rules.values()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.storage_path)

    def sync_plan_rules(self, plans: Iterable[BeginnerCandidate]) -> dict:
        """Atomically replace managed rules, preserving all manual rule values."""
        prepared: dict[str, TaiwanMonitorRule] = {}
        skipped: list[dict[str, str]] = []
        for candidate in plans:
            plan = candidate.trade_plan
            if plan is None:
                skipped.append({"symbol": candidate.symbol, "reason": candidate.plan_unavailable_reason or "目前沒有可用的計畫價位"})
                continue
            try:
                if not plan.plan_identity:
                    raise ValueError("計畫缺少識別資訊")
                if plan.entry_semantics == "pullback_limit":
                    if (plan.entry_zone_low is None or plan.entry_zone_high is None
                            or not math.isfinite(plan.entry_zone_low)
                            or plan.entry_zone_low <= 0
                            or plan.entry_zone_low > plan.entry_zone_high):
                        raise ValueError("承接區價位不完整")
                    entry_type, entry_price, entry_name = TaiwanRuleType.PRICE_BELOW, plan.entry_zone_high, "進入承接區"
                elif plan.entry_semantics == "breakout_stop":
                    entry_type, entry_price, entry_name = TaiwanRuleType.PRICE_ABOVE, plan.breakout_trigger, "突破"
                else:
                    raise ValueError("計畫進場方式不支援")
                if (entry_price is None or not math.isfinite(entry_price) or entry_price <= 0
                        or not math.isfinite(plan.stop_price) or plan.stop_price <= 0
                        or plan.stop_price >= entry_price):
                    raise ValueError("計畫價位不完整或失效位不低於進場價")
                instrument = get_security_master().get_instrument(candidate.symbol)
                if instrument is None or instrument.instrument_type != "stock":
                    raise ValueError("僅支援已確認的台股個股")
                pair = []
                for role, name, rtype, threshold, severity, channels in (
                    ("entry", entry_name, entry_type, entry_price, "warning", ["telegram"]),
                    ("stop", "跌破失效位", TaiwanRuleType.PRICE_BELOW, plan.stop_price, "critical", ["telegram", "line"]),
                ):
                    # Stable symbol/role IDs preserve session dedup across plan revisions.
                    rule = TaiwanMonitorRule(
                        rule_id=f"tw_plan_{uuid.uuid5(uuid.NAMESPACE_URL, candidate.symbol + ':' + role).hex}",
                        name=name, symbol=candidate.symbol, rule_type=rtype, threshold=threshold,
                        cooldown_seconds=6 * 3600, severity=severity, notify_channels=channels,
                        source="trade_plan", plan_identity=plan.plan_identity,
                        plan_as_of=plan.evidence_as_of, plan_levels=plan.model_copy(deep=True),
                    )
                    self.validate_rule(rule)
                    pair.append(rule)
                prepared.update((rule.rule_id, rule) for rule in pair)
            except ValueError:
                skipped.append({"symbol": candidate.symbol, "reason": "計畫價位或個股資料不完整，無法建立提醒"})  # noqa: RUF001

        # Evaluate and replace under the same lock order. A failed disk publication
        # leaves the old in-memory snapshot and session state untouched.
        with self._rules_lock, self._state_lock:
            manual = {key: rule for key, rule in self._rules.items() if rule.source == "manual"}
            if manual.keys() & prepared.keys():
                raise ValueError("Auto Watch rule ID conflicts with a manual rule")
            removed = sum(rule.source == "trade_plan" for rule in self._rules.values())
            replacement = {**manual, **prepared}
            for rule_id, rule in prepared.items():
                # Stable IDs carry the user's on/off choice across re-syncs.
                if (previous := self._rules.get(rule_id)) is not None:
                    rule.enabled = previous.enabled
            self._save_rules_locked(replacement)
            self._rules = replacement
        return {"created": len(prepared), "removed": removed, "skipped": skipped}

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

    def seed_quant_exit_rule(
        self, rule: TaiwanMonitorRule, signals: list[dict], *, force: bool = False,
    ) -> bool:
        """Baseline a new exit rule from an already audited snapshot without alerting."""
        if rule.rule_type != TaiwanRuleType.QUANT_TOP10_EXIT:
            return False
        ranked_symbols = {
            str(signal["symbol"])
            for signal in signals
            if isinstance(signal, dict)
            and isinstance(signal.get("symbol"), str)
            and isinstance(signal.get("rank"), int)
            and signal["rank"] <= 10
        }
        key = f"quant:{rule.rule_id}:{rule.symbol}"
        with self._state_lock:
            if key in self._trigger_states and not force:
                return False
            previous = self._trigger_states.get(key)
            had_previous = key in self._trigger_states
            prior_dirty = self._state_dirty
            self._trigger_states[key] = rule.symbol in ranked_symbols
            try:
                self._save_trigger_states_locked()
            except Exception as exc:
                if had_previous:
                    self._trigger_states[key] = previous
                else:
                    self._trigger_states.pop(key, None)
                self._state_dirty = prior_dirty
                raise OSError("Quant exit baseline could not be persisted") from exc
            else:
                self._state_dirty = False
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
        persist_events: Callable[[list[dict]], list[str] | None] | None = None,
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
        triggered_at = taipei_now()
        with self._state_lock:
            prior_states = dict(self._trigger_states)
            prior_dirty = self._state_dirty
            changed_keys: set[str] = set()
            changed = False
            events_persisted = False
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
                stable_event_key = ":".join((
                    rule.rule_id, rule.symbol, rule.rule_type.value, session,
                ))
                events.append({
                    "alert_id": f"tw_quant_{uuid.uuid5(uuid.NAMESPACE_URL, stable_event_key).hex}",
                    "triggered_at": triggered_at.isoformat(),
                    "ts": int(triggered_at.timestamp() * 1000),
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
                    "severity": (
                        rule.severity.value
                        if isinstance(rule.severity, TaiwanAlertSeverity)
                        else str(rule.severity)
                    ),
                    "conditions": [],
                    "quant_status": action,
                    "quant_rank": rank,
                    "quant_score": score,
                    "quant_session": session,
                })
            try:
                if events and persist_events is not None:
                    persisted_ids = persist_events(events)
                    events_persisted = True
                    if isinstance(persisted_ids, list):
                        events = [event for event in events if event["alert_id"] in persisted_ids]
                if changed or self._state_dirty:
                    try:
                        self._save_trigger_states_locked()
                    except Exception as exc:
                        if events_persisted:
                            self._state_dirty = True
                            logger.warning(
                                "Quant alert persisted but crossing state save failed: %s", exc,
                            )
                        elif prior_dirty:
                            for key in changed_keys:
                                if key in prior_states:
                                    self._trigger_states[key] = prior_states[key]
                                else:
                                    self._trigger_states.pop(key, None)
                            self._state_dirty = True
                            logger.warning("Quant crossing state retry failed: %s", exc)
                        else:
                            raise
                    else:
                        self._state_dirty = False
            except Exception:
                if not events_persisted:
                    for key in changed_keys:
                        if key in prior_states:
                            self._trigger_states[key] = prior_states[key]
                        else:
                            self._trigger_states.pop(key, None)
                    self._state_dirty = prior_dirty
                else:
                    self._state_dirty = True
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
        if (rtype in (TaiwanRuleType.QUANT_TOP10_ENTER, TaiwanRuleType.QUANT_TOP10_EXIT)
                and inst.instrument_type != "stock"):
            raise ValueError(
                f"Quant Top 10 reminders require a stock symbol; {rule.symbol} is {inst.instrument_type}"
            )
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
            active_rules = [
                r for r in self._rules.values()
                if r.enabled and r.rule_type not in (
                    TaiwanRuleType.QUANT_TOP10_ENTER, TaiwanRuleType.QUANT_TOP10_EXIT,
                )
            ]

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
        # Read outside the locks (disk I/O); only plan rules depend on it.
        watched = _watched_plan_symbols() if any(r.source == "trade_plan" for r in active_rules) else frozenset()

        with self._rules_lock, self._state_lock:
            prior_states = dict(self._trigger_states)
            prior_fire_times = dict(self._last_fire_time)
            prior_dirty = self._state_dirty
            events_persisted = False
            try:
                for rule in active_rules:
                    # Rules can be replaced while the batched quote request is running.
                    if self._rules.get(rule.rule_id) is not rule or not rule.enabled:
                        continue
                    if rule.source == "trade_plan" and rule.symbol not in watched:
                        continue
                    quote = quotes.get(rule.symbol)
                    alert, _status, _reason = self._evaluate_single_rule_locked(
                        rule, quote, now_mono=cur_mono, persist_state=False,
                    )
                    if alert:
                        alerts.append(alert)

                if alerts and persist_events is not None:
                    persist_events(alerts)
                    events_persisted = True
                if (self._trigger_states != prior_states
                        or self._last_fire_time != prior_fire_times
                        or self._state_dirty):
                    try:
                        self._save_trigger_states_locked()
                    except Exception as ex:
                        if events_persisted:
                            self._state_dirty = True
                            logger.warning(
                                "Taiwan alert persisted but crossing state save failed: %s", ex,
                            )
                        elif prior_dirty:
                            self._trigger_states.clear()
                            self._trigger_states.update(prior_states)
                            self._last_fire_time.clear()
                            self._last_fire_time.update(prior_fire_times)
                            self._state_dirty = True
                            logger.warning("Taiwan crossing state retry failed: %s", ex)
                        else:
                            raise
                    else:
                        self._state_dirty = False
            except Exception:
                if not events_persisted:
                    self._trigger_states.clear()
                    self._trigger_states.update(prior_states)
                    self._last_fire_time.clear()
                    self._last_fire_time.update(prior_fire_times)
                    self._state_dirty = prior_dirty
                else:
                    self._state_dirty = True
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
        if rule.source == "trade_plan" and rule.symbol not in _watched_plan_symbols():
            return None, EvaluationStatus.NOT_APPLICABLE, "Symbol is no longer in the watchlist"
        with self._rules_lock, self._state_lock:
            if rule.source == "trade_plan" and self._rules.get(rule.rule_id) is not rule:
                return None, EvaluationStatus.SKIPPED_STALE_DATA, "Plan rule has been replaced"
            return self._evaluate_single_rule_locked(rule, quote, now_mono, persist_state=persist_state)

    def _evaluate_single_rule_locked(
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

        # Gates 2-3: Market Status and Data Quality
        blocked = quote_quality_gate(quote)
        if blocked is not None:
            return None, *blocked

        if rule.source == "trade_plan":
            try:
                from app.taiwan.daily_update import resolve_target_latest_trading_date

                target = resolve_target_latest_trading_date(as_of_dt=now_dt).isoformat()
            except Exception:
                return None, EvaluationStatus.SKIPPED_STALE_DATA, "Latest completed session unavailable"
            if rule.plan_as_of != target:
                return None, EvaluationStatus.SKIPPED_STALE_DATA, "Plan is not from the latest completed session"
            if (rule.plan_levels is None or rule.plan_identity != rule.plan_levels.plan_identity
                    or rule.plan_as_of != rule.plan_levels.evidence_as_of):
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "Frozen plan metadata unavailable"
            if quote.last_price is None or not math.isfinite(quote.last_price) or quote.last_price <= 0:
                return None, EvaluationStatus.SKIPPED_MISSING_FIELD, "Live price unavailable"

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

        if rule.source == "trade_plan":
            # The entry radar's price classification decides, so the entry alert needs
            # the frozen zone (not just "below the top") and never fires with the stop.
            from app.taiwan.beginner_radar import live_plan_status

            wanted = _PLAN_ROLE_STATUS.get(rule.name)
            is_condition_met = (wanted is not None and rule.plan_levels is not None
                                and live_plan_status(rule.plan_levels, quote.last_price) == wanted)

        # Deduplication & Cooldown Gate
        dedup_key = f"{rule.rule_id}:{rule.symbol}:{rtype.value}"
        if rule.source == "trade_plan":
            # Persist a session latch rather than a monotonic timestamp, which
            # cannot survive restart. Entry/stop roles each fire at most once.
            dedup_key = f"{rule.rule_id}:{rule.symbol}:{now_dt.date().isoformat()}"

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

                if prev_triggered and should_rearm and rule.source != "trade_plan":
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
            source=quote.source_meta.source,
            source_status=quote.source_meta.status,
            market_status=quote.market_status,
            severity=sev,
            field_name=field_name,
            dedup_key=dedup_key,
            notify_channels=tuple(rule.notify_channels),
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

        if rule.source == "trade_plan" and rule.plan_levels is not None:
            plan = rule.plan_levels

            def price(number: float) -> str:
                return f"{number:,.2f}".rstrip("0").rstrip(".")

            if rule.name == "進入承接區":
                detail = f"進入承接區 {price(plan.entry_zone_low)}～{price(plan.entry_zone_high)}"  # noqa: RUF001
            elif rule.name == "突破":
                detail = f"突破 {price(plan.breakout_trigger)}"
            else:
                detail = f"跌破失效位 {price(plan.stop_price)}"
            code = rule.symbol.split(".", 1)[0]
            return f"{code} {name} {detail}，現價 {price(value)}；跌破 {price(plan.stop_price)} 理由失效。（觀察提醒，不是下單）"  # noqa: RUF001

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

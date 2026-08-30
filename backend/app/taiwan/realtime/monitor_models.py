"""Taiwan Realtime Monitor & Alert Engine Models.

Defines:
  - TaiwanRuleType: Canonical rule types for Taiwan intraday monitoring.
  - EvaluationStatus: Status of rule evaluation (triggered, normal, skipped due to data gate, not applicable).
  - TaiwanMonitorRule: Persistent and configurable rule model.
  - TaiwanAlertEvent: Emitted alert record with explainability and dedup key.
  - TaiwanAlertSeverity: Deterministic alert severity tiers (INFO, WARNING, CRITICAL).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import re
from typing import Any

from app.taiwan.symbol import parse_symbol

RULE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class TaiwanRuleType(str, Enum):
    """Supported rule types for Taiwan market monitoring."""
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    CHANGE_PCT_ABOVE = "change_pct_above"
    CHANGE_PCT_BELOW = "change_pct_below"
    VOLUME_ABOVE = "volume_above"
    VOLUME_SPIKE = "volume_spike"
    NEAR_UPPER_LIMIT = "near_upper_limit"
    NEAR_LOWER_LIMIT = "near_lower_limit"


class TaiwanAlertSeverity(str, Enum):
    """Deterministic alert severity classification."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class EvaluationStatus(str, Enum):
    """Detailed evaluation result classification for explainability."""
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    COOLDOWN_ACTIVE = "cooldown_active"
    DEDUP_SUPPRESSED = "dedup_suppressed"
    SKIPPED_MARKET_UNVERIFIED = "skipped_market_unverified"
    SKIPPED_MARKET_CLOSED = "skipped_market_closed"
    SKIPPED_STALE_DATA = "skipped_stale_data"
    SKIPPED_DAILY_FALLBACK = "skipped_daily_fallback"
    SKIPPED_DELAYED_SOURCE = "skipped_delayed_source"
    SKIPPED_MISSING_FIELD = "skipped_missing_field"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class TaiwanMonitorRule:
    """Taiwan real-time monitoring rule definition.

    Attributes:
        rule_id: Unique identifier for rule (1-64 alphanumeric, dash, underscore).
        name: Human-readable descriptive name.
        symbol: Canonical Taiwan symbol (e.g. '2330.TWSE', '8069.TPEX').
        rule_type: TaiwanRuleType enum or value.
        threshold: Numeric trigger value (price, change_pct %, volume in shares, distance_pct %, multiple).
        enabled: Whether rule actively participates in evaluation.
        cooldown_seconds: Minimum seconds between successive alerts.
        hysteresis: Optional delta required for re-arming edge-triggered state.
        reference_volume: Optional baseline volume in SHARES for volume_spike rule.
        severity: Alert severity (INFO, WARNING, CRITICAL).
        created_at: ISO timestamp when rule was created.
        updated_at: ISO timestamp when rule was last modified.
    """
    rule_id: str
    name: str
    symbol: str
    rule_type: TaiwanRuleType | str
    threshold: float
    enabled: bool = True
    cooldown_seconds: int = 300
    hysteresis: float | None = None
    reference_volume: int | None = None  # in SHARES
    severity: TaiwanAlertSeverity | str = TaiwanAlertSeverity.WARNING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        if isinstance(self.rule_type, str):
            self.rule_type = TaiwanRuleType(self.rule_type)
        if isinstance(self.severity, str):
            self.severity = TaiwanAlertSeverity(self.severity)

        # Standardize canonical symbol
        ts = parse_symbol(self.symbol)
        self.symbol = ts.canonical

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "symbol": self.symbol,
            "rule_type": self.rule_type.value if isinstance(self.rule_type, TaiwanRuleType) else str(self.rule_type),
            "threshold": self.threshold,
            "enabled": self.enabled,
            "cooldown_seconds": self.cooldown_seconds,
            "hysteresis": self.hysteresis,
            "reference_volume": self.reference_volume,
            "severity": self.severity.value if isinstance(self.severity, TaiwanAlertSeverity) else str(self.severity),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaiwanMonitorRule:
        return cls(
            rule_id=str(data["rule_id"]),
            name=str(data["name"]),
            symbol=str(data["symbol"]),
            rule_type=data["rule_type"],
            threshold=float(data["threshold"]),
            enabled=bool(data.get("enabled", True)),
            cooldown_seconds=int(data.get("cooldown_seconds", 300)),
            hysteresis=float(data["hysteresis"]) if data.get("hysteresis") is not None else None,
            reference_volume=int(data["reference_volume"]) if data.get("reference_volume") is not None else None,
            severity=data.get("severity", TaiwanAlertSeverity.WARNING),
            created_at=str(data.get("created_at", datetime.now().isoformat())),
            updated_at=str(data.get("updated_at", datetime.now().isoformat())),
        )


@dataclass(frozen=True)
class TaiwanAlertEvent:
    """Standardized Taiwan Real-time Alert Event schema."""
    alert_id: str
    rule_id: str
    rule_name: str
    symbol: str
    name: str
    rule_type: str
    triggered_at: datetime
    quote_time: datetime | None
    trigger_value: float
    threshold: float
    message: str
    source: str
    source_status: str
    market_status: str
    severity: str
    field_name: str
    dedup_key: str
    ts: int = 0  # Epoch timestamp in milliseconds for UI compatibility

    def __post_init__(self) -> None:
        if self.ts == 0 and self.triggered_at:
            object.__setattr__(self, "ts", int(self.triggered_at.timestamp() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "symbol": self.symbol,
            "name": self.name,
            "rule_type": self.rule_type,
            "triggered_at": self.triggered_at.isoformat(),
            "quote_time": self.quote_time.isoformat() if self.quote_time else None,
            "trigger_value": self.trigger_value,
            "threshold": self.threshold,
            "message": self.message,
            "source": self.source,
            "source_status": self.source_status,
            "market_status": self.market_status,
            "severity": self.severity,
            "field_name": self.field_name,
            "dedup_key": self.dedup_key,
            "ts": self.ts,
            # Compatibility fields with legacy SSE frontend schema
            "price": self.trigger_value if "price" in self.rule_type else None,
            "change_pct": self.trigger_value if "change_pct" in self.rule_type else None,
            "type": self.rule_type,
        }

"""Current live contracts. No historical admission, OOS labels or import API."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.taiwan.quant.panel import FACTOR_VERSION
from app.taiwan.realtime.calendar import TAIPEI_TZ, TradingDayEvidence

LIVE_TIER = "current_live_verified"
LIVE_CONTRACT = "current_live_verified_v1"
FEATURES = ("momentum_5d", "momentum_20d", "momentum_60d")
HORIZONS = (1, 5, 20)
HISTORICAL_BLOCKER = "authoritative_historical_ordinary_stock_subtype_unavailable"
EvidenceReader = Callable[[date, str], TradingDayEvidence]


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("naive timestamp")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("snapshot keys must be strings")
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        # Collections in this schema are sets of records; explicit rank/session
        # fields carry order. JSON object order and DataFrame order are not identity.
        rows = [_canonical(v) for v in value]
        return sorted(rows, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite snapshot value")
        return int(value) if value.is_integer() else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported snapshot type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def latest_completed_session(now: datetime, evidence: EvidenceReader) -> date:
    """Publication cutoff matches daily_update (16:00 Taipei).

    Unknown weekdays stop resolution; never walk over an unproven closure to
    turn stale data into a live session. Both exchanges must agree.
    """
    if now.utcoffset() is None:
        raise ValueError("live clock must be timezone-aware")
    local = now.astimezone(TAIPEI_TZ)
    day = local.date()
    if local.hour < 16:
        day -= timedelta(days=1)
    for _ in range(31):
        statuses = {evidence(day, exchange).status for exchange in ("TWSE", "TPEX")}
        if statuses == {"trading"}:
            return day
        if statuses != {"non_trading"}:
            raise ValueError(f"session_evidence_unresolved:{day}")
        day -= timedelta(days=1)
    raise ValueError("latest_session_unavailable")


@dataclass(frozen=True)
class LiveModel:
    model_id: str = "tw-eod-momentum"
    version: str = "v1"
    factor_version: str = FACTOR_VERSION
    feature_schema_version: str = "live-features-v1"
    policy_version: str = "live-momentum-policy-v1"
    min_warmup_sessions: int = 61
    min_adv20_twd: float = 10_000_000.0
    top_n: int = 10
    min_rank: float = 0.7

    def __post_init__(self) -> None:
        if not all((self.model_id, self.version, self.factor_version,
                    self.feature_schema_version, self.policy_version)):
            raise ValueError("all live versions are required")
        if (not isinstance(self.top_n, int) or isinstance(self.top_n, bool)
                or not 0 <= self.top_n <= 10 or not 0 <= self.min_rank <= 1):
            raise ValueError("invalid live selection bounds")
        if self.min_warmup_sessions < 61 or self.min_adv20_twd <= 0:
            raise ValueError("live requires warmed price features and positive liquidity floor")

    @property
    def key(self) -> str:
        return canonical_hash({"model_id": self.model_id, "version": self.version})

    def describe(self) -> dict[str, Any]:
        return {**asdict(self), "model_key": self.key, "universe_contract": LIVE_CONTRACT,
                "features": list(FEATURES), "weight_policy": "equal_weight_fixed_not_fitted",
                "usage_scope": "experimental_live", "validation_state": "unvalidated",
                "historical_primary_oos": {"status": "blocked", "reason": HISTORICAL_BLOCKER}}


@dataclass(frozen=True)
class LiveSignalBatch:
    model: LiveModel
    session: date
    snapshot: dict[str, Any]

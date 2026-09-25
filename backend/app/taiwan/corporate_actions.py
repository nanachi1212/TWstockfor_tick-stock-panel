"""A6 canonical price events and audit-backed factor derivation.

Raw OHLCV is never written here. Source parsing lives in providers; this module
owns numeric semantics and the atomic, append-only observation store. Official
tables are unversioned latest views, so conflicting observations are retained
and fail closed instead of silently rewriting an earlier training input.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan.providers.taiwan_values import TAIPEI

SOURCE_EXCHANGE = {"TWT49U": "TWSE", "TWTAUU": "TWSE", "TWTB8U": "TWSE",
                   "exDailyQ": "TPEX", "revivt": "TPEX"}
SOURCE_EVENT_TYPES = {
    "TWT49U": frozenset({"cash_dividend", "stock_dividend", "cash_capital_increase"}),
    "exDailyQ": frozenset({"cash_dividend", "stock_dividend", "cash_capital_increase"}),
    "TWTAUU": frozenset({"capital_reduction"}),
    "revivt": frozenset({"capital_reduction"}),
    "TWTB8U": frozenset({"par_change"}),
}
PRICE_COLUMNS = ("open", "high", "low", "close")
EVENT_TYPES = frozenset({"cash_dividend", "stock_dividend", "capital_reduction",
                         "par_change", "cash_capital_increase"})
# Audit §2.5 establishes one price-basis scale for all four daily prices.
# No volume support is inferred from this matrix.
PRICE_SUPPORT = {
    (exchange, kind): frozenset(PRICE_COLUMNS)
    for exchange in ("TWSE", "TPEX") for kind in EVENT_TYPES
    if not (exchange == "TPEX" and kind == "par_change")
}
SHARE_COUNT_EVENTS = frozenset({"stock_dividend", "capital_reduction", "par_change"})


def event_market_open(day: date) -> datetime:
    return datetime.combine(day, time(9), tzinfo=TAIPEI)


@dataclass(frozen=True)
class CorporateActionEvent:
    symbol: str
    exchange: str
    effective_date: date
    effective_at: datetime
    event_type: str
    previous_close: float | None
    reference_price: float | None
    factor: float | None
    cash_dividend: float | None
    free_share_ratio: float | None
    reduction_ratio: float | None
    source: str
    source_url: str
    retrieved_at: datetime
    status: str = "data_insufficient"
    precision_method: str = "unresolved"
    revision_status: str = "unversioned_source"
    raw_fields: str = "{}"
    reason: str | None = None
    available_at: datetime | None = None
    availability_policy: str = "market_mechanism_inferred"

    def __post_init__(self) -> None:
        if self.exchange not in {"TWSE", "TPEX"} or not self.symbol.endswith(f".{self.exchange}"):
            raise ValueError("corporate action requires a canonical market symbol")
        if self.retrieved_at.utcoffset() is None or self.effective_at.utcoffset() is None:
            raise ValueError("corporate action timestamps must be timezone-aware")
        if self.effective_at != event_market_open(self.effective_date):
            raise ValueError("effective_at must be ex/resume-date market open")
        if self.available_at is not None or self.availability_policy != "market_mechanism_inferred":
            raise ValueError("normalization events do not establish predictive available_at")
        if self.status not in {"verified", "data_insufficient", "provider_error"}:
            raise ValueError("invalid corporate action status")
        if self.status == "verified":
            values = (self.previous_close, self.reference_price, self.factor)
            if any(v is None or not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError("verified factor requires positive finite prices and factor")
            if not math.isclose(self.factor, self.reference_price / self.previous_close,
                                rel_tol=1e-12):
                raise ValueError("factor disagrees with official price basis")
            if not PRICE_SUPPORT.get((self.exchange, self.event_type)):
                raise ValueError("unsupported verified event")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def identity(self) -> tuple[str, date]:
        # Same-day cross-source events are not independently composable (§2.6).
        return self.symbol, self.effective_date

    @property
    def content_hash(self) -> str:
        values = self.to_dict()
        for key in ("retrieved_at", "revision_status", "source_url"):
            values.pop(key)
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def insufficient(event: CorporateActionEvent, reason: str, *,
                 status: str = "data_insufficient") -> CorporateActionEvent:
    return replace(event, factor=None, status=status, reason=reason, precision_method="unresolved")


def _number(value: object) -> Decimal:
    if value is None:
        raise ValueError("missing numeric field")
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("missing or malformed numeric field") from exc
    if not number.is_finite():
        raise ValueError("nonfinite numeric field")
    return number


def _trunc2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _print_step(text: object) -> Decimal:
    """Half a unit of the last printed decimal place; whole numbers are exact."""
    exponent = _number(text).as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= 0:
        return Decimal(0)
    return Decimal(1).scaleb(exponent) / 2


def _detail_reference_bounds(previous: Decimal, cash_text: object, free_text: object) -> tuple[Decimal, Decimal]:
    """Reference-price interval implied by the printed precision of the detail inputs."""
    cash, free = _number(cash_text), _number(free_text) / 1000
    cash_step, free_step = _print_step(cash_text), _print_step(free_text) / 1000
    candidates = [
        (previous - cash + sign_c * cash_step) / (1 + free + sign_f * free_step)
        for sign_c in (-1, 1) for sign_f in (-1, 1)
    ]
    return _trunc2(min(candidates)), max(candidates)


def derive_factor(event: CorporateActionEvent) -> CorporateActionEvent:
    """Audit §2.2-2.4 only; never use the cash-subscription reference by mistake.

    A parser may leave nullable normalized fields, but this function cannot
    silently approximate missing inputs or infer capital/reduction ratios.
    """
    if event.reason:
        return event
    if (SOURCE_EXCHANGE.get(event.source) != event.exchange
            or event.event_type not in SOURCE_EVENT_TYPES.get(event.source, frozenset())
            or (event.exchange, event.event_type) not in PRICE_SUPPORT):
        return insufficient(event, "unsupported_event_or_source")
    try:
        raw = json.loads(event.raw_fields)
        previous = _number(event.previous_close)
        # reference_price on a derived event is already high precision. Always
        # validate against the original published field, making derivation idempotent.
        published_ref = _number(raw.get("減除股利參考價") if event.source in {"TWT49U", "exDailyQ"}
                                else event.reference_price)
        if previous <= 0 or published_ref <= 0:
            raise ValueError("nonpositive price basis")
        reference = published_ref
        cash, free = event.cash_dividend, event.free_share_ratio
        kind = event.event_type
        if event.source in {"TWTAUU", "TWTB8U", "revivt"}:
            required_kind = "par_change" if event.source == "TWTB8U" else "capital_reduction"
            if kind != required_kind:
                raise ValueError("source/event type mismatch")
            method = "official_reference_ratio"
        elif event.source == "TWT49U":
            ex_reference = _number(raw.get("除權息參考價"))
            if ex_reference == published_ref:
                # Six-decimal official 權值+息值 retains the precision discarded
                # from the two-decimal published reference (audit 0056/2330).
                value = _number(raw.get("權值+息值"))
                if value < 0:
                    # A subscription price above the market makes the theoretical
                    # rights value negative; the exchange then publishes the prior
                    # close as the reference. Anything else stays unusable.
                    if published_ref != previous:
                        raise ValueError("negative rights/dividend value")
                    reference = previous
                    method = "twse_negative_rights_value_prior_close"
                else:
                    reference = previous - value
                    if _trunc2(reference) != published_ref:
                        raise ValueError("high precision reference mismatch")
                    method = "twse_rights_value_high_precision"
            else:
                detail = raw.get("detail")
                if not isinstance(detail, dict):
                    raise ValueError("cash_subscription_requires_detail")
                cash_text = detail.get("(每股配發現金股利)除息")
                free_text = detail.get("A. 按普通股股東持股比例每千股無償配股")
                cash_decimal = _number(cash_text)
                free_decimal = _number(free_text) / 1000
                if cash_decimal < 0 or free_decimal < 0:
                    raise ValueError("negative dividend or share ratio")
                reference = (previous - cash_decimal) / (1 + free_decimal)
                method = "twse_detail_recomputed"
                if _trunc2(reference) != published_ref:
                    # The detail page prints its inputs with limited decimals. The
                    # published reference must be reproducible from *some* value
                    # that prints the same way, otherwise the event stays unusable.
                    low, high = _detail_reference_bounds(previous, cash_text, free_text)
                    if not low <= published_ref <= high:
                        raise ValueError("detail reference mismatch")
                    reference = published_ref
                    method = "twse_detail_published_within_display_precision"
                cash, free = float(cash_decimal), float(free_decimal)
                kind = ("stock_dividend" if free > 0 else
                        "cash_dividend" if cash > 0 else "cash_capital_increase")
        else:  # TPEx exDailyQ: 1250/1250 official references reproduced (§2.2).
            cash_decimal = _number(raw.get("現金股利"))
            free_decimal = _number(raw.get("每仟股無償配股")) / 1000
            if cash_decimal < 0 or free_decimal < 0:
                raise ValueError("negative dividend or share ratio")
            reference = (previous - cash_decimal) / (1 + free_decimal)
            if _trunc2(reference) != published_ref:
                raise ValueError("TPEx reference mismatch")
            cash, free = float(cash_decimal), float(free_decimal)
            kind = ("stock_dividend" if free > 0 else
                    "cash_dividend" if cash > 0 else "cash_capital_increase")
            method = "tpex_dividend_fields_recomputed"
        if reference <= 0:
            raise ValueError("nonpositive derived reference")
        return replace(event, reference_price=float(reference), factor=float(reference / previous),
                       cash_dividend=cash, free_share_ratio=free, event_type=kind,
                       status="verified", precision_method=method)
    except (ValueError, TypeError, ArithmeticError) as exc:
        return insufficient(event, str(exc))


def _equivalent_observations(group: list[CorporateActionEvent]) -> bool:
    keys = {(e.event_type, e.status, e.previous_close, e.reference_price, e.factor,
             e.cash_dividend, e.free_share_ratio, e.reduction_ratio, e.precision_method)
            for e in group}
    return len(keys) == 1 and all(e.status == "verified" for e in group)


def resolve_event_conflicts(events: Iterable[CorporateActionEvent]) -> tuple[CorporateActionEvent, ...]:
    """Identical observations dedup; ambiguous duplicates/revisions are unusable."""
    groups: dict[tuple[str, date], dict[str, CorporateActionEvent]] = defaultdict(dict)
    for event in events:
        groups[event.identity].setdefault(event.content_hash, event)
    output = []
    for key in sorted(groups):
        group = list(groups[key].values())
        # A failed fetch is superseded only by a real observation of the same source;
        # another source's result says nothing about the request that failed.
        answered = {e.source for e in group if e.status != "provider_error"}
        group = [e for e in group if e.status != "provider_error" or e.source not in answered]
        if len(group) == 1:
            output.append(group[0])
        elif _equivalent_observations(group):
            # Repeated announcements of one event (different detail/report rows,
            # same official price basis): keep one, deterministically.
            output.append(replace(min(group, key=lambda e: e.content_hash),
                                  revision_status="equivalent_observations"))
        else:
            output.extend(replace(insufficient(e, "conflicting_event_or_revision"),
                                  revision_status="conflict") for e in group)
    return tuple(output)


_SCHEMA = {
    "symbol": pl.String, "exchange": pl.String, "effective_date": pl.Date,
    "effective_at": pl.String, "event_type": pl.String,
    **{name: pl.Float64 for name in ("previous_close", "reference_price", "factor",
                                    "cash_dividend", "free_share_ratio", "reduction_ratio")},
    **{name: pl.String for name in ("source", "source_url", "retrieved_at", "status",
                                   "precision_method", "revision_status", "raw_fields", "reason",
                                   "available_at", "availability_policy")},
}


class CorporateActionStore:
    """<DATA_DIR>/taiwan/adj_factor/events.parquet; no read cache or raw writes.

    Save preserves first observations and any changed source content. A small
    exclusive lock prevents cross-process read/merge/replace lost updates.
    A stale lock is an explicit error; it is never silently deleted.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "adj_factor"
        self.path = Path(data_dir) / "events.parquet"

    def _read_observations(self) -> list[CorporateActionEvent]:
        if not self.path.exists():
            return []
        frame = pl.read_parquet(self.path)
        result = []
        for row in frame.iter_rows(named=True):
            # Critical missing historical fields are not upgraded to verified.
            missing = set(_SCHEMA) - set(row)
            values = {k: v for k, v in row.items() if k in _SCHEMA}
            if isinstance(values.get("effective_date"), str):
                values["effective_date"] = date.fromisoformat(values["effective_date"])
            for key in ("effective_at", "retrieved_at", "available_at"):
                if isinstance(values.get(key), str):
                    values[key] = datetime.fromisoformat(values[key])
            if "effective_at" not in values and values.get("effective_date"):
                values["effective_at"] = event_market_open(values["effective_date"])
            for key in ("cash_dividend", "free_share_ratio", "reduction_ratio", "factor",
                        "previous_close", "reference_price"):
                values.setdefault(key, None)
            if missing:
                values.update(status="data_insufficient", factor=None,
                              reason="legacy_schema_missing:" + ",".join(sorted(missing)))
            result.append(CorporateActionEvent(**values))
        return result

    def read(self) -> tuple[CorporateActionEvent, ...]:
        return resolve_event_conflicts(self._read_observations())

    def snapshot_digest(self) -> str:
        """Identity of the stored observations; bound to the coverage marker."""
        digest = hashlib.sha256()
        for value in sorted(event.content_hash for event in self._read_observations()):
            digest.update(value.encode("ascii"))
        return digest.hexdigest()

    def save(self, events: Iterable[CorporateActionEvent]) -> int:
        incoming = tuple(events)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError("corporate-action store is locked; no data changed") from exc
        os.close(descriptor)
        temporary = None
        try:
            merged = {e.content_hash: e for e in self._read_observations()}
            for event in incoming:
                merged.setdefault(event.content_hash, event)
            records = []
            for event in sorted(merged.values(), key=lambda e: (e.symbol, e.effective_date, e.content_hash)):
                record = event.to_dict()
                for key in ("effective_at", "retrieved_at", "available_at"):
                    value = record[key]
                    record[key] = value.isoformat() if value else None
                records.append(record)
            frame = pl.DataFrame(records, schema=_SCHEMA)
            handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            os.close(handle)
            frame.write_parquet(temporary)
            os.replace(temporary, self.path)
            return frame.height
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
            lock.unlink(missing_ok=True)

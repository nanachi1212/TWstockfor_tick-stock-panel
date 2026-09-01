"""Taiwan Symbol Layer — canonical symbol representation and provider conversion.

Responsibilities (and *nothing* else):
  - Canonical symbol representation
  - Raw stock code extraction
  - Exchange / market identification
  - Instrument type classification (enum definition)
  - Provider-specific symbol conversion (adapters)
  - Display formatting interface

This module does NOT handle:
  - Market data / quotes / prices
  - Trading rules (price limits, fees, taxes, settlement)
  - Static security master data (stock names, listing dates, sector)
  - Those belong to their respective modules / providers.

Canonical format
----------------
Internal canonical symbol: ``{code}.{EXCHANGE}``

Examples::

    2330.TWSE   — TSMC, listed on TWSE
    8069.TPEX   — E Ink, listed on TPEx
    0050.TWSE   — Yuanta Taiwan 50 ETF
    TAIEX.TWSE  — TAIEX weighted index

Design rationale:

1. **Consistent with existing codebase**: The repo uses ``{code}.{SUFFIX}``
   (e.g. ``600519.SH``). Keeping the same pattern minimises changes to
   downstream Polars expressions, Parquet schemas, DuckDB views, and API
   contracts that split on ``"."`` to extract the raw code.

2. **Provider-agnostic**: ``TWSE`` / ``TPEX`` are official exchange names,
   not Yahoo's ``.TW`` / ``.TWO`` convention.  Provider-specific formats
   are confined to adapter functions (:func:`to_provider_symbol` /
   :func:`from_provider_symbol`) and never leak into the domain model.

3. **Unambiguous**: ``TWSE`` and ``TPEX`` cannot be confused with any
   A-share exchange suffix (``SH`` / ``SZ`` / ``BJ``), enabling a future
   multi-market coexistence path if needed.

4. **Suffix length note**: The existing codebase has *one* place that
   uses ``str.slice(-2)`` to extract the 2-char exchange suffix
   (``pipeline.py:_bench_exchange_expr``).  That function is A-share
   deviation logic slated for replacement.  The canonical suffix length
   should not be constrained by one line of legacy code.  The robust
   extraction pattern already used elsewhere — ``symbol.split(".")[0]``
   for code and ``symbol.split(".")[-1]`` for exchange — works with any
   suffix length.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ── Enums ──────────────────────────────────────────────────────


class Exchange(str, Enum):
    """Taiwan securities exchanges."""

    TWSE = "TWSE"  # Taiwan Stock Exchange (臺灣證券交易所) — listed (上市)
    TPEX = "TPEX"  # Taipei Exchange (財團法人中華民國證券櫃檯買賣中心) — OTC (上櫃)


class InstrumentType(str, Enum):
    """Instrument classification.

    Defined here as part of the Symbol Layer's domain vocabulary.
    The *association* between a specific symbol and its instrument type
    comes from the instruments / security-master provider, not from the
    symbol string itself.
    """

    STOCK = "stock"
    ETF = "etf"
    INDEX = "index"
    # Extensible: WARRANT = "warrant", BOND = "bond", TDR = "tdr", ...


# ── Canonical Symbol ───────────────────────────────────────────


@dataclass(frozen=True)
class TaiwanSymbol:
    """Canonical representation of a Taiwan market symbol.

    Identity is defined by ``(code, exchange)``.  The canonical string
    form is ``{code}.{EXCHANGE}`` (e.g. ``2330.TWSE``).

    Examples::

        TaiwanSymbol("2330", Exchange.TWSE).canonical  # "2330.TWSE"
        TaiwanSymbol("8069", Exchange.TPEX).canonical  # "8069.TPEX"
    """

    code: str
    exchange: Exchange

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("Symbol code must not be empty")

    @property
    def canonical(self) -> str:
        """Internal canonical symbol string: ``'{code}.{EXCHANGE}'``."""
        return f"{self.code}.{self.exchange.value}"

    def __str__(self) -> str:
        return self.canonical


# ── Parsing ────────────────────────────────────────────────────


def parse_symbol(raw: str, *, exchange: Exchange | None = None) -> TaiwanSymbol:
    """Parse a symbol string into a canonical :class:`TaiwanSymbol`.

    Supported input formats:

    - Canonical: ``"2330.TWSE"``, ``"8069.TPEX"``
    - Raw code + explicit exchange:
      ``parse_symbol("2330", exchange=Exchange.TWSE)``

    For provider-specific formats (e.g. Yahoo's ``2330.TW``), use
    :func:`from_provider_symbol` instead.

    Raises:
        ValueError: If the symbol cannot be parsed unambiguously.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("Empty symbol string")

    # Try canonical format: code.EXCHANGE
    if "." in raw:
        code, suffix = raw.rsplit(".", 1)
        suffix_upper = suffix.upper()
        try:
            ex = Exchange(suffix_upper)
            return TaiwanSymbol(code=code, exchange=ex)
        except ValueError:
            pass  # Not a known exchange suffix — fall through

    # Raw code with explicit exchange parameter
    if exchange is not None:
        code = raw.split(".")[0] if "." in raw else raw
        return TaiwanSymbol(code=code, exchange=exchange)

    raise ValueError(
        f"Cannot determine exchange for symbol {raw!r}. "
        f"Provide exchange explicitly or use canonical format "
        f"'CODE.TWSE' / 'CODE.TPEX'."
    )


# ── Provider Adapters ──────────────────────────────────────────

# Yahoo Finance Taiwan symbol convention:
#   TWSE → .TW   (e.g. 2330.TW)
#   TPEX → .TWO  (e.g. 8069.TWO)
_YAHOO_TO_EXCHANGE: dict[str, Exchange] = {
    ".TW": Exchange.TWSE,
    ".TWO": Exchange.TPEX,
}
_EXCHANGE_TO_YAHOO: dict[Exchange, str] = {v: k for k, v in _YAHOO_TO_EXCHANGE.items()}


def to_provider_symbol(symbol: TaiwanSymbol | str, provider: str) -> str:
    """Convert a canonical symbol to a provider-specific format.

    Supported providers:

    - ``"yahoo"``:  ``2330.TWSE`` → ``2330.TW``
    - ``"twse"``:   ``2330.TWSE`` → ``2330`` (raw code)
    - ``"tpex"``:   ``8069.TPEX`` → ``8069`` (raw code)
    - ``"finmind"``: any → raw code

    Raises:
        ValueError: If the provider is unknown or conversion is impossible.
    """
    if isinstance(symbol, str):
        symbol = parse_symbol(symbol)

    provider_lower = provider.lower()

    if provider_lower == "yahoo":
        suffix = _EXCHANGE_TO_YAHOO.get(symbol.exchange)
        if suffix is None:
            raise ValueError(f"No Yahoo mapping for exchange {symbol.exchange!r}")
        return f"{symbol.code}{suffix}"

    if provider_lower in ("twse", "tpex", "finmind"):
        return symbol.code

    raise ValueError(f"Unknown provider: {provider!r}")


def from_provider_symbol(
    provider_symbol: str,
    provider: str,
    *,
    exchange: Exchange | None = None,
) -> TaiwanSymbol:
    """Convert a provider-specific symbol to a canonical :class:`TaiwanSymbol`.

    Args:
        provider_symbol: Symbol in the provider's native format.
        provider: Provider name (``"yahoo"``, ``"twse"``, ``"tpex"``,
            ``"finmind"``).
        exchange: Required for providers whose symbol format does not
            encode the exchange (e.g. ``"finmind"``).

    Raises:
        ValueError: If conversion cannot be performed.
    """
    provider_symbol = provider_symbol.strip()
    if not provider_symbol:
        raise ValueError("Empty provider symbol")

    provider_lower = provider.lower()

    if provider_lower == "yahoo":
        upper = provider_symbol.upper()
        # Try longest suffix first to avoid ".TW" matching ".TWO" symbols
        for suffix in sorted(_YAHOO_TO_EXCHANGE, key=len, reverse=True):
            if upper.endswith(suffix):
                code = provider_symbol[: -len(suffix)]
                return TaiwanSymbol(code=code, exchange=_YAHOO_TO_EXCHANGE[suffix])
        raise ValueError(
            f"Cannot parse Yahoo symbol {provider_symbol!r}: "
            f"expected suffix .TW or .TWO"
        )

    if provider_lower == "twse":
        return TaiwanSymbol(code=provider_symbol, exchange=Exchange.TWSE)

    if provider_lower == "tpex":
        return TaiwanSymbol(code=provider_symbol, exchange=Exchange.TPEX)

    if provider_lower == "finmind":
        if exchange is None:
            raise ValueError(
                "FinMind symbols are raw codes; exchange must be provided explicitly."
            )
        return TaiwanSymbol(code=provider_symbol, exchange=exchange)

    raise ValueError(f"Unknown provider: {provider!r}")


# ── Display Formatting ─────────────────────────────────────────


def format_display(canonical_symbol: str, name: str | None = None) -> str:
    """Format a canonical symbol for UI display.

    The ``name`` parameter should come from the instruments / security-master
    provider, **not** from hardcoded mappings.

    Examples::

        format_display("2330.TWSE", "台積電")   # "2330 台積電"
        format_display("2330.TWSE")              # "2330"
    """
    code = extract_code(canonical_symbol)
    if name:
        return f"{code} {name}"
    return code


# ── Utility Helpers ────────────────────────────────────────────


def extract_code(canonical_symbol: str) -> str:
    """Extract the raw stock code from a canonical symbol string.

    Mirrors the existing codebase pattern ``symbol.split(".")[0]``.

    Examples::

        extract_code("2330.TWSE")   # "2330"
        extract_code("006208.TWSE") # "006208"
        extract_code("2330")        # "2330"
    """
    return canonical_symbol.split(".")[0] if "." in canonical_symbol else canonical_symbol


def extract_exchange(canonical_symbol: str) -> Exchange | None:
    """Extract the exchange from a canonical symbol string.

    Returns ``None`` if the suffix is not a recognised Taiwan exchange.

    Examples::

        extract_exchange("2330.TWSE")   # Exchange.TWSE
        extract_exchange("8069.TPEX")   # Exchange.TPEX
        extract_exchange("600519.SH")   # None
        extract_exchange("2330")        # None
    """
    if "." not in canonical_symbol:
        return None
    suffix = canonical_symbol.rsplit(".", 1)[1]
    try:
        return Exchange(suffix.upper())
    except ValueError:
        return None


def is_taiwan_symbol(canonical_symbol: str) -> bool:
    """Check whether a canonical symbol string belongs to the Taiwan market."""
    return extract_exchange(canonical_symbol) is not None

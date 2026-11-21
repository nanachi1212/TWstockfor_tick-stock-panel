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
   legacy non-Taiwan exchange suffix (``SH`` / ``SZ`` / ``BJ``), enabling a future
   multi-market coexistence path if needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.taiwan.universe.models import TaiwanInstrument
    from app.taiwan.universe.service import TaiwanSecurityMaster

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


@lru_cache(maxsize=2)
def _master_codes(path: Path, generation: tuple[int, int]) -> dict[str, set[Exchange]]:
    """Index the locally persisted official directory once per atomic publication."""
    import polars as pl

    try:
        rows = pl.read_parquet(path, columns=["code", "exchange"])
        markets: dict[str, set[Exchange]] = {}
        for code, market in rows.iter_rows():
            markets.setdefault(code, set()).add(Exchange(market))
        return markets
    except (OSError, pl.exceptions.PolarsError, ValueError) as exc:
        raise ValueError("Cannot read the Taiwan security master") from exc


def parse_symbol(
    raw: str, *, exchange: Exchange | None = None, verify_listing: bool = False,
) -> TaiwanSymbol:
    """Parse a symbol string into a canonical :class:`TaiwanSymbol`.

    Supported input formats:

    - Canonical: ``"2330.TWSE"``, ``"8069.TPEX"``
    - Bare code with one exact match in the local official securities directory.
    - Raw code + explicit exchange:
      ``parse_symbol("2330", exchange=Exchange.TWSE)``

    Bare codes always need an exact directory match to infer a market. Use
    ``verify_listing=True`` for current stock selection. Historical queries can
    address a qualified security that is absent from today's directory.

    For provider-specific formats (e.g. Yahoo's ``2330.TW``), use
    :func:`from_provider_symbol` instead.

    Raises:
        ValueError: If the symbol cannot be parsed unambiguously.
    """
    from app.taiwan.data_root import taiwan_data_root

    raw = raw.strip().upper()
    if not raw:
        raise ValueError("Empty symbol string")

    code, dot, suffix = raw.partition(".")
    if dot:
        market = Exchange(suffix) if suffix in ("TWSE", "TPEX") else None
        # Older callers can supply a provider suffix together with its explicit exchange.
        if market is None and exchange is not None:
            market = _YAHOO_TO_EXCHANGE.get("." + suffix)
        if market is None or (exchange is not None and exchange != market):
            raise ValueError(f"Cannot determine exchange for symbol {raw!r}")
        exchange = market

    # Benchmark indices have a separate official catalog from stocks and ETFs.
    if (code, exchange) in {("TAIEX", Exchange.TWSE), ("TPEX_INDEX", Exchange.TPEX)}:
        return TaiwanSymbol(code, exchange)

    path = taiwan_data_root() / "security_master.parquet"
    try:
        stat = path.stat()
    except FileNotFoundError:
        if not re.fullmatch(r"[0-9]{4,6}[A-Z]?", code):
            raise ValueError(f"Invalid Taiwan security code {code!r}") from None
        if exchange is not None:
            return TaiwanSymbol(code, exchange)
    else:
        markets = _master_codes(path, (stat.st_mtime_ns, stat.st_size)).get(code, set())
        if exchange is None and len(markets) == 1:
            return TaiwanSymbol(code, next(iter(markets)))
        if exchange is not None and exchange in markets:
            return TaiwanSymbol(code, exchange)
        if not markets and not verify_listing and exchange is not None and dot and re.fullmatch(
            r"[0-9]{4,6}[A-Z]?", code
        ):
            return TaiwanSymbol(code, exchange)

    raise ValueError(
        f"Cannot determine exchange for symbol {raw!r}. "
        f"Provide exchange explicitly or use canonical format "
        f"'CODE.TWSE' / 'CODE.TPEX'."
    )


def parse_history_symbol(raw: str) -> TaiwanSymbol:
    """A qualified historical key retains the market where that bar was recorded."""
    code, _, suffix = raw.strip().upper().partition(".")
    if suffix in ("TWSE", "TPEX") and re.fullmatch(r"[0-9]{4,6}[A-Z]?", code):
        return TaiwanSymbol(code, Exchange(suffix))
    return parse_symbol(raw)


def resolve_directory_instrument(raw: str, master: TaiwanSecurityMaster) -> TaiwanInstrument | None:
    """Resolve codes by exact market membership. A name may use the directory's text search."""
    query = raw.strip().upper()
    if not query:
        return None
    if "." in query:
        code, _, suffix = query.partition(".")
        market = _YAHOO_TO_EXCHANGE.get("." + suffix)
        if market:
            query = f"{code}.{market.value}"
        if is_taiwan_symbol(query):
            return master.get_instrument(query)
    if query.isascii() and query.isalnum():
        exact = [inst for exchange in Exchange
                 if (inst := master.get_instrument(f"{query}.{exchange.value}")) is not None]
        if exact:
            return exact[0] if len(exact) == 1 else None
    if re.fullmatch(r"[0-9][0-9A-Z._-]*", query):
        return None
    matches = master.search(query, limit=5)
    exact_names = [item for item in matches if item["name"].upper() == query]
    choices = exact_names or matches
    return master.get_instrument(choices[0]["symbol"]) if len(choices) == 1 else None


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
    """Check the transport namespace (custom index identifiers are also supported)."""
    return bool(re.fullmatch(r"[A-Za-z0-9_]+\.(?:TWSE|TPEX)", canonical_symbol, re.IGNORECASE | re.ASCII))

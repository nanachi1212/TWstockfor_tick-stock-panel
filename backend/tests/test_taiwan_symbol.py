"""Tests for Taiwan Symbol Layer.

Reference stocks used as test fixtures (names are NOT hardcoded in production):
  - 2330 台積電 (TSMC, TWSE)
  - 2454 聯發科 (MediaTek, TWSE)
  - 2317 鴻海 (Hon Hai, TWSE)
  - 0050 元大台灣50 (Yuanta Taiwan 50 ETF, TWSE)
  - 006208 富邦台50 (Fubon Taiwan 50 ETF, TWSE)
  - 8069 元太 (E Ink, TPEX)
"""
from __future__ import annotations

import pytest

from app.taiwan.symbol import (
    Exchange,
    InstrumentType,
    TaiwanSymbol,
    extract_code,
    extract_exchange,
    format_display,
    from_provider_symbol,
    is_taiwan_symbol,
    parse_symbol,
    to_provider_symbol,
)


# ── Test fixtures ──────────────────────────────────────────────

# These names are ONLY in tests — they are NOT in production code.
REFERENCE_STOCKS = [
    ("2330", Exchange.TWSE, "台積電"),
    ("2454", Exchange.TWSE, "聯發科"),
    ("2317", Exchange.TWSE, "鴻海"),
    ("0050", Exchange.TWSE, "元大台灣50"),
    ("006208", Exchange.TWSE, "富邦台50"),
    ("8069", Exchange.TPEX, "元太"),
]


# ── TaiwanSymbol creation ─────────────────────────────────────


class TestTaiwanSymbolCreation:
    """Test TaiwanSymbol dataclass creation and properties."""

    def test_basic_creation(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE
        assert sym.canonical == "2330.TWSE"
        assert str(sym) == "2330.TWSE"

    def test_tpex_symbol(self):
        sym = TaiwanSymbol(code="8069", exchange=Exchange.TPEX)
        assert sym.canonical == "8069.TPEX"
        assert sym.exchange == Exchange.TPEX

    def test_six_digit_etf_code(self):
        sym = TaiwanSymbol(code="006208", exchange=Exchange.TWSE)
        assert sym.canonical == "006208.TWSE"
        assert sym.code == "006208"

    def test_four_digit_etf_code(self):
        sym = TaiwanSymbol(code="0050", exchange=Exchange.TWSE)
        assert sym.canonical == "0050.TWSE"

    def test_index_code(self):
        sym = TaiwanSymbol(code="TAIEX", exchange=Exchange.TWSE)
        assert sym.canonical == "TAIEX.TWSE"

    def test_empty_code_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            TaiwanSymbol(code="", exchange=Exchange.TWSE)

    def test_frozen_immutable(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        with pytest.raises(AttributeError):
            sym.code = "2454"  # type: ignore[misc]

    def test_equality_same(self):
        a = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        b = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        assert a == b

    def test_inequality_different_exchange(self):
        a = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        b = TaiwanSymbol(code="2330", exchange=Exchange.TPEX)
        assert a != b

    def test_inequality_different_code(self):
        a = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        b = TaiwanSymbol(code="2454", exchange=Exchange.TWSE)
        assert a != b

    def test_hash_equal_objects_same_hash(self):
        a = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        b = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        assert hash(a) == hash(b)
        assert len({a, b}) == 1

    def test_hash_different_objects_different_hash(self):
        a = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        b = TaiwanSymbol(code="8069", exchange=Exchange.TPEX)
        assert len({a, b}) == 2

    def test_can_use_as_dict_key(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        d = {sym: "value"}
        assert d[TaiwanSymbol(code="2330", exchange=Exchange.TWSE)] == "value"


# ── parse_symbol ───────────────────────────────────────────────


class TestParseSymbol:
    """Test parse_symbol function."""

    def test_canonical_twse(self):
        sym = parse_symbol("2330.TWSE")
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE

    def test_canonical_tpex(self):
        sym = parse_symbol("8069.TPEX")
        assert sym.code == "8069"
        assert sym.exchange == Exchange.TPEX

    def test_canonical_case_insensitive(self):
        sym = parse_symbol("2330.twse")
        assert sym.exchange == Exchange.TWSE

    def test_canonical_mixed_case(self):
        sym = parse_symbol("2330.Twse")
        assert sym.exchange == Exchange.TWSE

    def test_six_digit_canonical(self):
        sym = parse_symbol("006208.TWSE")
        assert sym.code == "006208"
        assert sym.exchange == Exchange.TWSE

    def test_raw_code_with_explicit_exchange(self):
        sym = parse_symbol("2330", exchange=Exchange.TWSE)
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE

    def test_raw_code_tpex_explicit(self):
        sym = parse_symbol("8069", exchange=Exchange.TPEX)
        assert sym.code == "8069"
        assert sym.exchange == Exchange.TPEX

    def test_raw_code_without_exchange_raises(self):
        with pytest.raises(ValueError, match="Cannot determine exchange"):
            parse_symbol("2330")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty symbol"):
            parse_symbol("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty symbol"):
            parse_symbol("   ")

    def test_whitespace_stripped(self):
        sym = parse_symbol("  2330.TWSE  ")
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE

    def test_unknown_suffix_without_exchange_raises(self):
        with pytest.raises(ValueError, match="Cannot determine exchange"):
            parse_symbol("2330.XX")

    def test_unknown_suffix_with_explicit_exchange_extracts_code(self):
        """When suffix is unknown but exchange is explicit, use the code part."""
        sym = parse_symbol("2330.TW", exchange=Exchange.TWSE)
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE

    def test_yahoo_format_not_recognised_as_canonical(self):
        """Yahoo's .TW / .TWO must NOT be parsed as canonical — use from_provider_symbol."""
        with pytest.raises(ValueError, match="Cannot determine exchange"):
            parse_symbol("2330.TW")

    def test_yahoo_two_format_not_recognised_as_canonical(self):
        with pytest.raises(ValueError, match="Cannot determine exchange"):
            parse_symbol("8069.TWO")


# ── to_provider_symbol ─────────────────────────────────────────


class TestToProviderSymbol:
    """Test to_provider_symbol conversion."""

    def test_to_yahoo_twse(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        assert to_provider_symbol(sym, "yahoo") == "2330.TW"

    def test_to_yahoo_tpex(self):
        sym = TaiwanSymbol(code="8069", exchange=Exchange.TPEX)
        assert to_provider_symbol(sym, "yahoo") == "8069.TWO"

    def test_to_yahoo_etf(self):
        sym = TaiwanSymbol(code="0050", exchange=Exchange.TWSE)
        assert to_provider_symbol(sym, "yahoo") == "0050.TW"

    def test_to_yahoo_six_digit_etf(self):
        sym = TaiwanSymbol(code="006208", exchange=Exchange.TWSE)
        assert to_provider_symbol(sym, "yahoo") == "006208.TW"

    def test_to_twse(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        assert to_provider_symbol(sym, "twse") == "2330"

    def test_to_tpex(self):
        sym = TaiwanSymbol(code="8069", exchange=Exchange.TPEX)
        assert to_provider_symbol(sym, "tpex") == "8069"

    def test_to_finmind(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        assert to_provider_symbol(sym, "finmind") == "2330"

    def test_from_canonical_string(self):
        """Accept a canonical string as input, not just TaiwanSymbol."""
        assert to_provider_symbol("2330.TWSE", "yahoo") == "2330.TW"
        assert to_provider_symbol("8069.TPEX", "yahoo") == "8069.TWO"

    def test_provider_name_case_insensitive(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        assert to_provider_symbol(sym, "Yahoo") == "2330.TW"
        assert to_provider_symbol(sym, "YAHOO") == "2330.TW"
        assert to_provider_symbol(sym, "TWSE") == "2330"
        assert to_provider_symbol(sym, "FinMind") == "2330"

    def test_unknown_provider_raises(self):
        sym = TaiwanSymbol(code="2330", exchange=Exchange.TWSE)
        with pytest.raises(ValueError, match="Unknown provider"):
            to_provider_symbol(sym, "nonexistent")


# ── from_provider_symbol ───────────────────────────────────────


class TestFromProviderSymbol:
    """Test from_provider_symbol conversion."""

    def test_from_yahoo_twse(self):
        sym = from_provider_symbol("2330.TW", "yahoo")
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE

    def test_from_yahoo_tpex(self):
        sym = from_provider_symbol("8069.TWO", "yahoo")
        assert sym.code == "8069"
        assert sym.exchange == Exchange.TPEX

    def test_from_yahoo_etf(self):
        sym = from_provider_symbol("0050.TW", "yahoo")
        assert sym.code == "0050"
        assert sym.exchange == Exchange.TWSE

    def test_from_yahoo_six_digit(self):
        sym = from_provider_symbol("006208.TW", "yahoo")
        assert sym.code == "006208"
        assert sym.exchange == Exchange.TWSE

    def test_from_yahoo_case_insensitive(self):
        sym = from_provider_symbol("2330.tw", "yahoo")
        assert sym.exchange == Exchange.TWSE

    def test_from_yahoo_two_not_confused_with_tw(self):
        """Ensure .TWO is matched before .TW to avoid extracting code='8069.T'."""
        sym = from_provider_symbol("8069.TWO", "yahoo")
        assert sym.code == "8069"
        assert sym.exchange == Exchange.TPEX

    def test_from_yahoo_invalid_suffix_raises(self):
        with pytest.raises(ValueError, match="expected suffix"):
            from_provider_symbol("2330.XX", "yahoo")

    def test_from_twse(self):
        sym = from_provider_symbol("2330", "twse")
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE

    def test_from_tpex(self):
        sym = from_provider_symbol("8069", "tpex")
        assert sym.code == "8069"
        assert sym.exchange == Exchange.TPEX

    def test_from_finmind_with_exchange(self):
        sym = from_provider_symbol("2330", "finmind", exchange=Exchange.TWSE)
        assert sym.code == "2330"
        assert sym.exchange == Exchange.TWSE

    def test_from_finmind_without_exchange_raises(self):
        with pytest.raises(ValueError, match="exchange must be provided"):
            from_provider_symbol("2330", "finmind")

    def test_from_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            from_provider_symbol("2330", "nonexistent")

    def test_empty_symbol_raises(self):
        with pytest.raises(ValueError, match="Empty provider symbol"):
            from_provider_symbol("", "yahoo")

    def test_whitespace_stripped(self):
        sym = from_provider_symbol("  2330.TW  ", "yahoo")
        assert sym.code == "2330"


# ── Roundtrip consistency ──────────────────────────────────────


class TestRoundtrip:
    """Verify that canonical → provider → canonical is identity."""

    @pytest.mark.parametrize("code,exchange,_name", REFERENCE_STOCKS)
    def test_yahoo_roundtrip(self, code: str, exchange: Exchange, _name: str):
        original = TaiwanSymbol(code=code, exchange=exchange)
        yahoo_sym = to_provider_symbol(original, "yahoo")
        back = from_provider_symbol(yahoo_sym, "yahoo")
        assert back == original
        assert back.canonical == original.canonical

    @pytest.mark.parametrize("code,exchange,_name", REFERENCE_STOCKS)
    def test_twse_tpex_roundtrip(self, code: str, exchange: Exchange, _name: str):
        original = TaiwanSymbol(code=code, exchange=exchange)
        provider = "twse" if exchange == Exchange.TWSE else "tpex"
        raw = to_provider_symbol(original, provider)
        back = from_provider_symbol(raw, provider)
        assert back == original


# ── format_display ─────────────────────────────────────────────


class TestFormatDisplay:
    """Test format_display function."""

    @pytest.mark.parametrize("code,exchange,name", REFERENCE_STOCKS)
    def test_display_with_name(self, code: str, exchange: Exchange, name: str):
        canonical = f"{code}.{exchange.value}"
        result = format_display(canonical, name)
        assert result == f"{code} {name}"
        assert result.startswith(code)

    def test_display_without_name(self):
        assert format_display("2330.TWSE") == "2330"

    def test_display_none_name(self):
        assert format_display("2330.TWSE", None) == "2330"

    def test_display_empty_name(self):
        assert format_display("2330.TWSE", "") == "2330"

    def test_display_raw_code_with_name(self):
        assert format_display("2330", "台積電") == "2330 台積電"

    def test_display_raw_code_without_name(self):
        assert format_display("2330") == "2330"


# ── extract_code ───────────────────────────────────────────────


class TestExtractCode:
    """Test extract_code helper."""

    def test_canonical(self):
        assert extract_code("2330.TWSE") == "2330"

    def test_six_digit(self):
        assert extract_code("006208.TWSE") == "006208"

    def test_raw_code(self):
        assert extract_code("2330") == "2330"

    def test_index(self):
        assert extract_code("TAIEX.TWSE") == "TAIEX"

    def test_a_share_format(self):
        """Code extraction works for any dot-delimited format."""
        assert extract_code("600519.SH") == "600519"


# ── extract_exchange ───────────────────────────────────────────


class TestExtractExchange:
    """Test extract_exchange helper."""

    def test_twse(self):
        assert extract_exchange("2330.TWSE") == Exchange.TWSE

    def test_tpex(self):
        assert extract_exchange("8069.TPEX") == Exchange.TPEX

    def test_raw_code_returns_none(self):
        assert extract_exchange("2330") is None

    def test_unknown_suffix_returns_none(self):
        assert extract_exchange("2330.XX") is None

    def test_a_share_suffix_returns_none(self):
        """A-share exchange suffixes are NOT Taiwan exchanges."""
        assert extract_exchange("600519.SH") is None
        assert extract_exchange("000001.SZ") is None
        assert extract_exchange("830799.BJ") is None

    def test_yahoo_suffix_returns_none(self):
        """Yahoo's .TW/.TWO are NOT canonical exchange suffixes."""
        assert extract_exchange("2330.TW") is None
        assert extract_exchange("8069.TWO") is None


# ── is_taiwan_symbol ───────────────────────────────────────────


class TestIsTaiwanSymbol:
    """Test is_taiwan_symbol helper."""

    def test_true_twse(self):
        assert is_taiwan_symbol("2330.TWSE") is True

    def test_true_tpex(self):
        assert is_taiwan_symbol("8069.TPEX") is True

    def test_false_raw_code(self):
        assert is_taiwan_symbol("2330") is False

    def test_false_a_share(self):
        assert is_taiwan_symbol("600519.SH") is False

    def test_false_yahoo_format(self):
        assert is_taiwan_symbol("2330.TW") is False


# ── Enum validation ────────────────────────────────────────────


class TestEnums:
    """Test Exchange and InstrumentType enum definitions."""

    def test_exchange_values(self):
        assert Exchange.TWSE.value == "TWSE"
        assert Exchange.TPEX.value == "TPEX"

    def test_exchange_string_comparison(self):
        assert Exchange.TWSE == "TWSE"
        assert Exchange("TWSE") is Exchange.TWSE

    def test_exchange_from_string(self):
        assert Exchange("TWSE") == Exchange.TWSE
        assert Exchange("TPEX") == Exchange.TPEX

    def test_exchange_invalid_raises(self):
        with pytest.raises(ValueError):
            Exchange("SH")

    def test_instrument_type_values(self):
        assert InstrumentType.STOCK.value == "stock"
        assert InstrumentType.ETF.value == "etf"
        assert InstrumentType.INDEX.value == "index"

    def test_instrument_type_string_comparison(self):
        """InstrumentType(str, Enum) enables string comparison."""
        assert InstrumentType.STOCK == "stock"
        assert InstrumentType("etf") is InstrumentType.ETF

    def test_instrument_type_compatibility_with_asset_type(self):
        """InstrumentType values match existing AssetType literals."""
        from app.data_providers.base import AssetType  # noqa: F401

        # AssetType is Literal["stock", "index", "etf"]
        # InstrumentType values should match:
        for it in InstrumentType:
            assert it.value in ("stock", "index", "etf")


# ── Canonical format contract ──────────────────────────────────


class TestCanonicalFormatContract:
    """Verify that the canonical format is compatible with existing codebase patterns.

    The codebase universally uses ``symbol.split(".")[0]`` to extract the code
    and ``symbol.rsplit(".", 1)[1]`` for the exchange suffix.  These tests
    verify that our canonical format works with these patterns.
    """

    @pytest.mark.parametrize("code,exchange,_name", REFERENCE_STOCKS)
    def test_split_extracts_code(self, code: str, exchange: Exchange, _name: str):
        canonical = f"{code}.{exchange.value}"
        assert canonical.split(".")[0] == code
        assert canonical.split(".", 1)[0] == code

    @pytest.mark.parametrize("code,exchange,_name", REFERENCE_STOCKS)
    def test_rsplit_extracts_exchange(self, code: str, exchange: Exchange, _name: str):
        canonical = f"{code}.{exchange.value}"
        assert canonical.rsplit(".", 1)[1] == exchange.value

    def test_exactly_one_dot(self):
        """Canonical symbol has exactly one dot separator."""
        for code, exchange, _ in REFERENCE_STOCKS:
            canonical = f"{code}.{exchange.value}"
            assert canonical.count(".") == 1

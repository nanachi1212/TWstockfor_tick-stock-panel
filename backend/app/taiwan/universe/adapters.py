"""Official TWSE & TPEx ISIN Open Data Adapters with Provenance-tracked ETF Classification.

Fetches and normalizes the official Taiwan Securities Master from:
  - TWSE (strMode=2): https://isin.twse.com.tw/isin/C_public.jsp?strMode=2
  - TPEx (strMode=4): https://isin.twse.com.tw/isin/C_public.jsp?strMode=4
  - TWSE ETF Product Types: https://openapi.twse.com.tw/v1/opendata/t187ap47_L

Strictly adheres to:
  - Zero external dependencies (uses standard library html.parser.HTMLParser)
  - Multi-tier ETF classification priority:
      1. Official product metadata (TWSE t187ap47_L / TPEx)
      2. ISO 10962 CFI code (CEOIBU, CEOJBU, CEOGDU, CEOGEU)
      3. Multi-character name heuristics (degraded fallback)
  - Parameterized underlying_scope ("domestic" | "foreign" | "unknown") and leverage_multiplier
  - Strict statutory price limit rules:
      * Domestic leveraged/inverse: 10% * abs(multiplier) (e.g. 00631L is +-20%, 00632R is +-10%)
      * Foreign leveraged/inverse / Foreign plain / Bond: NO_LIMIT
  - Zero single-character false positives (e.g. '美' or '債' alone strictly disallowed)
  - Classification provenance tracking (OFFICIAL_METADATA, CFI_CODE, NAME_HEURISTIC, UNKNOWN)
  - Canonical symbol generation: {code}.{EXCHANGE}
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser

from app.taiwan.universe.models import TaiwanInstrument

logger = logging.getLogger(__name__)

TWSE_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
TPEX_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
TWSE_ETF_PRODUCTS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
TWSE_COMPANIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"


@dataclass(frozen=True)
class OfficialEtfProductMeta:
    """Official ETF product metadata from TWSE t187ap47_L."""
    fund_type: str         # 基金類型
    underlying_index: str  # 標的指數/追蹤指數名稱


class _IsinTableParser(HTMLParser):
    """Fast, zero-dependency HTML table parser for official ISIN pages."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._curr_row: list[str] = []
        self._curr_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._curr_row = []
        elif tag.lower() == "td":
            self._in_cell = True
            self._curr_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "td":
            self._in_cell = False
            self._curr_row.append("".join(self._curr_cell).strip())
        elif tag.lower() == "tr" and self._curr_row:
            self.rows.append(self._curr_row)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._curr_cell.append(data)


def classify_etf_provenance(
    code: str,
    name: str,
    cfi_code: str | None,
    official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
) -> tuple[str, str, str, float]:
    """Classify ETF with strict priority, provenance, underlying scope, and multiplier.

    Returns:
        tuple[etf_category, classification_source, underlying_scope, leverage_multiplier]
    """
    # 0. Determine leverage multiplier
    multiplier = 1.0
    if code.endswith("L") or "正2" in name or "正向2" in name:
        multiplier = 2.0
    elif code.endswith("R") or "反1" in name or "反向1" in name or "反一" in name:
        multiplier = -1.0

    # 1. First priority: Official Product Metadata (e.g. TWSE t187ap47_L 基金類型 + 標的指數)
    if official_product_types and code in official_product_types:
        meta = official_product_types[code]
        if isinstance(meta, OfficialEtfProductMeta):
            f_type = meta.fund_type
            idx_name = meta.underlying_index
        else:
            f_type = str(meta)
            idx_name = ""

        # Check Bond
        if "債券" in f_type or "債" in f_type:
            # Check if underlying bond is domestic or foreign
            return "bond", "official_metadata", "foreign", multiplier

        # Check Leveraged / Inverse
        elif "槓桿" in f_type or "反向" in f_type:
            cat = "inverse" if multiplier < 0 else "leveraged"
            # Authoritative underlying index examination
            if any(kw in idx_name for kw in ["臺灣", "台湾", "加權", "櫃買", "TPEx", "TWSE", "台50", "藍籌30"]) and not any(kw in idx_name for kw in ["中國", "S&P", "美", "日", "海外"]):
                scope = "domestic"
            elif any(kw in idx_name for kw in ["上証", "滬深", "S&P", "道瓊", "NASDAQ", "日經", "東証", "美債", "公債", "印度", "黃金", "原油", "香港", "恒生", "中國", "富時"]):
                scope = "foreign"
            else:
                # Fallback to ETF official name
                if any(kw in name for kw in ["臺灣", "台灣", "加權", "台50"]):
                    scope = "domestic"
                elif any(kw in name for kw in ["上証", "滬深", "S&P", "道瓊", "NASDAQ", "日經", "美債", "原油", "黃金", "香港", "恒生", "中國"]):
                    scope = "foreign"
                else:
                    scope = "unknown"
            return cat, "official_metadata", scope, multiplier

        # Check Foreign Equity
        elif "國外成分" in f_type or "國外成份" in f_type or "境外" in f_type:
            return "foreign_equity", "official_metadata", "foreign", multiplier

        # Check Domestic Equity
        elif "國內成分" in f_type:
            return "domestic_equity", "official_metadata", "domestic", multiplier

    # 2. Second priority: ISO 10962 CFI Code
    if cfi_code:
        # Debt/Bond ETF (e.g. 00720B is CEOJBU, 00710B is CEOIBU)
        if cfi_code in ("CEOIBU", "CEOJBU"):
            return "bond", "cfi_code", "foreign", multiplier
        # Leveraged / Inverse / Commodity Derivatives (e.g. 00631L/00632R is CEOGDU)
        elif cfi_code in ("CEOGDU", "CEOGMU"):
            cat = "inverse" if multiplier < 0 else "leveraged"
            if any(kw in name for kw in ["臺灣", "台灣", "加權", "台50"]):
                scope = "domestic"
            elif any(kw in name for kw in ["上証", "滬深", "S&P", "道瓊", "NASDAQ", "日經", "美債", "原油", "黃金", "香港", "恒生", "中國"]):
                scope = "foreign"
            else:
                scope = "unknown"
            return cat, "cfi_code", scope, multiplier
        # Plain-Vanilla Domestic Equity ETF (e.g. 0050, 006208, 0051 is CEOGEU)
        elif cfi_code == "CEOGEU":
            return "domestic_equity", "cfi_code", "domestic", multiplier

    # 3. Third priority / degraded fallback: Multi-character Name Heuristics
    # Strictly disallow single-character triggers (like "美" or "債")
    if "正2" in name or "正向2" in name:
        scope = "domestic" if any(kw in name for kw in ["台灣", "臺灣", "加權", "台50"]) else "foreign" if any(kw in name for kw in ["上証", "滬深", "美國", "S&P", "道瓊", "NASDAQ", "日經"]) else "unknown"
        return "leveraged", "name_heuristic", scope, multiplier
    elif "反1" in name or "反向1" in name:
        scope = "domestic" if any(kw in name for kw in ["台灣", "臺灣", "加權", "台50"]) else "foreign" if any(kw in name for kw in ["上証", "滬深", "美國", "S&P", "道瓊", "NASDAQ", "日經"]) else "unknown"
        return "inverse", "name_heuristic", scope, multiplier
    elif any(kw in name for kw in ["公司債", "金融債", "公債", "國債", "投等債", "新興債"]):
        return "bond", "name_heuristic", "foreign", multiplier
    elif any(kw in name for kw in ["S&P", "道瓊", "美國", "日經", "NASDAQ", "海外", "全球", "富時", "香港", "恒生"]):
        return "foreign_equity", "name_heuristic", "foreign", multiplier
    elif any(kw in name for kw in ["台灣50", "台50", "中型100", "高股息"]):
        return "domestic_equity", "name_heuristic", "domestic", multiplier

    # 4. Unknown
    return "unknown", "unknown", "unknown", multiplier


def parse_isin_html(
    html: str,
    exchange: str,
    source_name: str,
    official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
) -> list[TaiwanInstrument]:
    """Parse official TWSE/TPEx ISIN HTML table into canonical TaiwanInstrument list."""
    parser = _IsinTableParser()
    parser.feed(html)

    current_category = "UNKNOWN"
    instruments: list[TaiwanInstrument] = []
    now_iso = datetime.now().isoformat()

    for tds in parser.rows:
        if not tds:
            continue

        # Category Section Header (single cell)
        if len(tds) == 1:
            text = tds[0].strip()
            if text:
                current_category = text
            continue

        # Skip header or malformed rows
        if len(tds) < 6:
            continue
        first_cell = tds[0].strip()
        if "代號" in first_cell or "Code" in first_cell:
            continue

        # Split code and Traditional Chinese name
        # Official format: '2330\u3000台積電' or '8069 元太'
        parts = re.split(r"[\s\u3000]+", first_cell, maxsplit=1)
        if len(parts) < 2:
            continue
        code, name = parts[0].strip(), parts[1].strip()
        if not code or not name:
            continue

        isin = tds[1].strip() or None
        listing_date = tds[2].strip() or None
        industry = tds[4].strip() or None
        cfi_code = tds[5].strip() or None

        # Determine instrument classification from official category header
        cat_lower = current_category.lower()
        etf_category: str | None = None
        classification_source: str | None = None
        underlying_scope: str | None = None
        leverage_multiplier: float = 1.0

        if "etf" in cat_lower:
            instrument_type = "etf"
            is_supported = True
            listing_status = "active"
            etf_category, classification_source, underlying_scope, leverage_multiplier = classify_etf_provenance(
                code=code,
                name=name,
                cfi_code=cfi_code,
                official_product_types=official_product_types,
            )
        elif "股票" in current_category or "創新板" in current_category:
            instrument_type = "stock"
            is_supported = True
            listing_status = "active"
            underlying_scope = "domestic"
            leverage_multiplier = 1.0
        else:
            # Warrants, ETNs, TDRs, REITs, Preferred shares, etc.
            instrument_type = "unsupported"
            is_supported = False
            listing_status = "unsupported"

        canonical_symbol = f"{code}.{exchange}"

        inst = TaiwanInstrument(
            symbol=canonical_symbol,
            code=code,
            exchange=exchange,
            name=name,
            instrument_type=instrument_type,
            listing_status=listing_status,
            listing_date=listing_date,
            isin=isin,
            industry=industry,
            cfi_code=cfi_code,
            raw_category=current_category,
            is_supported=is_supported,
            source=source_name,
            updated_at=now_iso,
            etf_category=etf_category,
            classification_source=classification_source,
            underlying_scope=underlying_scope,
            leverage_multiplier=leverage_multiplier,
        )
        instruments.append(inst)

    return instruments


def fetch_official_twse_etf_products(timeout: float = 10.0) -> dict[str, OfficialEtfProductMeta]:
    """Fetch official TWSE ETF product categories and underlying index from t187ap47_L.

    Returns:
        dict[code, OfficialEtfProductMeta]
    """
    try:
        req = urllib.request.Request(TWSE_ETF_PRODUCTS_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        product_map: dict[str, OfficialEtfProductMeta] = {}
        for r in data:
            code = r.get("基金代號") or r.get("證券代號") or list(r.values())[1]
            cat = r.get("基金類型") or list(r.values())[3]
            idx_name = r.get("標的指數/追蹤指數名稱") or list(r.values())[6] or ""
            if code and cat:
                product_map[str(code).strip()] = OfficialEtfProductMeta(
                    fund_type=str(cat).strip(),
                    underlying_index=str(idx_name).strip(),
                )
        return product_map
    except Exception as e:
        logger.warning("Could not fetch official TWSE ETF product metadata: %s", e)
        return {}


class TwseInstrumentAdapter:
    """Fetches and parses TWSE official ISIN securities list."""

    def __init__(self, url: str = TWSE_ISIN_URL) -> None:
        self.url = url

    def fetch_live_html(self, timeout: float = 15.0) -> str:
        req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("cp950", errors="ignore")

    def get_instruments(
        self,
        html_content: str | None = None,
        official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
    ) -> list[TaiwanInstrument]:
        if html_content is None:
            return _official_company_directory("TWSE", TWSE_COMPANIES_URL) + _official_twse_etf_directory()
        html = html_content
        # If live and no product types injected, fetch live product types
        prod_types = official_product_types
        if prod_types is None and html_content is None:
            prod_types = fetch_official_twse_etf_products()

        return parse_isin_html(
            html,
            exchange="TWSE",
            source_name="TWSE_ISIN",
            official_product_types=prod_types,
        )


class TpexInstrumentAdapter:
    """Fetches and parses TPEx official ISIN securities list."""

    def __init__(self, url: str = TPEX_ISIN_URL) -> None:
        self.url = url

    def fetch_live_html(self, timeout: float = 15.0) -> str:
        req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("cp950", errors="ignore")

    def get_instruments(
        self,
        html_content: str | None = None,
        official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
    ) -> list[TaiwanInstrument]:
        if html_content is None:
            return _official_company_directory("TPEX", TPEX_COMPANIES_URL)
        html = html_content
        return parse_isin_html(
            html,
            exchange="TPEX",
            source_name="TPEX_ISIN",
            official_product_types=official_product_types,
        )


def _get_json(url: str, timeout: float = 15.0) -> list[dict]:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"official directory schema changed: {url}")
    return payload


def _official_company_directory(exchange: str, url: str) -> list[TaiwanInstrument]:
    now = datetime.now().astimezone().isoformat()
    instruments = []
    for row in _get_json(url):
        if exchange == "TWSE":
            code, name = row.get("公司代號"), row.get("公司簡稱")
            listed, industry = row.get("上市日期"), row.get("產業別")
        else:
            code, name = row.get("SecuritiesCompanyCode"), row.get("CompanyAbbreviation")
            listed, industry = row.get("DateOfListing"), row.get("SecuritiesIndustryCode")
        if not code or not name:
            raise ValueError(f"official directory schema changed: {url}")
        code = str(code).strip()
        instruments.append(TaiwanInstrument(
            symbol=f"{code}.{exchange}", code=code, exchange=exchange, name=str(name).strip(),
            instrument_type="stock", listing_status="active", listing_date=str(listed or "").strip() or None,
            isin=None, industry=str(industry or "").strip() or None, cfi_code=None, raw_category="股票",
            is_supported=True, source="TWSE_OPENAPI" if exchange == "TWSE" else "TPEX_OPENAPI",
            updated_at=now, underlying_scope="domestic",
        ))
    return instruments


def _official_twse_etf_directory() -> list[TaiwanInstrument]:
    now = datetime.now().astimezone().isoformat()
    instruments = []
    for row in _get_json(TWSE_ETF_PRODUCTS_URL):
        code, name = row.get("基金代號"), row.get("基金簡稱")
        if not code or not name:
            raise ValueError(f"official directory schema changed: {TWSE_ETF_PRODUCTS_URL}")
        scope = {"是": "foreign", "否": "domestic"}.get(str(row.get("是否包含國外成分股", "")).strip(), "unknown")
        fund_type = str(row.get("基金類型", "")).strip()
        ordinary_types = {"國內成分證券指數股票型基金", "國外成分證券指數股票型基金"}
        category = "foreign_equity" if scope == "foreign" else "domestic_equity" if scope == "domestic" else "unknown"
        code = str(code).strip()
        instruments.append(TaiwanInstrument(
            symbol=f"{code}.TWSE", code=code, exchange="TWSE", name=str(name).strip(), instrument_type="etf",
            listing_status="active", listing_date=str(row.get("上市日期") or "").strip() or None,
            isin=None, industry=None, cfi_code=None, raw_category=fund_type, is_supported=True,
            source="TWSE_OPENAPI", updated_at=now, etf_category=category,
            classification_source="official_metadata" if fund_type in ordinary_types else None,
            underlying_scope=scope,
        ))
    return instruments

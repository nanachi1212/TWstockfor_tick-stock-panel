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
  - Zero single-character false positives (e.g. '美' or '債' alone strictly disallowed)
  - Classification provenance tracking (OFFICIAL_METADATA, CFI_CODE, NAME_HEURISTIC, UNKNOWN)
  - Canonical symbol generation: {code}.{EXCHANGE}
"""
from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import json
import logging
import re
import urllib.request

from app.taiwan.universe.models import TaiwanInstrument

logger = logging.getLogger(__name__)

TWSE_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
TPEX_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
TWSE_ETF_PRODUCTS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"


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
    official_product_types: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Classify ETF with strict priority and provenance tracking.

    Returns:
        tuple[etf_category, classification_source]
    """
    # 1. First priority: Official Product Metadata (e.g. TWSE t187ap47_L 基金類型)
    if official_product_types and code in official_product_types:
        f_type = official_product_types[code]
        if "債券" in f_type or "債" in f_type:
            return "bond", "official_metadata"
        elif "槓桿" in f_type or "反向" in f_type:
            cat = "inverse" if "反" in name else "leveraged"
            return cat, "official_metadata"
        elif "國外成分" in f_type or "國外成份" in f_type or "境外" in f_type:
            return "foreign_equity", "official_metadata"
        elif "國內成分" in f_type:
            return "domestic_equity", "official_metadata"

    # 2. Second priority: ISO 10962 CFI Code
    if cfi_code:
        # Debt/Bond ETF (e.g. 00720B is CEOJBU, 00710B is CEOIBU)
        if cfi_code in ("CEOIBU", "CEOJBU"):
            return "bond", "cfi_code"
        # Leveraged / Inverse / Commodity Derivatives (e.g. 00631L/00632R is CEOGDU)
        elif cfi_code in ("CEOGDU", "CEOGMU"):
            cat = "inverse" if ("反1" in name or "反向" in name) else "leveraged"
            return cat, "cfi_code"
        # Plain-Vanilla Domestic Equity ETF (e.g. 0050, 006208, 0051 is CEOGEU)
        elif cfi_code == "CEOGEU":
            return "domestic_equity", "cfi_code"

    # 3. Third priority / degraded fallback: Multi-character Name Heuristics
    # Strictly disallow single-character triggers (like "美" or "債")
    if "正2" in name or "正向2" in name:
        return "leveraged", "name_heuristic"
    elif "反1" in name or "反向1" in name:
        return "inverse", "name_heuristic"
    elif any(kw in name for kw in ["公司債", "金融債", "公債", "國債", "投等債", "新興債"]):
        return "bond", "name_heuristic"
    elif any(kw in name for kw in ["S&P", "道瓊", "美國", "日經", "NASDAQ", "海外", "全球", "富時", "香港", "恒生"]):
        return "foreign_equity", "name_heuristic"
    elif any(kw in name for kw in ["台灣50", "台50", "中型100", "高股息"]):
        return "domestic_equity", "name_heuristic"

    # 4. Unknown
    return "unknown", "unknown"


def parse_isin_html(
    html: str,
    exchange: str,
    source_name: str,
    official_product_types: dict[str, str] | None = None,
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

        if "etf" in cat_lower:
            instrument_type = "etf"
            is_supported = True
            listing_status = "active"
            etf_category, classification_source = classify_etf_provenance(
                code=code,
                name=name,
                cfi_code=cfi_code,
                official_product_types=official_product_types,
            )
        elif "股票" in current_category or "創新板" in current_category:
            instrument_type = "stock"
            is_supported = True
            listing_status = "active"
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
        )
        instruments.append(inst)

    return instruments


def fetch_official_twse_etf_products(timeout: float = 10.0) -> dict[str, str]:
    """Fetch official TWSE ETF product categories from t187ap47_L.

    Returns:
        dict[code, 基金類型]
    """
    try:
        req = urllib.request.Request(TWSE_ETF_PRODUCTS_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        product_map: dict[str, str] = {}
        for r in data:
            code = r.get("基金代號") or r.get("證券代號") or list(r.values())[1]
            cat = r.get("基金類型") or list(r.values())[3]
            if code and cat:
                product_map[str(code).strip()] = str(cat).strip()
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
        official_product_types: dict[str, str] | None = None,
    ) -> list[TaiwanInstrument]:
        html = html_content if html_content is not None else self.fetch_live_html()
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
        official_product_types: dict[str, str] | None = None,
    ) -> list[TaiwanInstrument]:
        html = html_content if html_content is not None else self.fetch_live_html()
        return parse_isin_html(
            html,
            exchange="TPEX",
            source_name="TPEX_ISIN",
            official_product_types=official_product_types,
        )

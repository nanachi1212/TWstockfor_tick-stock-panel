"""Official TWSE & TPEx ISIN Open Data Adapters.

Fetches and normalizes the official Taiwan Securities Master from:
  - TWSE (strMode=2): https://isin.twse.com.tw/isin/C_public.jsp?strMode=2
  - TPEx (strMode=4): https://isin.twse.com.tw/isin/C_public.jsp?strMode=4

Strictly adheres to:
  - Zero external dependencies (uses standard library html.parser.HTMLParser)
  - Offline testing support via injected HTML
  - Accurate Traditional Chinese security names
  - Explicit instrument classification (stock, etf, unsupported)
  - Canonical symbol generation: {code}.{EXCHANGE}
"""
from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import logging
import re
import urllib.request

from app.taiwan.universe.models import TaiwanInstrument

logger = logging.getLogger(__name__)

TWSE_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
TPEX_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"


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


def parse_isin_html(html: str, exchange: str, source_name: str) -> list[TaiwanInstrument]:
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
        if "etf" in cat_lower:
            instrument_type = "etf"
            is_supported = True
            listing_status = "active"
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
        )
        instruments.append(inst)

    return instruments


class TwseInstrumentAdapter:
    """Fetches and parses TWSE official ISIN securities list."""

    def __init__(self, url: str = TWSE_ISIN_URL) -> None:
        self.url = url

    def fetch_live_html(self, timeout: float = 15.0) -> str:
        req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("cp950", errors="ignore")

    def get_instruments(self, html_content: str | None = None) -> list[TaiwanInstrument]:
        html = html_content if html_content is not None else self.fetch_live_html()
        return parse_isin_html(html, exchange="TWSE", source_name="TWSE_ISIN")


class TpexInstrumentAdapter:
    """Fetches and parses TPEx official ISIN securities list."""

    def __init__(self, url: str = TPEX_ISIN_URL) -> None:
        self.url = url

    def fetch_live_html(self, timeout: float = 15.0) -> str:
        req = urllib.request.Request(self.url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("cp950", errors="ignore")

    def get_instruments(self, html_content: str | None = None) -> list[TaiwanInstrument]:
        html = html_content if html_content is not None else self.fetch_live_html()
        return parse_isin_html(html, exchange="TPEX", source_name="TPEX_ISIN")

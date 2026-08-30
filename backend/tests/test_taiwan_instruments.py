"""Unit tests for Taiwan Instrument Adapters and Parsing.

Tests are 100% offline using injected HTML samples (zero internet dependencies).
Covers:
  - TWSE HTML parsing (stocks, ETFs, warrants, preferred shares)
  - TPEx HTML parsing (stocks, ETFs, warrants)
  - Canonical symbol generation: {code}.{EXCHANGE}
  - Traditional Chinese name extraction
  - Instrument classification: stock vs etf vs unsupported
  - ISIN, listing date, industry, and CFI code extraction
"""
from __future__ import annotations

import pytest

from app.taiwan.universe.adapters import parse_isin_html


TWSE_SAMPLE_HTML = """
<html>
<body>
<table class="h4">
  <tr><td colspan="7"><b>股票</b></td></tr>
  <tr>
    <td>有價證券代號及名稱</td><td>國際證券辨識號碼(ISIN Code)</td><td>上市日</td><td>市場別</td><td>產業別</td><td>CFICode</td><td>備註</td>
  </tr>
  <tr>
    <td>2330　台積電</td><td>TW0002330008</td><td>1994/09/05</td><td>上市</td><td>半導體業</td><td>ESVUFR</td><td></td>
  </tr>
  <tr>
    <td>2454　聯發科</td><td>TW0002454006</td><td>2001/07/23</td><td>上市</td><td>半導體業</td><td>ESVUFR</td><td></td>
  </tr>
  <tr><td colspan="7"><b>ETF</b></td></tr>
  <tr>
    <td>0050　元大台灣50</td><td>TW0000050004</td><td>2003/06/30</td><td>上市</td><td></td><td>CEOJEU</td><td></td>
  </tr>
  <tr>
    <td>006208　富邦台50</td><td>TW0000062082</td><td>2012/07/17</td><td>上市</td><td></td><td>CEOJEU</td><td></td>
  </tr>
  <tr><td colspan="7"><b>特別股</b></td></tr>
  <tr>
    <td>1101B　台泥乙特</td><td>TW0001101B05</td><td>2019/01/29</td><td>上市</td><td></td><td>EPNRAR</td><td></td>
  </tr>
  <tr><td colspan="7"><b>認購(售)權證</b></td></tr>
  <tr>
    <td>052330　台積電富邦59購01</td><td>TW26Z0523301</td><td>2026/01/14</td><td>上市</td><td></td><td>RWSCPE</td><td></td>
  </tr>
</table>
</body>
</html>
"""

TPEX_SAMPLE_HTML = """
<html>
<body>
<table class="h4">
  <tr><td colspan="7"><b>股票</b></td></tr>
  <tr>
    <td>有價證券代號及名稱</td><td>國際證券辨識號碼(ISIN Code)</td><td>上市日</td><td>市場別</td><td>產業別</td><td>CFICode</td><td>備註</td>
  </tr>
  <tr>
    <td>8069　元太</td><td>TW0008069006</td><td>2004/03/30</td><td>上櫃</td><td>光電業</td><td>ESVUFR</td><td></td>
  </tr>
  <tr><td colspan="7"><b>ETF</b></td></tr>
  <tr>
    <td>00720B　元大投資級公司債</td><td>TW00000720B5</td><td>2018/01/09</td><td>上櫃</td><td></td><td>CEOIEU</td><td></td>
  </tr>
  <tr><td colspan="7"><b>上櫃認購(售)權證</b></td></tr>
  <tr>
    <td>701623　元太統一5B購01</td><td>TW25Z7016231</td><td>2025/11/03</td><td>上櫃</td><td></td><td>RWSCCA</td><td></td>
  </tr>
</table>
</body>
</html>
"""


class TestTaiwanInstrumentAdaptersOffline:
    """Offline parsing tests using canonical HTML fixtures."""

    def test_twse_parsing_stocks_and_etfs(self):
        items = parse_isin_html(TWSE_SAMPLE_HTML, exchange="TWSE", source_name="TWSE_ISIN")
        by_symbol = {inst.symbol: inst for inst in items}

        # 1. 2330 TSMC
        tsmc = by_symbol.get("2330.TWSE")
        assert tsmc is not None
        assert tsmc.code == "2330"
        assert tsmc.name == "台積電"
        assert tsmc.exchange == "TWSE"
        assert tsmc.instrument_type == "stock"
        assert tsmc.is_supported is True
        assert tsmc.isin == "TW0002330008"
        assert tsmc.listing_date == "1994/09/05"
        assert tsmc.industry == "半導體業"
        assert tsmc.cfi_code == "ESVUFR"

        # 2. 0050 ETF
        etf0050 = by_symbol.get("0050.TWSE")
        assert etf0050 is not None
        assert etf0050.name == "元大台灣50"
        assert etf0050.instrument_type == "etf"
        assert etf0050.is_supported is True
        assert etf0050.isin == "TW0000050004"

        # 3. Preferred shares (1101B) -> unsupported
        pref = by_symbol.get("1101B.TWSE")
        assert pref is not None
        assert pref.instrument_type == "unsupported"
        assert pref.is_supported is False

        # 4. Warrant (052330) -> unsupported
        warrant = by_symbol.get("052330.TWSE")
        assert warrant is not None
        assert warrant.instrument_type == "unsupported"
        assert warrant.is_supported is False

    def test_tpex_parsing_stocks_and_bond_etf(self):
        items = parse_isin_html(TPEX_SAMPLE_HTML, exchange="TPEX", source_name="TPEX_ISIN")
        by_symbol = {inst.symbol: inst for inst in items}

        # 1. 8069 E-Ink
        eink = by_symbol.get("8069.TPEX")
        assert eink is not None
        assert eink.code == "8069"
        assert eink.name == "元太"
        assert eink.exchange == "TPEX"
        assert eink.instrument_type == "stock"
        assert eink.is_supported is True
        assert eink.industry == "光電業"

        # 2. 00720B TPEx Bond ETF
        bond_etf = by_symbol.get("00720B.TPEX")
        assert bond_etf is not None
        assert bond_etf.instrument_type == "etf"
        assert bond_etf.is_supported is True

        # 3. TPEx Warrant -> unsupported
        warrant = by_symbol.get("701623.TPEX")
        assert warrant is not None
        assert warrant.instrument_type == "unsupported"
        assert warrant.is_supported is False

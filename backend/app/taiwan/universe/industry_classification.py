"""Canonical Taiwan industry code -> readable name normalization.

Background (Phase 8C-C Final Completion):
  TWSE's official company-directory API (t187ap03_L) and TPEx's equivalent
  (mopsfin_t187ap03_O) both return the numeric TWSE/TPEx industry
  classification code (e.g. "01", "24") in their "產業別" /
  "SecuritiesIndustryCode" field — NOT a readable name. `TaiwanInstrument.industry`
  is documented and consumed everywhere (industry_intelligence, screener,
  abnormal_diagnostics, research_context) as a readable Chinese name (e.g.
  "半導體業"), so those numeric codes leaked through to end users as raw
  "01"/"24"/"91" values.

  The readable name for each code IS available from an official source: the
  TWSE/TPEx ISIN tables (isin.twse.com.tw, strMode=2/4) carry a "產業別"
  column with the actual Chinese industry name for every listed stock. This
  mapping was derived by cross-referencing, for ~1,900+ listed companies, the
  numeric code from the official company-directory APIs against the readable
  name from the official ISIN tables for the *same company code* — every one
  of the 34 codes below resolved to exactly one name with zero conflicts
  across all companies sharing that code. This is the TWSE/TPEx standard
  33-industry classification scheme.

  Code "91" is intentionally excluded: every company using it in the official
  directories is a Taiwan Depositary Receipt ("-DR" suffix name), i.e. "91"
  is an instrument-type marker (foreign company via TDR), not a real
  industry — there is no genuine industry name to map it to, so it correctly
  falls through to the honest "unknown code" fallback below rather than being
  assigned a fabricated label.
"""
from __future__ import annotations

# Verified 2026-09-09 by cross-referencing official TWSE (t187ap03_L) and TPEx
# (mopsfin_t187ap03_O) numeric industry codes against the readable "產業別"
# column of the official TWSE/TPEx ISIN tables (isin.twse.com.tw strMode=2/4),
# joined on company code. Zero conflicts across all companies checked.
TAIWAN_INDUSTRY_CODE_TO_NAME: dict[str, str] = {
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "14": "建材營造業",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險業",
    "18": "貿易百貨業",
    "20": "其他業",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}


def resolve_industry_name(raw: str | None) -> str | None:
    """Normalize a raw `industry` value to a canonical readable name.

    - None / empty -> None (e.g. ETFs, which have no industry classification).
    - A known numeric code (e.g. "24") -> its verified readable name ("半導體業").
    - An unrecognized numeric code -> an honest, visibly-flagged fallback —
      never a guessed or fabricated industry name.
    - Anything else (already a readable name, from a correctly-ingested row,
      or from the ISIN-HTML parsing path) -> returned unchanged. This makes
      the function idempotent/safe to apply to already-normalized data.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if value in TAIWAN_INDUSTRY_CODE_TO_NAME:
        return TAIWAN_INDUSTRY_CODE_TO_NAME[value]
    if value.isdigit():
        return f"未知產業（{value}）"
    return value

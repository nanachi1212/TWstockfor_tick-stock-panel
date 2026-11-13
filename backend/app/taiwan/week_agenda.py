"""本週行事曆: 重要經濟數據 (Forex Factory) 與美股大型／科技股財報 (Nasdaq)。

本週 = 台北時間週一～週日；週日 12:00 後 (Forex Factory 換週) 改看下一週。
成功資料沿用 3 小時、失敗 5 分鐘後才重試；任一來源失敗時保留上次成功的那一份並標示錯誤。
Ported from EquiLens (MIT) briefing week agenda. Display context only, not market data.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from app.taiwan.providers.http import fetch_json
from app.taiwan.realtime.calendar import TAIPEI_TZ, taipei_now

logger = logging.getLogger(__name__)

FOREX_FACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NASDAQ_EARNINGS_URL = "https://api.nasdaq.com/api/calendar/earnings?date={day}"
SOURCES = [
    {"title": "Forex Factory 經濟行事曆", "url": "https://www.forexfactory.com/calendar"},
    {"title": "Nasdaq 財報行事曆", "url": "https://www.nasdaq.com/market-activity/earnings"},
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}
_FRESH_SECONDS = 3 * 3600
_RETRY_AFTER_ERROR_SECONDS = 5 * 60

COUNTRY = {"USD": "美國", "CNY": "中國", "EUR": "歐元區", "JPY": "日本"}
HOLIDAY_COUNTRIES = {"USD", "CNY", "JPY"}
_ZH: list[tuple[str, str]] = [
    (r"Non-Farm Employment Change", "非農就業人數"), (r"ADP Non-Farm Employment Change", "ADP 民間就業人數"),
    (r"Unemployment Rate", "失業率"), (r"Average Hourly Earnings m/m", "平均時薪（月增）"),
    (r"Unemployment Claims", "初領失業救濟金人數"), (r"JOLTS Job Openings", "JOLTS 職缺數"),
    (r"Core PCE Price Index m/m", "核心 PCE 物價指數（月增）"), (r"PCE Price Index m/m", "PCE 物價指數（月增）"),
    (r"Core CPI m/m", "核心 CPI（月增）"), (r"CPI m/m", "CPI（月增）"), (r"CPI y/y", "CPI（年增）"),
    (r"Core CPI y/y", "核心 CPI（年增）"), (r"Core PPI m/m", "核心 PPI（月增）"), (r"PPI m/m", "PPI（月增）"),
    (r"PPI y/y", "PPI（年增）"), (r"Advance GDP q/q", "GDP 初值（季增年率）"), (r"Prelim GDP q/q", "GDP 修正值（季增年率）"),
    (r"Final GDP q/q", "GDP 終值（季增年率）"), (r"(Advance|Prelim|Final) GDP Price Index q/q", "GDP 物價平減指數"),
    (r"GDP q/q", "GDP（季增）"), (r"GDP y/y", "GDP（年增）"), (r"ISM Manufacturing PMI", "ISM 製造業 PMI"),
    (r"ISM Services PMI", "ISM 服務業 PMI"), (r"ISM Manufacturing Prices", "ISM 製造業物價"),
    (r"Flash Manufacturing PMI", "製造業 PMI 初值"), (r"Flash Services PMI", "服務業 PMI 初值"),
    (r"Final Manufacturing PMI", "製造業 PMI 終值"), (r"Final Services PMI", "服務業 PMI 終值"),
    (r"Manufacturing PMI", "官方製造業 PMI"), (r"Non-Manufacturing PMI", "官方非製造業 PMI"),
    (r"Caixin Manufacturing PMI", "財新製造業 PMI"), (r"Caixin Services PMI", "財新服務業 PMI"),
    (r"Retail Sales m/m", "零售銷售（月增）"), (r"Core Retail Sales m/m", "核心零售銷售（月增）"),
    (r"Durable Goods Orders m/m", "耐久財訂單（月增）"), (r"Core Durable Goods Orders m/m", "核心耐久財訂單（月增）"),
    (r"Industrial Production m/m", "工業生產（月增）"), (r"Industrial Production y/y", "工業生產（年增）"),
    (r"CB Consumer Confidence", "諮商會消費者信心指數"), (r"Prelim UoM Consumer Sentiment", "密大消費者信心初值"),
    (r"Revised UoM Consumer Sentiment", "密大消費者信心終值"), (r"Prelim UoM Inflation Expectations", "密大通膨預期初值"),
    (r"Revised UoM Inflation Expectations", "密大通膨預期終值"), (r"Empire State Manufacturing Index", "紐約州製造業指數"),
    (r"Philly Fed Manufacturing Index", "費城聯儲製造業指數"), (r"Pending Home Sales m/m", "成屋待完成銷售（月增）"),
    (r"Existing Home Sales", "成屋銷售"), (r"New Home Sales", "新屋銷售"), (r"Building Permits", "營建許可"),
    (r"Housing Starts", "新屋開工"), (r"Trade Balance", "貿易收支"), (r"Crude Oil Inventories", "EIA 原油庫存"),
    (r"Federal Funds Rate", "Fed 利率決策"), (r"FOMC Statement", "FOMC 聲明"), (r"FOMC Press Conference", "FOMC 記者會"),
    (r"FOMC Meeting Minutes", "FOMC 會議紀要"), (r"FOMC Economic Projections", "FOMC 經濟預測"),
    (r"Fed Chair (.+) Speaks", r"Fed 主席 \1 談話"), (r"FOMC Member (.+) Speaks", r"Fed 官員 \1 談話"),
    (r"Main Refinancing Rate", "ECB 主要再融資利率"), (r"Monetary Policy Statement", "貨幣政策聲明"),
    (r"ECB Press Conference", "ECB 記者會"), (r"ECB President (.+) Speaks", r"ECB 總裁 \1 談話"),
    (r"BOJ Policy Rate", "日銀政策利率"), (r"BOJ Press Conference", "日銀記者會"), (r"BOJ Gov (.+) Speaks", r"日銀總裁 \1 談話"),
    (r"Tankan Manufacturing Index", "短觀大型製造業指數"), (r"Core CPI Flash Estimate y/y", "核心 CPI 快估（年增）"),
    (r"CPI Flash Estimate y/y", "CPI 快估（年增）"), (r"German Prelim CPI m/m", "德國 CPI 初值（月增）"),
    (r"Tokyo Core CPI y/y", "東京核心 CPI（年增）"), (r"1-y Loan Prime Rate|Loan Prime Rate", "一年期 LPR"),
    (r"New Loans", "新增人民幣貸款"),
]
_ZH_RE = [(re.compile(f"^(?:{pattern})$"), zh) for pattern, zh in _ZH]

TECH = {
    "AAPL": "蘋果", "MSFT": "微軟", "NVDA": "輝達", "GOOGL": "Alphabet", "GOOG": "Alphabet", "AMZN": "亞馬遜",
    "META": "Meta", "TSLA": "特斯拉", "AVGO": "博通", "AMD": "超微", "TSM": "台積電 ADR", "MU": "美光", "ORCL": "甲骨文",
    "INTC": "英特爾", "QCOM": "高通", "ASML": "艾司摩爾", "ADBE": "Adobe", "CRM": "Salesforce", "NFLX": "網飛",
    "ARM": "安謀", "SMCI": "美超微", "DELL": "戴爾", "HPE": "慧與", "HPQ": "惠普", "MRVL": "邁威爾", "AMAT": "應用材料",
    "LRCX": "科林研發", "KLAC": "科磊", "TXN": "德州儀器", "ADI": "亞德諾", "NXPI": "恩智浦", "ON": "安森美",
    "MCHP": "微芯", "IBM": "IBM", "CSCO": "思科", "ANET": "Arista", "NOW": "ServiceNow", "PLTR": "Palantir",
    "SNOW": "Snowflake", "CRWD": "CrowdStrike", "PANW": "Palo Alto", "INTU": "Intuit", "CDNS": "益華電腦",
    "SNPS": "新思科技", "WDC": "威騰", "STX": "希捷", "SNDK": "SanDisk", "JBL": "捷普", "CLS": "Celestica",
    "VRT": "Vertiv", "GLW": "康寧", "APH": "安費諾", "COHR": "Coherent", "TER": "泰瑞達", "KEYS": "是德科技",
    "UMC": "聯電 ADR", "ASX": "日月光 ADR", "SAP": "SAP", "SONY": "索尼", "BABA": "阿里巴巴", "BIDU": "百度",
    "PDD": "拼多多", "UBER": "Uber", "SHOP": "Shopify", "DDOG": "Datadog", "MDB": "MongoDB", "NET": "Cloudflare",
    "ZS": "Zscaler", "WDAY": "Workday", "ACN": "埃森哲",
}
OTHER_ZH = {
    "JPM": "摩根大通", "BAC": "美國銀行", "WFC": "富國銀行", "C": "花旗", "GS": "高盛", "MS": "摩根士丹利",
    "WMT": "沃爾瑪", "COST": "好市多", "NKE": "Nike", "FDX": "聯邦快遞", "UNH": "聯合健康", "JNJ": "嬌生",
    "LLY": "禮來", "PG": "寶僑", "KO": "可口可樂", "PEP": "百事", "XOM": "埃克森美孚", "V": "Visa", "MA": "萬事達卡",
}
BIG_CAP_USD = 100e9
_TIMING = {"time-pre-market": "盤前", "time-after-hours": "盤後"}
_TIMING_ORDER = {"盤前": 0, "盤後": 1, "時間未定": 2}

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def week_range(now: datetime) -> tuple[date, date]:
    local = now.astimezone(TAIPEI_TZ)
    monday = local.date() - timedelta(days=local.weekday())
    if local.weekday() == 6 and local.hour >= 12:
        monday += timedelta(days=7)
    return monday, monday + timedelta(days=6)


def zh_title(title: str) -> str | None:
    for pattern, zh in _ZH_RE:
        if pattern.match(title):
            return pattern.sub(zh, title)
    return None


def map_macro(items: Any, start: date, end: date) -> list[dict[str, Any]]:
    out = []
    for item in items if isinstance(items, list) else []:
        country, impact = item.get("country"), item.get("impact")
        holiday = impact == "Holiday"
        if country not in COUNTRY or not (impact in ("High", "Medium") or (holiday and country in HOLIDAY_COUNTRIES)):
            continue
        try:
            at = datetime.fromisoformat(str(item.get("date"))).astimezone(TAIPEI_TZ)
        except ValueError:
            continue
        if not start <= at.date() <= end:
            continue
        title = str(item.get("title") or "")
        out.append({
            "time": at.isoformat(), "date": at.date().isoformat(), "country": country, "country_name": COUNTRY[country],
            "impact": impact, "holiday": holiday, "title": "金融市場休假" if holiday else zh_title(title) or title,
            "title_en": title, "forecast": item.get("forecast") or "", "previous": item.get("previous") or "",
        })
    return sorted(out, key=lambda e: e["time"])


def _usd(value: Any) -> float | None:
    try:
        n = float(str(value or "").replace("$", "").replace(",", ""))
    except ValueError:
        return None
    return n if n > 0 else None


def map_earnings(rows: Any, day: date) -> list[dict[str, Any]]:
    out = []
    for row in rows if isinstance(rows, list) else []:
        symbol = str(row.get("symbol") or "").strip()
        cap = _usd(row.get("marketCap"))
        if not symbol or not (symbol in TECH or (cap or 0) >= BIG_CAP_USD):
            continue
        out.append({
            "date": day.isoformat(), "symbol": symbol, "name": row.get("name") or symbol,
            "zh": TECH.get(symbol) or OTHER_ZH.get(symbol), "tech": symbol in TECH, "market_cap": cap,
            "timing": _TIMING.get(str(row.get("time")), "時間未定"), "eps_forecast": row.get("epsForecast") or "",
        })
    return sorted(out, key=lambda r: (_TIMING_ORDER[r["timing"]], not r["tech"], -(r["market_cap"] or 0)))


def week_agenda(*, now: datetime | None = None, fetch: Callable[..., Any] = fetch_json) -> dict[str, Any]:
    global _cache
    now = now or taipei_now()
    start, end = week_range(now)
    mono = time.monotonic()
    with _lock:
        cached = _cache if _cache and _cache["start"] == start.isoformat() else None
        if cached is not None:
            age = mono - cached["_fetched_mono"]
            if age < (_RETRY_AFTER_ERROR_SECONDS if cached["errors"] else _FRESH_SECONDS):
                return {k: v for k, v in cached.items() if not k.startswith("_")}
        errors: list[str] = []
        macro = cached["macro"] if cached else []
        earnings = cached["earnings"] if cached else []
        keep = "，顯示上次資料" if cached else ""
        try:
            macro = map_macro(fetch(FOREX_FACTORY_URL, headers=_HEADERS, timeout=20.0), start, end)
        except Exception as exc:
            logger.warning("Forex Factory calendar fetch failed: %s", type(exc).__name__)
            errors.append(f"經濟行事曆（Forex Factory）暫時無法連線{keep}")
        try:
            fresh: list[dict[str, Any]] = []
            for offset in range(5):  # Mon-Fri; US earnings are not reported on weekends
                day = start + timedelta(days=offset)
                payload = fetch(NASDAQ_EARNINGS_URL.format(day=day.isoformat()), headers=_HEADERS, timeout=20.0)
                fresh.extend(map_earnings(((payload or {}).get("data") or {}).get("rows"), day))
            earnings = fresh
        except Exception as exc:
            logger.warning("Nasdaq earnings calendar fetch failed: %s", type(exc).__name__)
            errors.append(f"財報行事曆（Nasdaq）暫時無法連線{keep}")
        _cache = {
            "start": start.isoformat(), "end": end.isoformat(), "macro": macro, "earnings": earnings,
            "errors": errors, "sources": SOURCES, "fetched_at": now.isoformat(), "_fetched_mono": mono,
        }
        return {k: v for k, v in _cache.items() if not k.startswith("_")}

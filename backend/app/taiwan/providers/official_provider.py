"""TWSE/TPEx official quote and daily K-line adapter."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import polars as pl

from app.data_providers.base import AssetType
from app.taiwan.providers.base import AmountUnit, PriceSemantics, SourceMetadata, VolumeUnit
from app.taiwan.providers.normalizer import normalize_taiwan_daily
from app.taiwan.providers.taiwan_values import (
    TAIPEI,
    market_close,
    official_status,
    parse_number,
    parse_taiwan_date,
)
from app.taiwan.symbol import Exchange, TaiwanSymbol, parse_symbol

TWSE_QUOTE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_QUOTE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
TWSE_MONTH_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TPEX_MONTH_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"

OFFICIAL_METADATA = SourceMetadata(
    source_name="taiwan_official", volume_unit=VolumeUnit.SHARES,
    amount_unit=AmountUnit.TWD, price_semantics=PriceSemantics.RAW,
)


class OfficialTaiwanAdapter:
    metadata = OFFICIAL_METADATA

    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    def _json(self, url: str) -> object:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8-sig"))

    def fetch_quote(self, symbols: list[str | TaiwanSymbol]) -> pl.DataFrame:
        wanted = [parse_symbol(s) if isinstance(s, str) else s for s in symbols]
        grouped = {Exchange.TWSE: [], Exchange.TPEX: []}
        for symbol in wanted:
            grouped[symbol.exchange].append(symbol)
        frames: list[pl.DataFrame] = []
        if grouped[Exchange.TWSE]:
            frames.append(self._quote_rows(grouped[Exchange.TWSE], TWSE_QUOTE_URL, self._json(TWSE_QUOTE_URL)))
        if grouped[Exchange.TPEX]:
            frames.append(self._quote_rows(grouped[Exchange.TPEX], TPEX_QUOTE_URL, self._json(TPEX_QUOTE_URL)))
        return pl.concat([f for f in frames if not f.is_empty()], how="diagonal_relaxed") if any(not f.is_empty() for f in frames) else pl.DataFrame()

    def _quote_rows(self, symbols: list[TaiwanSymbol], url: str, payload: object) -> pl.DataFrame:
        rows_by_code = {}
        for row in payload if isinstance(payload, list) else []:
            code = row.get("Code") or row.get("SecuritiesCompanyCode")
            if code:
                rows_by_code[str(code).strip()] = row
        frames = []
        retrieved_at = datetime.now(TAIPEI).isoformat()
        for symbol in symbols:
            row = rows_by_code.get(symbol.code)
            if not row:
                continue
            is_twse = symbol.exchange == Exchange.TWSE
            trade_date = parse_taiwan_date(row["Date"])
            raw = [{
                "date": trade_date.isoformat(),
                "open": parse_number(row["OpeningPrice" if is_twse else "Open"]),
                "high": parse_number(row["HighestPrice" if is_twse else "High"]),
                "low": parse_number(row["LowestPrice" if is_twse else "Low"]),
                "close": parse_number(row["ClosingPrice" if is_twse else "Close"]),
                "volume": parse_number(row["TradeVolume" if is_twse else "TradingShares"]),
                "amount": parse_number(row["TradeValue" if is_twse else "TransactionAmount"]),
                "market_timestamp": market_close(trade_date),
            }]
            frames.append(normalize_taiwan_daily(
                raw, self.metadata, symbol,
                provenance={"provider": "twse" if is_twse else "tpex", "source": "official_quote", "source_url": url,
                            "retrieved_at": retrieved_at, "trade_date": trade_date.isoformat(),
                            "status": official_status(trade_date)},
            ))
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def fetch_daily(self, symbols: list[str | TaiwanSymbol], start_time: datetime | None,
                    end_time: datetime | None, asset_type: AssetType = "stock") -> pl.DataFrame:
        del asset_type
        parsed = [parse_symbol(s) if isinstance(s, str) else s for s in symbols]
        start = start_time.date() if start_time else None
        end = end_time.date() if end_time else datetime.now().date()
        frames: list[pl.DataFrame] = []
        for symbol in parsed:
            cursor = end.replace(day=1)
            earliest = start or cursor
            while cursor >= earliest.replace(day=1):
                frame = self._fetch_month(symbol, cursor)
                if not frame.is_empty():
                    if start:
                        frame = frame.filter(pl.col("date") >= start)
                    frame = frame.filter(pl.col("date") <= end)
                    frames.append(frame)
                cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
        return pl.concat(frames, how="diagonal_relaxed").unique(["symbol", "date"]).sort(["symbol", "date"]) if frames else pl.DataFrame()

    def _fetch_month(self, symbol: TaiwanSymbol, month: object) -> pl.DataFrame:
        is_twse = symbol.exchange == Exchange.TWSE
        if is_twse:
            params = {"response": "json", "date": month.strftime("%Y%m01"), "stockNo": symbol.code}
            url = f"{TWSE_MONTH_URL}?{urllib.parse.urlencode(params)}"
        else:
            params = {"response": "json", "date": month.strftime("%Y/%m/01"), "code": symbol.code}
            url = f"{TPEX_MONTH_URL}?{urllib.parse.urlencode(params)}"
        payload = self._json(url)
        rows = payload.get("data", []) if is_twse else ((payload.get("tables") or [{}])[0].get("data", []))
        retrieved_at = datetime.now(TAIPEI).isoformat()
        raw = []
        for values in rows:
            if len(values) < 8:
                raise ValueError(f"official monthly schema changed for {symbol.canonical}")
            trade_date = parse_taiwan_date(values[0])
            volume = parse_number(values[1])
            amount = parse_number(values[2])
            raw.append({"date": trade_date.isoformat(), "open": parse_number(values[3]), "high": parse_number(values[4]),
                        "low": parse_number(values[5]), "close": parse_number(values[6]),
                        "volume": volume if is_twse or volume is None else volume * 1000,
                        "amount": amount if is_twse or amount is None else amount * 1000,
                        "market_timestamp": market_close(trade_date)})
        metadata = self.metadata
        return normalize_taiwan_daily(raw, metadata, symbol, provenance={
            "provider": "twse" if is_twse else "tpex", "source": "official_daily_kline", "source_url": url,
            "retrieved_at": retrieved_at, "trade_date": None, "status": "official",
        })

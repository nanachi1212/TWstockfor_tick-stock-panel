"""Daily CBC foreign-exchange context from the public Frankfurter v2 API."""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from app.config import settings
from app.taiwan.providers.external_models import (
    ExternalProviderResult,
    ExternalStatus,
    read_json_cache,
    write_json_cache,
)
from app.taiwan.providers.http import fetch_json

FRANKFURTER_SOURCE = "frankfurter:v2:provider:CBC"
FRANKFURTER_URL = "https://api.frankfurter.dev/v2/providers/cbc/rates"
FX_CACHE_TTL = timedelta(hours=24)
FX_STALE_AFTER = timedelta(days=7)
_PAIRS = ("USD/TWD", "JPY/TWD", "EUR/TWD")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _change(current: float, previous: float | None) -> float | None:
    if previous is None or previous == 0:
        return None
    return round((current / previous - 1.0) * 100.0, 3)


def normalize_cbc_rates(rows: Any, *, retrieved_at: str) -> ExternalProviderResult:
    """Build three TWD pairs from one CBC-sourced USD matrix."""
    if not isinstance(rows, list):
        return ExternalProviderResult.unavailable(FRANKFURTER_SOURCE, "invalid_response")
    by_date: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        if not isinstance(row, dict) or str(row.get("base", "")).upper() != "USD":
            continue
        day = str(row.get("date") or "")
        quote = str(row.get("quote") or "").upper()
        rate = _number(row.get("rate"))
        if day and quote in {"TWD", "JPY", "EUR"} and rate is not None and rate > 0:
            by_date[day][quote] = rate
    series: dict[str, list[tuple[str, float]]] = {pair: [] for pair in _PAIRS}
    for day in sorted(by_date):
        values = by_date[day]
        if not all(currency in values for currency in ("TWD", "JPY", "EUR")):
            continue
        series["USD/TWD"].append((day, values["TWD"]))
        series["JPY/TWD"].append((day, values["TWD"] / values["JPY"]))
        series["EUR/TWD"].append((day, values["TWD"] / values["EUR"]))
    if not series["USD/TWD"]:
        return ExternalProviderResult.unavailable(FRANKFURTER_SOURCE, "missing_rates")
    pair_data: dict[str, dict[str, Any]] = {}
    for pair, observations in series.items():
        latest_day, latest = observations[-1]
        pair_data[pair] = {
            "current": round(latest, 5),
            "change_5d_pct": _change(latest, observations[-6][1] if len(observations) > 5 else None),
            "change_20d_pct": _change(latest, observations[-21][1] if len(observations) > 20 else None),
            "change_60d_pct": _change(latest, observations[-61][1] if len(observations) > 60 else None),
            "observed_date": latest_day,
        }
    latest_date = series["USD/TWD"][-1][0]
    try:
        age = datetime.now(UTC).date() - date.fromisoformat(latest_date)
    except ValueError:
        age = timedelta.max
    status: ExternalStatus = "stale" if age > FX_STALE_AFTER else "available"
    usd_20d = pair_data["USD/TWD"]["change_20d_pct"]
    if usd_20d is None:
        summary = "匯率歷史不足，暫時無法判斷台幣強弱。"
    elif usd_20d >= 1.0:
        summary = "匯率環境：台幣偏弱。美元兌台幣近 20 個觀測日走強；出口型公司通常較有利，進口成本較高的公司可能承壓。"
    elif usd_20d <= -1.0:
        summary = "匯率環境：台幣偏強。美元兌台幣近 20 個觀測日走弱；進口成本壓力可能降低，出口換匯利益可能減少。"
    else:
        summary = "匯率環境：台幣大致中性。美元兌台幣近 20 個觀測日變動不大。"
    return ExternalProviderResult(
        source=FRANKFURTER_SOURCE,
        status=status,
        as_of=latest_date,
        retrieved_at=retrieved_at,
        freshness="daily" if status == "available" else "stale",
        data={"pairs": pair_data, "summary": summary, "context_only": True},
        error_reason="latest_rate_too_old" if status == "stale" else None,
    )


class FrankfurterFxProvider:
    def __init__(self, *, cache_path: Path | None = None, fetcher=fetch_json) -> None:
        self.cache_path = cache_path or Path(settings.data_dir) / "external_context" / "frankfurter_cbc.json"
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._memory: ExternalProviderResult | None = None

    def get_context(self, *, now: datetime | None = None) -> ExternalProviderResult:
        current = now or datetime.now(UTC)
        with self._lock:
            cached = self._memory or self._read_cache()
            if cached is not None and self._cache_is_fresh(cached, current):
                self._memory = cached
                return cached.model_copy(deep=True)
            start = (current.date() - timedelta(days=120)).isoformat()
            url = FRANKFURTER_URL + "?" + urlencode({
                "from": start, "base": "USD", "quotes": "TWD,JPY,EUR",
            })
            retrieved_at = current.isoformat()
            try:
                result = normalize_cbc_rates(
                    self.fetcher(url, timeout=20.0, max_attempts=3),
                    retrieved_at=retrieved_at,
                )
            except Exception:
                if cached is not None:
                    result = cached.model_copy(update={
                        "status": "stale", "freshness": "stale", "error_reason": "provider_unavailable",
                    })
                else:
                    result = ExternalProviderResult.unavailable(FRANKFURTER_SOURCE, "provider_unavailable")
                    result.retrieved_at = retrieved_at
            self._memory = result
            if result.data is not None:
                write_json_cache(self.cache_path, result.model_dump(mode="json"))
            return result.model_copy(deep=True)

    def health_metadata(self) -> dict[str, Any]:
        result = self._memory or self._read_cache()
        if result is None:
            return {
                "provider": "Frankfurter", "enabled": True, "auth_configured": True,
                "status": "not_run", "source": FRANKFURTER_SOURCE,
                "freshness": "尚未查詢", "reason": "尚未建立每日 FX context 快取",
            }
        return {
            "provider": "Frankfurter", "enabled": True, "auth_configured": True,
            "status": "current" if result.status == "available" else result.status,
            "source": result.source, "data_date": result.as_of, "as_of": result.as_of,
            "freshness": result.freshness,
            "reason": result.error_reason or "CBC 每日匯率 context 可用",
            "last_attempt": result.retrieved_at,
            "last_success": result.retrieved_at if result.data is not None else None,
            "error": result.error_reason,
        }

    def _read_cache(self) -> ExternalProviderResult | None:
        payload = read_json_cache(self.cache_path)
        try:
            return ExternalProviderResult.model_validate(payload) if payload else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cache_is_fresh(result: ExternalProviderResult, now: datetime) -> bool:
        try:
            stamp = datetime.fromisoformat(result.retrieved_at or "")
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        except ValueError:
            return False
        return now.astimezone(UTC) - stamp.astimezone(UTC) < FX_CACHE_TTL


_PROVIDER: FrankfurterFxProvider | None = None
_PROVIDER_LOCK = threading.Lock()


def get_frankfurter_fx_provider() -> FrankfurterFxProvider:
    global _PROVIDER
    with _PROVIDER_LOCK:
        if _PROVIDER is None:
            _PROVIDER = FrankfurterFxProvider()
        return _PROVIDER

"""Small, cached FRED/ALFRED macro context adapter."""
# ruff: noqa: RUF001 -- user-facing Traditional Chinese text is intentional.
from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.taiwan.providers.external_models import (
    ExternalProviderResult,
    ExternalStatus,
    read_json_cache,
    write_json_cache,
)

FRED_SOURCE = "fred:series_observations"
FRED_TERMS_URL = "https://fred.stlouisfed.org/docs/api/terms_of_use.html"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_CACHE_TTL = timedelta(hours=24)
FRED_RETRY_TTL = timedelta(minutes=5)
FRED_SERIES: dict[str, str] = {
    "FEDFUNDS": "Federal Funds Rate",
    "DGS2": "US 2Y Treasury",
    "DGS10": "US 10Y Treasury",
    "T10Y2Y": "US 10Y minus 2Y spread",
    "CPIAUCSL": "US CPI",
    "PCEPILFE": "US Core PCE",
    "UNRATE": "US Unemployment Rate",
    "INDPRO": "US Industrial Production",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if value != "." else None


def normalize_fred_series(
    series_id: str,
    payload: Any,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("observations"), list):
        return None
    observations: list[dict[str, Any]] = []
    for row in payload["observations"]:
        if not isinstance(row, dict):
            continue
        value = _number(row.get("value"))
        observed_date = str(row.get("date") or "")
        if value is None or not observed_date:
            continue
        observations.append({
            "observed_date": observed_date,
            "value": value,
            "realtime_start": str(row.get("realtime_start") or payload.get("realtime_start") or "") or None,
            "realtime_end": str(row.get("realtime_end") or payload.get("realtime_end") or "") or None,
        })
    if not observations:
        return None
    observations.sort(key=lambda row: str(row["observed_date"]))
    return {
        "series_id": series_id,
        "name": FRED_SERIES[series_id],
        "latest": observations[-1],
        "observations": observations,
    }


def _macro_summary(series: list[dict[str, Any]]) -> str:
    latest = {item["series_id"]: item["latest"]["value"] for item in series}
    long_rate = latest.get("DGS10")
    spread = latest.get("T10Y2Y")
    if long_rate is None and spread is None:
        return "全球環境：關鍵利率資料不足，暫無法判斷利率與景氣壓力。"
    if long_rate is not None and long_rate >= 4.0:
        return "全球環境：利率壓力偏高。美國長期利率仍在高檔，對高估值成長股相對不利。"
    if spread is not None and spread < 0:
        return "全球環境：殖利率曲線仍呈倒掛，景氣前景的不確定性偏高。"
    return "全球環境：利率與景氣壓力目前大致中性，仍需配合台股官方證據判斷。"


class FredMacroProvider:
    def __init__(self, api_key: str | None = None, *, cache_dir: Path | None = None, requester=None) -> None:
        self.api_key = (settings.fred_api_key if api_key is None else api_key).strip()
        self.cache_dir = cache_dir or Path(settings.data_dir) / "external_context"
        self.requester = requester or self._request_series
        self._lock = threading.Lock()
        self._memory: dict[str, ExternalProviderResult] = {}
        self._last_attempt: dict[str, datetime] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def get_context(
        self, *, vintage_date: date | None = None, now: datetime | None = None,
    ) -> ExternalProviderResult:
        if not self.enabled:
            return ExternalProviderResult.unavailable(FRED_SOURCE, "config_missing")
        current = now or datetime.now(UTC)
        cache_key = vintage_date.isoformat() if vintage_date else "current"
        cache_path = self.cache_dir / f"fred_{cache_key}.json"
        with self._lock:
            cached = self._memory.get(cache_key) or self._read_cache(cache_path)
            if cached is not None and self._cache_is_fresh(cached, current):
                self._memory[cache_key] = cached
                return cached.model_copy(deep=True)
            previous_attempt = self._last_attempt.get(cache_key)
            if previous_attempt is not None and current - previous_attempt < FRED_RETRY_TTL:
                return (cached or ExternalProviderResult.unavailable(
                    FRED_SOURCE, "provider_unavailable",
                )).model_copy(deep=True)
            self._last_attempt[cache_key] = current
        series: list[dict[str, Any]] = []
        missing: list[str] = []
        for series_id in FRED_SERIES:
            try:
                normalized = normalize_fred_series(
                    series_id, self.requester(series_id, vintage_date),
                )
            except Exception:
                normalized = None
            if normalized is None:
                missing.append(series_id)
            else:
                series.append(normalized)
        with self._lock:
            if not series:
                if cached is not None:
                    result = cached.model_copy(update={
                        "status": "stale", "freshness": "stale",
                        "error_reason": "provider_unavailable",
                    })
                else:
                    result = ExternalProviderResult.unavailable(FRED_SOURCE, "provider_unavailable")
                self._memory[cache_key] = result
                if result.data is not None:
                    write_json_cache(cache_path, result.model_dump(mode="json"))
                return result.model_copy(deep=True)
            as_of = max(item["latest"]["observed_date"] for item in series)
            status: ExternalStatus = "partial" if missing else "available"
            result = ExternalProviderResult(
                source=FRED_SOURCE,
                status=status,
                as_of=as_of,
                retrieved_at=current.isoformat(),
                freshness="daily_cache",
                data={
                    "summary": _macro_summary(series),
                    "series": series,
                    "missing_series": missing,
                    "vintage_date": vintage_date.isoformat() if vintage_date else None,
                    "ui_context_only": True,
                    "terms_url": FRED_TERMS_URL,
                },
                error_reason="missing_series" if missing else None,
            )
            self._memory[cache_key] = result
            write_json_cache(cache_path, result.model_dump(mode="json"))
            return result.model_copy(deep=True)

    def cached_context(
        self, *, vintage_date: date | None = None, now: datetime | None = None,
    ) -> ExternalProviderResult:
        """Return immediately for core request paths; never perform network I/O."""
        if not self.enabled:
            return ExternalProviderResult.unavailable(FRED_SOURCE, "config_missing")
        current = now or datetime.now(UTC)
        cache_key = vintage_date.isoformat() if vintage_date else "current"
        cache_path = self.cache_dir / f"fred_{cache_key}.json"
        with self._lock:
            cached = self._memory.get(cache_key) or self._read_cache(cache_path)
            if cached is None:
                return ExternalProviderResult.unavailable(FRED_SOURCE, "not_queried")
            self._memory[cache_key] = cached
            if cached.data is None:
                return cached.model_copy(deep=True)
            if self._cache_is_fresh(cached, current):
                return cached.model_copy(deep=True)
            return cached.model_copy(deep=True, update={
                "status": "stale", "freshness": "stale", "error_reason": "cache_expired",
            })

    def _request_series(self, series_id: str, vintage_date: date | None) -> Any:
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 24,
        }
        if vintage_date is not None:
            params["vintage_dates"] = vintage_date.isoformat()
        with httpx.Client(timeout=15.0) as client:
            response = client.get(FRED_API_URL, params=params)
            response.raise_for_status()
            return response.json()

    def health_metadata(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "provider": "FRED", "enabled": False, "auth_configured": False,
                "status": "config_missing", "source": FRED_SOURCE,
                "freshness": "unavailable", "reason": "未設定 FRED_API_KEY",
                "error": "config_missing",
            }
        result = self._memory.get("current") or self._read_cache(self.cache_dir / "fred_current.json")
        if result is None:
            return {
                "provider": "FRED", "enabled": True, "auth_configured": True,
                "status": "not_run", "source": FRED_SOURCE,
                "freshness": "尚未查詢", "reason": "尚未建立 Macro Context 快取",
            }
        return {
            "provider": "FRED", "enabled": True, "auth_configured": True,
            "status": "current" if result.status == "available" else result.status,
            "source": result.source, "data_date": result.as_of, "as_of": result.as_of,
            "freshness": result.freshness, "reason": result.error_reason or "Macro Context 可用",
            "last_attempt": (
                self._last_attempt["current"].isoformat()
                if "current" in self._last_attempt else result.retrieved_at
            ),
            "last_success": result.retrieved_at if result.data is not None else None,
            "error": result.error_reason,
        }

    @staticmethod
    def _cache_is_fresh(result: ExternalProviderResult, now: datetime) -> bool:
        try:
            stamp = datetime.fromisoformat(result.retrieved_at or "")
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        except ValueError:
            return False
        return now.astimezone(UTC) - stamp.astimezone(UTC) < FRED_CACHE_TTL

    @staticmethod
    def _read_cache(path: Path) -> ExternalProviderResult | None:
        payload = read_json_cache(path)
        try:
            return ExternalProviderResult.model_validate(payload) if payload else None
        except (TypeError, ValueError):
            return None


_PROVIDER: FredMacroProvider | None = None
_LOCK = threading.Lock()


def get_fred_macro_provider() -> FredMacroProvider:
    global _PROVIDER
    with _LOCK:
        if _PROVIDER is None:
            _PROVIDER = FredMacroProvider()
        return _PROVIDER

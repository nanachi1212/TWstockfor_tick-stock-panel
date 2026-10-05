"""FinBridge secondary cross-check adapter for one user-requested Taiwan stock."""
from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.taiwan.providers.external_models import (
    ExternalProviderResult,
    read_json_cache,
    write_json_cache,
)

FINBRIDGE_SOURCE = "finbridge:rest:tw"
FINBRIDGE_BASE_URL = "https://mcp.gronox.kr/api/v1"
FINBRIDGE_CACHE_TTL = timedelta(hours=24)
FINBRIDGE_RETRY_TTL = timedelta(minutes=5)


def _find_number(value: Any, names: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in names and isinstance(item, (int, float)) and not isinstance(item, bool):
                return float(item)
        for item in value.values():
            found = _find_number(item, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_number(item, names)
            if found is not None:
                return found
    return None


def build_cross_check(
    official: dict[str, float | None], valuation: Any,
) -> dict[str, Any]:
    secondary = {
        "pe": _find_number(valuation, {"per", "pe", "peratio", "priceearningsratio"}),
        # FinBridge EPS lacks enough period and basis metadata for a like-for-like comparison.
        "eps": None,
    }
    checks: dict[str, dict[str, Any]] = {}
    for metric in ("pe", "eps"):
        primary_value = official.get(metric)
        secondary_value = secondary.get(metric)
        if primary_value is None or secondary_value is None:
            status = "unavailable"
        else:
            denominator = max(abs(primary_value), abs(secondary_value), 1e-9)
            status = "matched" if abs(primary_value - secondary_value) / denominator <= 0.05 else "mismatch"
        checks[metric] = {
            "status": status,
            "official": primary_value,
            "secondary": secondary_value,
        }
    statuses = [row["status"] for row in checks.values()]
    overall = (
        "mismatch" if "mismatch" in statuses else
        "matched" if all(status == "matched" for status in statuses) else
        "partial" if "matched" in statuses else "unavailable"
    )
    return {
        "official": dict(official),
        "secondary": {"provider": "finbridge", **secondary},
        "cross_check": {"status": overall, "metrics": checks},
        "authority": "official_values_are_not_overwritten",
    }


class FinBridgeProvider:
    def __init__(self, api_key: str | None = None, *, cache_dir: Path | None = None, requester=None) -> None:
        self.api_key = (settings.finbridge_api_key if api_key is None else api_key).strip()
        self.cache_dir = cache_dir or Path(settings.data_dir) / "external_context" / "finbridge"
        self.requester = requester or self._request
        self._lock = threading.Lock()
        self._memory: dict[str, ExternalProviderResult] = {}
        self._last_attempt: dict[str, datetime] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def cross_check(
        self,
        symbol: str,
        *,
        official: dict[str, float | None],
        now: datetime | None = None,
    ) -> ExternalProviderResult:
        if not self.enabled:
            return ExternalProviderResult.unavailable(FINBRIDGE_SOURCE, "config_missing")
        current = now or datetime.now(UTC)
        code = symbol.split(".", 1)[0]
        cache_path = self.cache_dir / f"{code}.json"
        with self._lock:
            cached = self._memory.get(code) or self._read_cache(cache_path)
            if cached is not None and self._cache_is_fresh(cached, current):
                return self._with_official(cached, official)
            previous_attempt = self._last_attempt.get(code)
            if previous_attempt is not None and current - previous_attempt < FINBRIDGE_RETRY_TTL:
                return self._with_official(
                    cached or ExternalProviderResult.unavailable(
                        FINBRIDGE_SOURCE, "provider_unavailable",
                    ),
                    official,
                )
            self._last_attempt[code] = current
        try:
            responses = {"valuation": self.requester(code, "valuation")}
            missing: list[str] = []
        except Exception:
            responses = {}
            missing = ["valuation"]
        with self._lock:
            if not responses:
                if cached is not None:
                    result = cached.model_copy(update={
                        "status": "stale", "freshness": "stale",
                        "error_reason": "provider_unavailable",
                    })
                    write_json_cache(cache_path, result.model_dump(mode="json"))
                else:
                    result = ExternalProviderResult.unavailable(FINBRIDGE_SOURCE, "provider_unavailable")
                self._memory[code] = result
                return self._with_official(result, official)
            raw = ExternalProviderResult(
                source=FINBRIDGE_SOURCE,
                status="partial" if missing else "available",
                as_of=self._find_as_of(responses),
                retrieved_at=current.isoformat(),
                freshness="nightly_cache",
                data={"responses": responses, "missing_endpoints": missing},
                error_reason="missing_endpoints" if missing else None,
            )
            self._memory[code] = raw
            write_json_cache(cache_path, raw.model_dump(mode="json"))
            return self._apply_official(raw, official)

    def cached_cross_check(
        self,
        symbol: str,
        *,
        official: dict[str, float | None],
        now: datetime | None = None,
    ) -> ExternalProviderResult:
        """Return immediately for core request paths; never perform network I/O."""
        if not self.enabled:
            return ExternalProviderResult.unavailable(FINBRIDGE_SOURCE, "config_missing")
        current = now or datetime.now(UTC)
        code = symbol.split(".", 1)[0]
        cache_path = self.cache_dir / f"{code}.json"
        with self._lock:
            cached = self._memory.get(code) or self._read_cache(cache_path)
            if cached is None:
                return ExternalProviderResult.unavailable(FINBRIDGE_SOURCE, "not_queried")
            self._memory[code] = cached
            if cached.data is None:
                return cached.model_copy(deep=True)
            if not self._cache_is_fresh(cached, current):
                cached = cached.model_copy(update={
                    "status": "stale", "freshness": "stale", "error_reason": "cache_expired",
                })
            return self._with_official(cached, official)

    def _request(self, code: str, endpoint: str) -> Any:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with httpx.Client(timeout=15.0, headers=headers) as client:
            response = client.get(f"{FINBRIDGE_BASE_URL}/companies/tw/{code}/{endpoint}")
            response.raise_for_status()
            return response.json()

    def health_metadata(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "provider": "FinBridge", "enabled": False, "auth_configured": False,
                "status": "config_missing", "source": FINBRIDGE_SOURCE,
                "freshness": "unavailable", "reason": "未設定 FINBRIDGE_API_KEY",
                "error": "config_missing",
            }
        latest = max(self._memory.values(), key=lambda row: row.retrieved_at or "", default=None)
        if latest is None:
            return {
                "provider": "FinBridge", "enabled": True, "auth_configured": True,
                "status": "not_run", "source": FINBRIDGE_SOURCE,
                "freshness": "尚未查詢", "reason": "尚未對目前個股執行 secondary cross-check",
            }
        return {
            "provider": "FinBridge", "enabled": True, "auth_configured": True,
            "status": "current" if latest.status == "available" else latest.status,
            "source": latest.source, "data_date": latest.as_of, "as_of": latest.as_of,
            "freshness": latest.freshness, "reason": latest.error_reason or "Secondary cross-check 可用",
            "last_attempt": (
                max(self._last_attempt.values()).isoformat()
                if self._last_attempt else latest.retrieved_at
            ),
            "last_success": latest.retrieved_at if latest.data is not None else None,
            "error": latest.error_reason,
        }

    @staticmethod
    def _with_official(
        raw: ExternalProviderResult, official: dict[str, float | None],
    ) -> ExternalProviderResult:
        if raw.data is None:
            return raw.model_copy(deep=True)
        return FinBridgeProvider._apply_official(raw, official)

    @staticmethod
    def _apply_official(
        raw: ExternalProviderResult, official: dict[str, float | None],
    ) -> ExternalProviderResult:
        responses = raw.data.get("responses", {}) if isinstance(raw.data, dict) else {}
        data = build_cross_check(
            official,
            responses.get("valuation"),
        )
        return raw.model_copy(deep=True, update={"data": data})

    @staticmethod
    def _find_as_of(responses: dict[str, Any]) -> str | None:
        dates: list[str] = []
        stack: list[Any] = [responses]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if str(key).lower() in {"data_as_of", "as_of", "date", "filing_date"} and isinstance(item, str):
                        dates.append(item[:10])
                    elif isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(value, list):
                stack.extend(value)
        return max(dates, default=None)

    @staticmethod
    def _cache_is_fresh(result: ExternalProviderResult, now: datetime) -> bool:
        try:
            stamp = datetime.fromisoformat(result.retrieved_at or "")
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
        except ValueError:
            return False
        return now.astimezone(UTC) - stamp.astimezone(UTC) < FINBRIDGE_CACHE_TTL

    @staticmethod
    def _read_cache(path: Path) -> ExternalProviderResult | None:
        payload = read_json_cache(path)
        try:
            return ExternalProviderResult.model_validate(payload) if payload else None
        except (TypeError, ValueError):
            return None


_PROVIDER: FinBridgeProvider | None = None
_LOCK = threading.Lock()


def get_finbridge_provider() -> FinBridgeProvider:
    global _PROVIDER
    with _LOCK:
        if _PROVIDER is None:
            _PROVIDER = FinBridgeProvider()
        return _PROVIDER

"""Throttled HTTP access shared by every Taiwan market-data source.

Every Taiwan request goes through the process-shared slot table in
``app.rate_limits`` before it leaves the machine.  Buckets are namespaced
(``taiwan:twse`` ...) so they never share accounting with the legacy TickFlow /
A-share buckets, which stay keyed by rpm value.

Requests whose host is not in ``_HOST_KEYS`` return ``None`` from
``rate_limit_key`` and are sent unthrottled, i.e. exactly the previous
behaviour.  That keeps injected test clients and unrelated hosts fast.

rpm resolution
--------------
``SourceMetadata.rate_limit_rpm`` wins when a caller passes it.  Only FinMind
(5 anonymous / 10 with token) and Yahoo (30) actually declare one; ``SourceMetadata`` otherwise carries
its dataclass default of 60, which is not a statement about the source.  The
conservative per-host defaults below are used when no rpm is supplied:

* ``taiwan:twse`` / ``taiwan:tpex`` = 20 rpm (3 s spacing).  Neither exchange
  publishes a quota.  Both the ``rwd``/``www`` endpoints and the openapi hosts
  start returning empty payloads or connection errors under rapid sequential
  polling, and ~3 s is the spacing this repo's existing serial fetch loops
  already produced in practice.  20 is the floor of that observation, not a
  documented allowance.
* ``taiwan:mops`` = 12 rpm (5 s spacing).  MOPS (公開資訊觀測站) is the
  strictest of the official hosts and blocks bursts of detail queries; the
  dividend-lifecycle path issues one detail POST per material-information row,
  so it is paced more slowly than the quote endpoints.
* ``taiwan:tdcc`` = 12 rpm.  集保 distribution data is not fetched yet; it is
  registered up-front at the MOPS pace so a future adapter cannot forget to.
* ``taiwan:finmind`` = 5 rpm by default. Declared: 300 req/hour anonymous (5/min),
  600 req/hour with a verified token (10/min).
* ``taiwan:yahoo`` = 30 rpm.  Declared in ``YAHOO_METADATA``; Yahoo publishes
  no quota for the chart API, 30 is the adapter's long-standing declaration.

All of the above are further scaled by ``apply_safety_rpm`` (80%), the same
safety factor the legacy path uses, so e.g. TWSE effectively paces at 16 rpm.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import httpx

from app.rate_limits import acquire_slot, apply_safety_rpm

logger = logging.getLogger(__name__)

TWSE = "taiwan:twse"
TPEX = "taiwan:tpex"
MOPS = "taiwan:mops"
FINMIND = "taiwan:finmind"
YAHOO = "taiwan:yahoo"
TDCC = "taiwan:tdcc"

# Most specific host suffix first: mops.twse.com.tw must not fall into TWSE.
_HOST_KEYS: tuple[tuple[str, str], ...] = (
    ("mops.twse.com.tw", MOPS),
    ("mopsov.twse.com.tw", MOPS),
    ("twse.com.tw", TWSE),
    ("tpex.org.tw", TPEX),
    ("finmindtrade.com", FINMIND),
    ("finance.yahoo.com", YAHOO),
    ("tdcc.com.tw", TDCC),
)

# Conservative defaults; see module docstring for the basis of each number.
SOURCE_RPM: dict[str, int] = {
    TWSE: 20,
    TPEX: 20,
    MOPS: 12,
    TDCC: 12,
    FINMIND: 5,
    YAHOO: 30,
}

DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def rate_limit_key(url: str) -> str | None:
    """Return the namespaced bucket for *url*, or None when the host is unmanaged."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for suffix, key in _HOST_KEYS:
        if host == suffix or host.endswith("." + suffix):
            return key
    return None


def throttle(url: str, *, rpm: int | None = None) -> float:
    """Block until *url*'s bucket releases a slot; return the seconds slept."""
    key = rate_limit_key(url)
    if key is None:
        return 0.0
    effective = apply_safety_rpm(rpm if rpm is not None else SOURCE_RPM[key])
    return acquire_slot(effective, key=key)


def taiwan_client(
    *,
    timeout: float = 15.0,
    headers: dict[str, str] | None = None,
    rpm: int | None = None,
    **kwargs: Any,
) -> httpx.Client:
    """An ``httpx.Client`` that takes a slot before every request it sends."""
    return httpx.Client(
        timeout=timeout,
        headers=headers,
        event_hooks={"request": [lambda request: throttle(str(request.url), rpm=rpm)]},
        **kwargs,
    )


def fetch_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    rpm: int | None = None,
    max_attempts: int = 1,
    initial_backoff: float = 1.0,
) -> Any:
    """Throttled urllib JSON GET; a slot is consumed by every attempt.

    Retry policy is the one the institutional/margin refresh already used:

    Retryable:
      - Timeouts (TimeoutError, urllib.error.URLError wrapping timeout)
      - Connection reset / network dropped
      - HTTP 429 (Too Many Requests)
      - HTTP 5xx (500, 502, 503, 504)

    Non-retryable:
      - HTTP 4xx (400, 401, 403, 404, etc. except 429)
      - JSON decode / malformed data errors

    ``max_attempts=1`` (the default) is a plain single-shot fetch, matching the
    FinMind and Yahoo adapters, which never retried.
    """
    attempt = 0
    backoff = initial_backoff

    while attempt < max_attempts:
        attempt += 1
        throttle(url, rpm=rpm)
        try:
            req = urllib.request.Request(
                url, headers=headers or {"User-Agent": DEFAULT_USER_AGENT}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 429 or 5xx are transient; other 4xx are permanent
            is_retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not is_retryable or attempt >= max_attempts:
                raise
            logger.warning(
                "HTTP %d on %s (attempt %d/%d), retrying in %.1fs...",
                exc.code, url, attempt, max_attempts, backoff,
            )
            time.sleep(backoff)
            backoff *= 2.0
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "Network/timeout error on %s: %s (attempt %d/%d), retrying in %.1fs...",
                url, exc, attempt, max_attempts, backoff,
            )
            time.sleep(backoff)
            backoff *= 2.0
        except json.JSONDecodeError:
            # Data parsing error is permanent; do not retry
            raise

    raise RuntimeError(f"Failed to fetch {url} after {max_attempts} attempts")

"""Taiwan Stock News Service (A11).

Fetches, normalizes, deduplicates, and caches recent stock news using FinMind TaiwanStockNews.
Strict Constraints:
- No web scraping or news crawlers.
- No synthetic content or AI-generated news articles; preserve original description if present, else None.
- Deterministic deduplication via normalized URL and title normalization (no NLP clustering).
- Caching via FinMindCache with 30-minute TTL to respect API quotas.
- Graceful degradation for paid tiers or rate limits (unavailable status, never fake zeroes).
"""
# ruff: noqa: RUF001
from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from app.taiwan.finmind_cache import FinMindCache
from app.taiwan.providers.finmind_provider import (
    FinMindAdapter,
    FinMindAuthError,
    FinMindError,
    FinMindNetworkError,
    FinMindRateLimitError,
)
from app.taiwan.realtime.calendar import taipei_now
from app.taiwan.symbol import parse_symbol

logger = logging.getLogger(__name__)

NEWS_CACHE_TTL_SECONDS = 1800  # 30 minutes


class TaiwanStockNewsItem(BaseModel):
    """Canonical normalized Taiwan stock news item."""

    id: str = Field(..., description="確定性新聞識別碼")
    symbol: str = Field(..., description="標準標的代碼，如 2330.TWSE")
    code: str = Field(..., description="標的代號，如 2330")
    date: str = Field(..., description="發布時間戳記或日期")
    title: str = Field(..., description="新聞標題")
    source: str = Field(..., description="新聞來源媒體")
    url: str = Field(..., description="新聞外部連結")
    description: str | None = Field(None, description="新聞簡短摘要（來源有才提供，不得自己補）")


class TaiwanStockNewsResponse(BaseModel):
    """Stock news query response."""

    symbol: str
    code: str
    items: list[TaiwanStockNewsItem] = Field(default_factory=list)
    status: str = Field("available", description="available, unavailable, rate_limited, auth_required, error")
    status_message: str | None = None
    fetched_at: str | None = None


def normalize_news_url(raw_url: str) -> str:
    """Normalize URL by stripping tracking and affiliate query parameters."""
    if not raw_url:
        return ""
    try:
        parts = urlsplit(raw_url.strip())
        # Drop tracking query params: utm_*, oc, fbclid, gclid, ref, etc.
        filtered_queries = [
            (k, v)
            for k, v in parse_qsl(parts.query)
            if not (
                k.lower().startswith("utm_")
                or k.lower() in ("oc", "fbclid", "gclid", "ref", "spm", "_hsenc")
            )
        ]
        clean_query = urlencode(filtered_queries)
        clean_url = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), clean_query, ""))
        return clean_url
    except Exception:
        return raw_url.strip()


def normalize_news_title(raw_title: str) -> str:
    """Normalize title for deterministic duplicate detection."""
    title = str(raw_title or "").strip()
    # Strip common media suffixes like "- ETtoday新聞雲", "| 鉅亨網", "- 經濟日報", "- 自由財經"
    title = re.sub(
        r"[\s\-|—－~～]+(ETtoday[^\s]*|鉅亨網|經濟日報|工商時報|自由財經|Yahoo奇摩股市|中央社|非凡商業|時報資訊|鏡週刊|數位時代|MoneyDJ[^\s]*).*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    # Remove repetitive punctuation and whitespace
    title = re.sub(r"[\s　]+", " ", title)
    return title


def deduplicate_news(raw_items: list[dict[str, Any]], symbol: str, code: str) -> list[TaiwanStockNewsItem]:
    """Perform deterministic deduplication across URLs and titles."""
    seen_urls: set[str] = set()
    seen_titles: dict[str, str] = {}  # norm_title -> item_id
    items_by_id: dict[str, TaiwanStockNewsItem] = {}

    # Sort raw items descending by date (newest first)
    sorted_rows = sorted(
        raw_items,
        key=lambda r: str(r.get("date") or ""),
        reverse=True,
    )

    for r in sorted_rows:
        raw_title = str(r.get("title") or "").strip()
        raw_url = str(r.get("link") or r.get("url") or "").strip()
        raw_date = str(r.get("date") or "").strip()
        raw_source = str(r.get("source") or "新聞媒體").strip()
        raw_desc = r.get("description")
        description = str(raw_desc).strip() if raw_desc and str(raw_desc).strip() else None

        if not raw_title or not raw_url:
            continue

        norm_url = normalize_news_url(raw_url)
        norm_title = normalize_news_title(raw_title)

        if norm_url in seen_urls:
            continue
        if norm_title in seen_titles:
            continue

        item_id = hashlib.sha256(f"{code}_{norm_title}_{norm_url}".encode()).hexdigest()[:16]
        item = TaiwanStockNewsItem(
            id=f"news_{item_id}",
            symbol=symbol,
            code=code,
            date=raw_date,
            title=raw_title,
            source=raw_source,
            url=raw_url,
            description=description,
        )

        seen_urls.add(norm_url)
        seen_titles[norm_title] = item_id
        items_by_id[item_id] = item

    return list(items_by_id.values())


class TaiwanNewsService:
    """Service providing cached, deduplicated stock news."""

    def __init__(
        self,
        finmind_adapter: FinMindAdapter | None = None,
        finmind_cache: FinMindCache | None = None,
    ) -> None:
        self.finmind = finmind_adapter or FinMindAdapter()
        self.cache = finmind_cache or FinMindCache()

    def get_recent_news(
        self,
        symbol_or_code: str,
        limit: int = 15,
        force_refresh: bool = False,
    ) -> TaiwanStockNewsResponse:
        """Fetch and return recent news for a single stock."""
        try:
            canonical = parse_symbol(symbol_or_code)
            symbol = canonical.canonical
            code = canonical.code
        except Exception:
            clean = str(symbol_or_code).strip().upper()
            code = clean.split(".")[0]
            symbol = f"{code}.TWSE"

        now_iso = taipei_now().isoformat()
        dataset_name = "TaiwanStockNews"

        # Check local cache first
        if not force_refresh:
            cached = self.cache.get(dataset_name, code)
            if cached and isinstance(cached.get("data"), list):
                status = str(cached.get("status", "available"))
                error_msg = cached.get("error_msg")
                if status == "available":
                    raw_rows = cached.get("data", [])
                    items = deduplicate_news(raw_rows, symbol, code)[:limit]
                    return TaiwanStockNewsResponse(
                        symbol=symbol,
                        code=code,
                        items=items,
                        status="available",
                        fetched_at=cached.get("fetched_at"),
                    )
                elif status in ("unavailable", "rate_limited", "auth_required"):
                    return TaiwanStockNewsResponse(
                        symbol=symbol,
                        code=code,
                        items=[],
                        status=status,
                        status_message=error_msg or "目前資料來源方案不提供",
                        fetched_at=cached.get("fetched_at"),
                    )

        # FinMind TaiwanStockNews requires start_date and end_date=None
        # Query recent days
        today = taipei_now().date()
        # Query yesterday or today
        start_date = (today - timedelta(days=2)).isoformat()

        try:
            raw_rows = self.finmind.fetch_dataset(
                dataset=dataset_name,
                symbol_or_code=code,
                start_date=start_date,
                end_date=None,
                raise_for_status=True,
            )

            # Write to cache
            self.cache.set(
                dataset=dataset_name,
                symbol=code,
                data=raw_rows,
                data_date=start_date,
                status="available",
            )

            items = deduplicate_news(raw_rows, symbol, code)[:limit]
            return TaiwanStockNewsResponse(
                symbol=symbol,
                code=code,
                items=items,
                status="available",
                fetched_at=now_iso,
            )
        except FinMindAuthError as e:
            logger.warning("FinMind news auth error for %s: %s", code, e)
            self.cache.set(
                dataset=dataset_name,
                symbol=code,
                data=[],
                status="auth_required",
                error_msg="目前資料來源方案不提供或需要有效金鑰",
            )
            return TaiwanStockNewsResponse(
                symbol=symbol,
                code=code,
                items=[],
                status="auth_required",
                status_message="目前資料來源方案不提供或需要有效金鑰",
                fetched_at=now_iso,
            )
        except FinMindRateLimitError as e:
            logger.warning("FinMind news rate limit error for %s: %s", code, e)
            self.cache.set(
                dataset=dataset_name,
                symbol=code,
                data=[],
                status="rate_limited",
                error_msg="FinMind 請求頻率達上限，請稍候重試",
            )
            return TaiwanStockNewsResponse(
                symbol=symbol,
                code=code,
                items=[],
                status="rate_limited",
                status_message="FinMind 請求頻率達上限，請稍候重試",
                fetched_at=now_iso,
            )
        except (FinMindNetworkError, FinMindError, Exception) as e:
            logger.warning("FinMind news fetch failed for %s: %s", code, e)
            self.cache.set(
                dataset=dataset_name,
                symbol=code,
                data=[],
                status="unavailable",
                error_msg=f"新聞資料暫時無法取得: {e}",
            )
            return TaiwanStockNewsResponse(
                symbol=symbol,
                code=code,
                items=[],
                status="unavailable",
                status_message="新聞資料暫時無法取得，目前資料來源方案不提供或連線中斷",
                fetched_at=now_iso,
            )


_news_service_instance: TaiwanNewsService | None = None


def get_news_service() -> TaiwanNewsService:
    global _news_service_instance
    if _news_service_instance is None:
        _news_service_instance = TaiwanNewsService()
    return _news_service_instance

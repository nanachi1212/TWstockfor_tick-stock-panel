"""PTT Stock 與 Dcard 台股社群聲量、情緒 MVP。

這個模組刻意保持獨立：collector 只負責來源讀取，股票辨識只使用既有
TaiwanSecurityMaster，AI 與檔案輸出則由 service 編排。排程快照只保存聚合
結果；手動快照另外保存有上限的文章節錄與代表留言，供單次執行頁查閱。
"""
from __future__ import annotations

import asyncio
import csv
import html
import json
import logging
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import settings
from app.taiwan.providers.taiwan_values import TAIPEI

logger = logging.getLogger(__name__)

_NON_RETRYABLE_AI_STATUS = {401, 402, 403, 404}

_PTT_INDEX = "https://www.ptt.cc/bbs/Stock/index.html"
_PTT_BOARD = "https://www.ptt.cc"
_DCARD_FORUMS = ("stock", "investment")
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
}
_CODE_RE = re.compile(r"(?<![\dA-Za-z])([0-9]{4,6}[A-Za-z]?)(?![\dA-Za-z])")
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_VALID_TRIGGERS = {"pre_open", "after_close", "manual", "missed_schedule"}


class SocialSentimentAlreadyRunningError(RuntimeError):
    """Raised when another process already owns the Social Sentiment run lock."""


class SocialSentimentRunLock:
    """Small cross-process, non-blocking file lock shared by CLI and API runs."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: Any | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self.acquired:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None


def _clean_html(value: str) -> str:
    value = html.unescape(_TAG_RE.sub(" ", value or ""))
    return _SPACE_RE.sub(" ", value).strip()


def _snapshot_slot(moment: datetime) -> str:
    return "pre_open" if moment.hour < 12 else "after_close"


def _resolve_trigger(trigger: str | None, moment: datetime) -> tuple[str, str]:
    if trigger is None:
        slot = _snapshot_slot(moment)
        return slot, slot
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(f"unsupported trigger: {trigger}")
    if trigger == "manual":
        return "manual", "manual"
    if trigger == "missed_schedule":
        return trigger, _snapshot_slot(moment)
    cutoff = (9, 0) if trigger == "pre_open" else (16, 0)
    actual = "missed_schedule" if (moment.hour, moment.minute) > cutoff else trigger
    return actual, trigger


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TAIPEI)
    except ValueError:
        pass
    for fmt in ("%a %b %d %H:%M:%S %Y", "%m/%d %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=TAIPEI)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class SocialPost:
    source: str
    post_id: str
    url: str
    title: str
    content: str
    published_at: datetime | None = None
    author: str | None = None
    comments: tuple[str, ...] = ()
    engagement: int = 0

    @property
    def text(self) -> str:
        return "\n".join(part for part in (self.title, self.content, *self.comments) if part).strip()


@dataclass
class SourceResult:
    source: str
    posts: list[SocialPost] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages: int = 0
    fetch_succeeded: bool = False

    @property
    def status(self) -> str:
        if self.errors and (self.posts or self.fetch_succeeded):
            return "partial"
        if self.posts or self.fetch_succeeded:
            return "available"
        return "unavailable"

    @property
    def comments_count(self) -> int:
        return sum(len(post.comments) for post in self.posts)


class SocialCollector(Protocol):
    source: str

    def fetch(self, *, since: datetime, max_pages: int) -> SourceResult:
        ...


class PTTStockCollector:
    source = "ptt"

    def __init__(self, *, timeout: float = 20.0, request_delay: float = 0.2) -> None:
        self.timeout = timeout
        self.request_delay = request_delay

    def fetch(self, *, since: datetime, max_pages: int = 3) -> SourceResult:
        result = SourceResult(self.source)
        headers = dict(_DEFAULT_HEADERS)
        try:
            with httpx.Client(
                headers=headers,
                cookies={"over18": "1"},
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                index_url = _PTT_INDEX
                for _ in range(max(1, max_pages)):
                    response = client.get(index_url)
                    response.raise_for_status()
                    result.fetch_succeeded = True
                    entries = parse_ptt_index(response.text)
                    result.pages += 1
                    if not entries:
                        break
                    for entry in entries:
                        published = entry.get("published_at")
                        try:
                            page = client.get(entry["url"])
                            page.raise_for_status()
                            post = parse_ptt_post(
                                page.text,
                                source=self.source,
                                post_id=entry["post_id"],
                                url=entry["url"],
                                fallback_title=entry["title"],
                                fallback_published=published,
                            )
                            if post.published_at is None or post.published_at < since:
                                continue
                            result.posts.append(post)
                        except Exception as exc:  # noqa: BLE001 - isolate one PTT post
                            result.errors.append(f"post {entry['post_id']}: {type(exc).__name__}")
                    previous = entry.get("previous_url")
                    if not previous:
                        break
                    index_url = previous
                    if self.request_delay:
                        import time

                        time.sleep(self.request_delay)
        except Exception as exc:  # noqa: BLE001 - source isolation is intentional
            result.errors.append(f"index: {type(exc).__name__}: {exc}")
        logger.info("PTT collector: %d posts, %d comments, status=%s", len(result.posts), result.comments_count, result.status)
        return result


class DcardCollector:
    source = "dcard"

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        request_delay: float = 0.2,
        comment_posts_limit: int = 12,
    ) -> None:
        self.timeout = timeout
        self.request_delay = request_delay
        self.comment_posts_limit = comment_posts_limit

    def fetch(self, *, since: datetime, max_pages: int = 2) -> SourceResult:
        result = SourceResult(self.source)
        try:
            with httpx.Client(
                headers={**_DEFAULT_HEADERS, "Accept": "application/json"},
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                for forum in _DCARD_FORUMS:
                    try:
                        response = client.get(
                            f"https://www.dcard.tw/service/api/v2/forums/{forum}/posts",
                            params={"limit": min(100, max_pages * 30)},
                        )
                        response.raise_for_status()
                        posts = response.json()
                        if not isinstance(posts, list):
                            raise ValueError("Dcard posts response is not a list")
                        result.fetch_succeeded = True
                        result.pages += 1
                        parsed = parse_dcard_posts(posts, forum=forum, since=since)
                        for post in parsed:
                            comments = self._fetch_comments(client, post.post_id, result) if len(result.posts) < self.comment_posts_limit else []
                            result.posts.append(
                                SocialPost(
                                    source=post.source,
                                    post_id=post.post_id,
                                    url=post.url,
                                    title=post.title,
                                    content=post.content,
                                    published_at=post.published_at,
                                    comments=tuple(comments),
                                    engagement=post.engagement,
                                )
                            )
                            if self.request_delay:
                                import time

                                time.sleep(self.request_delay)
                    except Exception as exc:  # noqa: BLE001 - isolate one forum
                        result.errors.append(f"forum {forum}: {type(exc).__name__}: {exc}")
                        try:
                            fallback = client.get(f"https://www.dcard.tw/f/{forum}")
                            fallback.raise_for_status()
                            fallback_posts = parse_dcard_html(fallback.text, forum=forum, since=since)
                            result.posts.extend(fallback_posts)
                            if fallback_posts:
                                result.fetch_succeeded = True
                                result.pages += 1
                        except Exception as fallback_exc:  # noqa: BLE001 - fallback is optional
                            result.errors.append(f"forum {forum} fallback: {type(fallback_exc).__name__}")
        except Exception as exc:  # noqa: BLE001 - source isolation is intentional
            result.errors.append(f"client: {type(exc).__name__}: {exc}")
        logger.info("Dcard collector: %d posts, %d comments, status=%s", len(result.posts), result.comments_count, result.status)
        return result

    def _fetch_comments(self, client: httpx.Client, post_id: str, result: SourceResult) -> list[str]:
        try:
            response = client.get(
                f"https://www.dcard.tw/service/api/v2/posts/{post_id}/comments",
                params={"limit": 20},
            )
            response.raise_for_status()
            items = response.json()
            if not isinstance(items, list):
                return []
            return [str(item.get("content") or "").strip() for item in items if item.get("content")]
        except Exception as exc:  # noqa: BLE001 - comments are optional
            result.errors.append(f"comments {post_id}: {type(exc).__name__}")
            return []


def parse_ptt_index(markup: str) -> list[dict[str, Any]]:
    """Parse a PTT Stock index page without requiring BeautifulSoup."""
    entries: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<div class="r-ent">(?P<body>.*?)</div>\s*</div>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(markup):
        body = match.group("body")
        link = re.search(r'<div class="title">\s*<a href="([^"]+)">(.*?)</a>', body, re.DOTALL)
        if not link:
            continue
        date_match = re.search(r'<div class="date">\s*([^<]+)', body)
        author_match = re.search(r'<div class="author">\s*([^<]+)', body)
        href = html.unescape(link.group(1))
        entries.append(
            {
                "post_id": href.rsplit("/", 1)[-1].removesuffix(".html"),
                "url": f"{_PTT_BOARD}{href}",
                "title": _clean_html(link.group(2)),
                "published_at": _parse_datetime(date_match.group(1).strip()) if date_match else None,
                "author": author_match.group(1).strip() if author_match else None,
            }
        )
    previous = re.search(
        r'<a\b[^>]*href="([^"]+)"[^>]*>\s*(?:‹\s*)?上頁\s*</a>',
        html.unescape(markup),
        re.IGNORECASE,
    )
    for entry in entries:
        entry["previous_url"] = f"{_PTT_BOARD}{html.unescape(previous.group(1))}" if previous else None
    return entries


def parse_ptt_post(
    markup: str,
    *,
    source: str = "ptt",
    post_id: str = "unknown",
    url: str = "",
    fallback_title: str = "",
    fallback_published: datetime | None = None,
) -> SocialPost:
    def meta(label: str) -> str:
        match = re.search(
            rf'<span class="article-meta-tag">\s*{re.escape(label)}\s*</span>\s*'
            r'<span class="article-meta-value">(.*?)</span>',
            markup,
            re.DOTALL | re.IGNORECASE,
        )
        return _clean_html(match.group(1)) if match else ""

    content_match = re.search(r'<div id="main-content"[^>]*>(.*)', markup, re.DOTALL | re.IGNORECASE)
    body = content_match.group(1) if content_match else markup
    body = re.sub(
        r'<span\b[^>]*class="article-meta-tag"[^>]*>.*?</span>\s*'
        r'<span\b[^>]*class="article-meta-value"[^>]*>.*?</span>',
        " ",
        body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    body = re.split(r'<div class="push"', body, maxsplit=1, flags=re.IGNORECASE)[0]
    comments: list[str] = []
    engagement = 0
    for push in re.finditer(r'<div class="push">(.*?)(?=</div>\s*<div class="push">|</div>\s*</div>)', markup, re.DOTALL | re.IGNORECASE):
        part = push.group(1)
        tag_match = re.search(
            r'<span\b[^>]*class="[^"]*\bpush-tag\b[^"]*"[^>]*>(.*?)</span>',
            part,
            re.DOTALL | re.IGNORECASE,
        )
        text_match = re.search(
            r'<span\b[^>]*class="[^"]*\bpush-content\b[^"]*"[^>]*>(.*?)</span>',
            part,
            re.DOTALL | re.IGNORECASE,
        )
        text = _clean_html(text_match.group(1) if text_match else "").lstrip(":： ")
        if not text:
            continue
        comments.append(text)
        engagement += 1
        if tag_match and _clean_html(tag_match.group(1)).startswith(("推", "噓")):
            engagement += 1
    title = meta("標題") or fallback_title
    published = _parse_datetime(meta("時間")) or fallback_published
    return SocialPost(
        source=source,
        post_id=post_id,
        url=url,
        title=title,
        content=_clean_html(body),
        published_at=published,
        author=meta("作者") or None,
        comments=tuple(comments),
        engagement=engagement,
    )


def parse_dcard_posts(
    items: list[dict[str, Any]], *, forum: str, since: datetime | None = None
) -> list[SocialPost]:
    """Normalize public Dcard forum API rows into the common post model."""
    posts: list[SocialPost] = []
    for item in items:
        published = _parse_datetime(str(item.get("createdAt") or ""))
        if since and published is None:
            continue
        if since and published and published < since:
            continue
        post_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not post_id or not title:
            continue
        posts.append(
            SocialPost(
                source="dcard",
                post_id=post_id,
                url=f"https://www.dcard.tw/f/{forum}/p/{post_id}",
                title=title,
                content=str(item.get("excerpt") or item.get("content") or ""),
                published_at=published,
                engagement=int(item.get("likeCount") or 0) + int(item.get("commentCount") or 0),
            )
        )
    return posts


def parse_dcard_html(
    markup: str, *, forum: str, since: datetime | None = None
) -> list[SocialPost]:
    """Best-effort parser for Dcard's public forum HTML fallback.

    The HTML endpoint is intentionally only a fallback for API blocks. It does
    not depend on browser automation and accepts whatever title text the page
    currently exposes around a public post link.
    """
    # The public HTML fallback exposes no trustworthy publication timestamp.
    # Keep it available for explicit unbounded parsing, but never let it bypass
    # a caller's requested time window.
    if since is not None:
        return []
    posts: list[SocialPost] = []
    pattern = re.compile(
        rf'href="/f/{re.escape(forum)}/p/(?P<id>\d+)"[^>]*>(?P<title>.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    seen: set[str] = set()
    for match in pattern.finditer(markup):
        post_id = match.group("id")
        title = _clean_html(match.group("title"))
        if post_id in seen or not title or len(title) > 300:
            continue
        seen.add(post_id)
        posts.append(
            SocialPost(
                source="dcard",
                post_id=post_id,
                url=f"https://www.dcard.tw/f/{forum}/p/{post_id}",
                title=title,
                content="",
            )
        )
    return posts


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    code: str
    name: str


class StockMentionResolver:
    """Resolve codes/names against the existing Taiwan security master."""

    def __init__(self, security_master: Any) -> None:
        frame = security_master.to_dataframe(supported_only=True)
        rows = frame.to_dicts() if hasattr(frame, "to_dicts") else list(frame)
        self.stocks: dict[str, StockInfo] = {
            str(row["code"]).upper(): StockInfo(
                symbol=str(row["symbol"]), code=str(row["code"]).upper(), name=str(row["name"]),
            )
            for row in rows
            if row.get("instrument_type", "stock") == "stock" and row.get("code") and row.get("name")
        }
        self._names = sorted(self.stocks.values(), key=lambda item: len(item.name), reverse=True)
        self._aliases = {"TSMC": self.stocks.get("2330")}

    def resolve(self, text: str) -> dict[str, int]:
        text = text or ""
        mentions: Counter[str] = Counter()
        for match in _CODE_RE.finditer(text.upper()):
            code = match.group(1)
            if code in self.stocks:
                if self._looks_like_year(text.upper(), match.start(), match.end(), code):
                    continue
                mentions[code] += 1
        normalized = text.upper()
        for alias, info in self._aliases.items():
            if info and re.search(rf"(?<![A-Z]){alias}(?![A-Z])", normalized):
                mentions[info.code] += 1
        for info in self._names:
            name = info.name.upper()
            for match in re.finditer(re.escape(name), normalized):
                if len(name) < 3 and not self._has_stock_context(normalized, match.start(), match.end()):
                    continue
                mentions[info.code] += 1
        return dict(mentions)

    @staticmethod
    def _has_stock_context(text: str, start: int, end: int) -> bool:
        context = text[max(0, start - 12) : min(len(text), end + 12)]
        if re.search(r"股票|股價|代號|代碼|持有|買進|賣出|漲停|跌停|股東|公司|法人", context):
            return True
        before = text[max(0, start - 12) : start]
        after = text[end : min(len(text), end + 12)]
        return bool(re.search(r"\d{4,6}\s*$", before) or re.match(r"^\s*\d{4,6}(?:\s|$)", after))

    def _looks_like_year(self, text: str, start: int, end: int, code: str) -> bool:
        """Avoid treating bare calendar years as stock mentions.

        Valid 19xx/20xx stock codes remain detectable when the company name or
        an explicit stock cue is adjacent; ordinary four-digit years do not.
        """
        if len(code) != 4 or not (1900 <= int(code) <= 2099):
            return False
        context = text[max(0, start - 12) : min(len(text), end + 12)]
        if re.search(r"代號|股票|持有|買進|賣出|看好|看壞|做多|做空|漲停|跌停", context):
            return False
        info = self.stocks.get(code)
        return not info or info.name.upper() not in context


@dataclass
class SocialSentimentService:
    security_master: Any | None = None
    collectors: tuple[SocialCollector, ...] | None = None
    output_dir: Path | None = None

    def _collectors(self) -> tuple[SocialCollector, ...]:
        return self.collectors or (PTTStockCollector(), DcardCollector())

    def _output_root(self) -> Path:
        return Path(self.output_dir or settings.data_dir / "social_sentiment")

    async def run_async(
        self,
        *,
        window_hours: int = 24,
        max_pages: int = 3,
        now: datetime | None = None,
        run_ai: bool = True,
        trigger: str | None = None,
        run_lock: SocialSentimentRunLock | None = None,
    ) -> dict[str, Any]:
        if window_hours <= 0:
            raise ValueError("window_hours must be positive")
        supplied_now = now is not None
        started_at = now or datetime.now(TAIPEI)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=TAIPEI)
        started_at = started_at.astimezone(TAIPEI)
        actual_trigger, snapshot_slot = _resolve_trigger(trigger, started_at)
        lock = run_lock or SocialSentimentRunLock(self._output_root() / "run.lock")
        if not lock.acquired and not lock.acquire():
            raise SocialSentimentAlreadyRunningError("social sentiment job is already running")
        try:
            return await self._run_pipeline(
                window_hours=window_hours,
                max_pages=max_pages,
                started_at=started_at,
                finished_at=started_at if supplied_now else None,
                run_ai=run_ai,
                trigger=actual_trigger,
                snapshot_slot=snapshot_slot,
            )
        finally:
            lock.release()

    async def _run_pipeline(
        self,
        *,
        window_hours: int,
        max_pages: int,
        started_at: datetime,
        finished_at: datetime | None,
        run_ai: bool,
        trigger: str,
        snapshot_slot: str,
    ) -> dict[str, Any]:
        since = started_at - timedelta(hours=window_hours)
        source_results: dict[str, SourceResult] = {}
        for collector in self._collectors():
            try:
                source_results[collector.source] = collector.fetch(since=since, max_pages=max_pages)
            except Exception as exc:  # noqa: BLE001 - source isolation is intentional
                logger.exception("Social source %s failed", collector.source)
                source_results[collector.source] = SourceResult(collector.source, errors=[type(exc).__name__])

        from app.taiwan.universe import get_security_master

        master = self.security_master or get_security_master()
        resolver = StockMentionResolver(master)
        evidence: dict[str, list[tuple[SocialPost, int]]] = defaultdict(list)
        for result in source_results.values():
            for post in result.posts:
                for code, count in resolver.resolve(post.text).items():
                    evidence[code].append((post, count))

        previous_payload = self._load_previous_snapshot(started_at.date(), snapshot_slot)
        previous = self._rows_from_snapshot(previous_payload)
        comparable_source_coverage = self._source_coverage_is_comparable(
            source_results, previous_payload, window_hours=window_hours
        )
        rankings = self._aggregate(
            evidence,
            resolver,
            previous,
            comparable_source_coverage=comparable_source_coverage,
        )
        ai_info = {"status": "not_queried", "batches": 0, "analyzed_symbols": 0, "errors": []}
        if run_ai and rankings:
            ai_info = await self._apply_ai(rankings, evidence)
        discussions = self._build_discussions(source_results, resolver) if trigger == "manual" else []
        payload = self._build_payload(
            started_at,
            finished_at or datetime.now(TAIPEI),
            window_hours,
            source_results,
            rankings,
            ai_info,
            trigger=trigger,
            snapshot_slot=snapshot_slot,
            discussions=discussions,
        )
        self.save(payload)
        logger.info(
            "Social sentiment complete: ptt=%d, dcard=%d, symbols=%d, ai_batches=%d, output=%s",
            len(source_results.get("ptt", SourceResult("ptt")).posts),
            len(source_results.get("dcard", SourceResult("dcard")).posts),
            len(rankings),
            ai_info["batches"],
            self._output_root(),
        )
        return payload

    def run(self, **kwargs: Any) -> dict[str, Any]:
        return asyncio.run(self.run_async(**kwargs))

    def _aggregate(
        self,
        evidence: dict[str, list[tuple[SocialPost, int]]],
        resolver: StockMentionResolver,
        previous: dict[tuple[str, str], dict[str, Any]],
        *,
        comparable_source_coverage: bool,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for code, items in evidence.items():
            info = resolver.stocks[code]
            source_mentions = Counter()
            unique_posts: set[tuple[str, str]] = set()
            engagement = 0
            texts: list[dict[str, str]] = []
            for post, count in items:
                source_mentions[post.source] += count
                unique_posts.add((post.source, post.post_id))
                engagement += post.engagement
                texts.append({"source": post.source, "text": post.text[:600]})
            total = sum(source_mentions.values())
            prior = previous.get(("", info.symbol)) or previous.get(("", code))
            prior_total = int(prior["total_mentions"]) if prior and str(prior.get("total_mentions", "")).isdigit() else None
            volume_change = (
                None
                if not comparable_source_coverage or prior_total in (None, 0)
                else round(total / prior_total - 1.0, 4)
            )
            raw_heat = total + len(unique_posts) * 2 + engagement * 0.2 + max(volume_change or 0.0, 0.0) * 10
            rows.append(
                {
                    "symbol": info.symbol,
                    "code": info.code,
                    "company_name": info.name,
                    "ptt_mentions": source_mentions["ptt"],
                    "dcard_mentions": source_mentions["dcard"],
                    "total_mentions": total,
                    "unique_posts": len(unique_posts),
                    "engagement": engagement,
                    "volume_change_24h": volume_change,
                    "bullish_count": 0,
                    "neutral_count": 0,
                    "bearish_count": 0,
                    "sentiment_score": None,
                    "sentiment_confidence": None,
                    "sentiment": "unavailable",
                    "sentiment_reason": None,
                    "social_heat_score": round(raw_heat, 4),
                    "_texts": texts,
                }
            )
        return sorted(rows, key=lambda row: (-row["social_heat_score"], row["code"]))

    async def _apply_ai(self, rankings: list[dict[str, Any]], evidence: dict[str, list[tuple[SocialPost, int]]]) -> dict[str, Any]:
        from app.services.ai_provider import (
            ai_configured,
            generate_structured_ai_text,
            snapshot_ai_provider_config,
        )

        if not ai_configured():
            return {"status": "unavailable", "batches": 0, "analyzed_symbols": 0, "errors": ["AI provider is not configured"]}
        config_snapshot = snapshot_ai_provider_config()
        batches = 0
        analyzed = 0
        errors: list[str] = []
        # Reasoning-capable gateways can spend most of a small output budget on
        # hidden reasoning before emitting JSON. Keep each request bounded so a
        # valid sentiment result has room to complete on Agnes as well as GLM.
        batch_size = 4
        for offset in range(0, len(rankings), batch_size):
            batch = rankings[offset : offset + batch_size]
            prompt_items = [
                {"symbol": row["code"], "company_name": row["company_name"], "texts": row["_texts"][:4]}
                for row in batch
            ]
            prompt = (
                "你是台灣股市社群文字分析器。只分析提供的文章，不做買賣建議。理解「噴、崩、抄底、出貨、"
                "割韭菜、套牢、畢業、上車、下車、空爆、多蛙、空蛙、嘎空、倒貨、韭菜」等語境。\n"
                "請只回傳 JSON，不要 Markdown，格式為 {\"items\":[{\"symbol\":\"2330\","
                "\"sentiment\":\"bullish|neutral|bearish\",\"score\":0.0,\"confidence\":0.0,"
                "\"bullish_count\":0,\"neutral_count\":0,\"bearish_count\":0,\"reason\":\"簡短理由\"}]}。"
                "count 欄位以提供的不同討論串為單位。\n資料：" + json.dumps(prompt_items, ensure_ascii=False)
            )
            raw = ""
            try:
                batches += 1
                raw = await generate_structured_ai_text(
                    [{"role": "user", "content": prompt}],
                    truncated_retry_message=(
                        "前次 JSON 輸出已超出 token 上限而截斷。請以相同 JSON 結構重新輸出完整結果，"
                        "每個 reason 縮短至不超過 20 字。"
                    ),
                    temperature=0.1,
                    max_tokens=3000,
                    timeout=120,
                    config_snapshot=config_snapshot,
                )
                items = _parse_ai_items(raw)
                by_code = {str(item.get("symbol", "")).upper(): item for item in items}
                for row in batch:
                    item = by_code.get(row["code"].upper())
                    if not item:
                        continue
                    try:
                        _apply_ai_item(row, item)
                    except (TypeError, ValueError, KeyError) as exc:
                        errors.append(f"symbol {row['code']}: {type(exc).__name__}")
                        continue
                    analyzed += 1
            except Exception as exc:  # noqa: BLE001 - one batch must not fail the run
                # Keep only a safe HTTP status; provider text may echo request details.
                http_status = getattr(exc.__cause__, "status_code", None) or getattr(exc, "status_code", None)
                detail = f"HTTP {http_status}" if http_status else type(exc).__name__
                errors.append(f"batch {offset // batch_size + 1}: {detail}")
                logger.warning("Social sentiment AI batch failed: %s", detail)
                if http_status in _NON_RETRYABLE_AI_STATUS:
                    # Auth/billing failures repeat for every batch; stop spending calls.
                    errors.append(f"remaining batches skipped after HTTP {http_status}")
                    break
        return {
            "status": (
                "available" if analyzed == len(rankings) and not errors
                else "degraded" if analyzed
                else "unavailable"
            ),
            "batches": batches,
            "analyzed_symbols": analyzed,
            "errors": errors,
        }

    def _build_discussions(
        self,
        source_results: dict[str, SourceResult],
        resolver: StockMentionResolver,
    ) -> list[dict[str, Any]]:
        discussions: list[dict[str, Any]] = []
        for source, result in source_results.items():
            for post in result.posts:
                codes = sorted(resolver.resolve(post.text))
                stocks = [resolver.stocks[code] for code in codes if code in resolver.stocks]
                excerpt = (post.content or post.title).strip()[:500]
                discussions.append(
                    {
                        "id": f"{source}:{post.post_id}",
                        "source": source,
                        "published_at": post.published_at.isoformat() if post.published_at else None,
                        "symbols": [stock.symbol for stock in stocks],
                        "stock_names": [stock.name for stock in stocks],
                        "title": post.title[:300],
                        "url": post.url,
                        "excerpt": excerpt,
                        "representative_comments": [comment[:220] for comment in post.comments[:3]],
                        "comments_count": len(post.comments),
                        "engagement": post.engagement,
                    }
                )
        return sorted(
            discussions,
            key=lambda item: (item.get("published_at") or "", item["id"]),
            reverse=True,
        )

    def _build_payload(
        self,
        started_at: datetime,
        finished_at: datetime,
        window_hours: int,
        source_results: dict[str, SourceResult],
        rankings: list[dict[str, Any]],
        ai_info: dict[str, Any],
        *,
        trigger: str,
        snapshot_slot: str,
        discussions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        for rank, row in enumerate(rankings, 1):
            row["rank"] = rank
            row["sentiment_status"] = "available" if row.get("sentiment_score") is not None else "unavailable"
            row.pop("_texts", None)
        source_statuses = [result.status for result in source_results.values()]
        overall_status = (
            "unavailable"
            if not source_statuses or all(status == "unavailable" for status in source_statuses)
            else "available"
            if all(status == "available" for status in source_statuses)
            else "partial"
        )
        snapshot_id = (
            f"{started_at.strftime('%Y-%m-%d-%H%M%S-%f')}-manual.json"
            if trigger == "manual"
            else f"{started_at.date().isoformat()}-{snapshot_slot}.json"
        )
        return {
            "schema_version": 2,
            "status": overall_status,
            "generated_at": finished_at.isoformat(),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "as_of": started_at.date().isoformat(),
            "trigger": trigger,
            "snapshot_slot": snapshot_slot,
            "snapshot_id": snapshot_id,
            "window_hours": window_hours,
            "sources": {
                source: {
                    "status": result.status,
                    "posts": len(result.posts),
                    "comments": result.comments_count,
                    "pages": result.pages,
                    "errors": result.errors,
                }
                for source, result in source_results.items()
            },
            "identified_symbols": len(rankings),
            "ai": ai_info,
            "rankings": rankings,
            "discussions": discussions,
        }

    def _load_previous_snapshot(self, as_of: date, snapshot_slot: str) -> dict[str, Any] | None:
        history_dir = self._output_root() / "history" / "snapshots"
        previous_day = (as_of - timedelta(days=1)).isoformat()
        path = history_dir / f"{previous_day}-{snapshot_slot}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("as_of") != previous_day
                or payload.get("snapshot_slot") != snapshot_slot
            ):
                return None
            return payload
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _rows_from_snapshot(
        payload: dict[str, Any] | None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        rows: dict[tuple[str, str], dict[str, Any]] = {}
        if not payload:
            return rows
        for row in payload.get("rankings", []):
            if not isinstance(row, dict):
                continue
            rows.setdefault(("", str(row.get("symbol", ""))), row)
            rows.setdefault(("", str(row.get("code", ""))), row)
        return rows

    @staticmethod
    def _source_coverage_is_comparable(
        current: dict[str, SourceResult],
        previous: dict[str, Any] | None,
        *,
        window_hours: int,
    ) -> bool:
        if (
            not previous
            or previous.get("window_hours") != window_hours
            or not isinstance(previous.get("sources"), dict)
        ):
            return False
        previous_sources = previous["sources"]
        if set(previous_sources) != set(current):
            return False
        return all(
            result.status == "available"
            and isinstance(previous_sources[source], dict)
            and previous_sources[source].get("status") == "available"
            for source, result in current.items()
        )

    def save(self, payload: dict[str, Any]) -> None:
        root = self._output_root()
        history_dir = root / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        day = str(payload["as_of"])
        snapshot_slot = str(payload.get("snapshot_slot") or "")
        snapshot_id = str(payload.get("snapshot_id") or "")
        _atomic_write_json(root / "latest.json", payload)
        _atomic_write_json(history_dir / f"{day}.json", payload)
        if snapshot_id:
            _atomic_write_json(history_dir / "snapshots" / snapshot_id, payload)
        csv_path = root / "social_sentiment_history.csv"
        existing: dict[tuple[str, str], dict[str, Any]] = {}
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    existing[(row.get("as_of", ""), row.get("symbol", ""))] = row
        fields = [
            "as_of", "generated_at", "started_at", "finished_at", "trigger", "snapshot_slot",
            "window_hours", "source_statuses", "rank", "symbol",
            "code", "company_name", "ptt_mentions", "dcard_mentions",
            "total_mentions", "unique_posts", "engagement", "volume_change_24h", "bullish_count",
            "neutral_count", "bearish_count", "sentiment", "sentiment_score", "sentiment_confidence",
            "sentiment_status", "social_heat_score", "sentiment_reason",
        ]
        existing = {key: row for key, row in existing.items() if key[0] != day}
        metadata = {
            "generated_at": payload.get("generated_at"),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "trigger": payload.get("trigger"),
            "snapshot_slot": snapshot_slot,
            "window_hours": payload.get("window_hours"),
            "source_statuses": json.dumps(
                {
                    source: data.get("status")
                    for source, data in sorted(payload.get("sources", {}).items())
                    if isinstance(data, dict)
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        }
        for row in payload["rankings"]:
            existing[(day, row["symbol"])] = {
                field: _csv_value(day, row, field, metadata) for field in fields
            }
        rows = sorted(
            existing.values(),
            key=lambda item: (item["as_of"], -int(item.get("rank") or 999999), item["symbol"]),
            reverse=True,
        )
        fd, temp_name = tempfile.mkstemp(prefix="social-sentiment-", suffix=".csv", dir=root)
        os.close(fd)
        Path(temp_name).unlink(missing_ok=True)
        temp_path = Path(temp_name)
        try:
            with temp_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            temp_path.replace(csv_path)
        finally:
            temp_path.unlink(missing_ok=True)


def _csv_value(day: str, row: dict[str, Any], field: str, metadata: dict[str, Any] | None = None) -> str:
    value = day if field == "as_of" else (metadata or {}).get(field, row.get(field))
    return "" if value is None else str(value)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    Path(temp_name).unlink(missing_ok=True)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _parse_ai_items(raw: str) -> list[dict[str, Any]]:
    text = raw.strip().removeprefix("```json").removesuffix("```").strip()
    start = min((idx for idx in (text.find("{"), text.find("[") ) if idx >= 0), default=-1)
    if start < 0:
        raise ValueError("AI response does not contain JSON")
    data = json.loads(text[start:])
    if isinstance(data, dict):
        data = data.get("items", [])
    if not isinstance(data, list):
        raise ValueError("AI response items is not a list")
    return [item for item in data if isinstance(item, dict)]


def _apply_ai_item(row: dict[str, Any], item: dict[str, Any]) -> None:
    sentiment = str(item.get("sentiment") or "").lower()
    if sentiment not in {"bullish", "neutral", "bearish"}:
        raise ValueError("invalid sentiment")
    score = float(item.get("score"))
    confidence = float(item.get("confidence"))
    if not -1.0 <= score <= 1.0 or not 0.0 <= confidence <= 1.0:
        raise ValueError("AI score/confidence out of range")
    row["sentiment"] = sentiment
    row["sentiment_score"] = round(score, 4)
    row["sentiment_confidence"] = round(confidence, 4)
    row["bullish_count"] = max(0, int(item.get("bullish_count", 0) or 0))
    row["neutral_count"] = max(0, int(item.get("neutral_count", 0) or 0))
    row["bearish_count"] = max(0, int(item.get("bearish_count", 0) or 0))
    row["sentiment_reason"] = str(item.get("reason") or "")[:240] or None


def load_social_sentiment(path: Path | None = None) -> dict[str, Any] | None:
    """Load latest or a date-specific local result for the read-only API."""
    target = Path(path or settings.data_dir / "social_sentiment" / "latest.json")
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    # Keep snapshots written by the collector MVP readable after the additive
    # product-UI fields were introduced.
    source_statuses = [
        detail.get("status")
        for detail in payload.get("sources", {}).values()
        if isinstance(detail, dict)
    ]
    payload.setdefault(
        "status",
        "unavailable"
        if not source_statuses or all(status == "unavailable" for status in source_statuses)
        else "available"
        if all(status == "available" for status in source_statuses)
        else "partial",
    )
    payload.setdefault("trigger", payload.get("snapshot_slot") or "unknown")
    payload.setdefault("started_at", payload.get("generated_at"))
    payload.setdefault("finished_at", payload.get("generated_at"))
    payload.setdefault("snapshot_id", None)
    payload.setdefault("discussions", [])
    for row in payload.get("rankings", []):
        if isinstance(row, dict):
            row.setdefault(
                "sentiment_status",
                "available" if row.get("sentiment_score") is not None else "unavailable",
            )
    return payload


def load_social_sentiment_date(target_date: date) -> dict[str, Any] | None:
    return load_social_sentiment(settings.data_dir / "social_sentiment" / "history" / f"{target_date.isoformat()}.json")


def load_social_sentiment_snapshot(target_date: date, snapshot_slot: str) -> dict[str, Any] | None:
    if snapshot_slot not in {"pre_open", "after_close"}:
        return None
    return load_social_sentiment(
        settings.data_dir
        / "social_sentiment"
        / "history"
        / "snapshots"
        / f"{target_date.isoformat()}-{snapshot_slot}.json"
    )


def list_social_sentiment_history(limit: int = 30) -> list[dict[str, Any]]:
    """Return compact local history metadata without exposing raw discussion text."""
    root = settings.data_dir / "social_sentiment" / "history"
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    snapshot_paths = list((root / "snapshots").glob("????-??-??-*.json")) if (root / "snapshots").exists() else []
    for path in sorted(snapshot_paths, reverse=True):
        payload = load_social_sentiment(path)
        if not payload or payload.get("snapshot_slot") not in {"pre_open", "after_close"}:
            continue
        entries.append(
            {
                "as_of": payload.get("as_of"),
                "generated_at": payload.get("generated_at"),
                "snapshot_slot": payload.get("snapshot_slot"),
                "trigger": payload.get("trigger"),
                "status": payload.get("status"),
                "identified_symbols": payload.get("identified_symbols", 0),
            }
        )
        if len(entries) >= limit:
            break
    return entries

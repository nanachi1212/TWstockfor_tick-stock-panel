from __future__ import annotations

import csv
import json
import time
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.social_sentiment import (
    DcardCollector,
    PTTStockCollector,
    SocialPost,
    SocialSentimentAlreadyRunningError,
    SocialSentimentRunLock,
    SocialSentimentService,
    SourceResult,
    StockMentionResolver,
    list_social_sentiment_history,
    load_social_sentiment,
    load_social_sentiment_snapshot,
    parse_dcard_html,
    parse_dcard_posts,
    parse_ptt_index,
    parse_ptt_post,
)
from app.taiwan.social_sentiment_jobs import SocialSentimentJobManager


class FakeSecurityMaster:
    def to_dataframe(self, *, supported_only: bool):
        assert supported_only is True
        return pl.DataFrame(
            [
                {"symbol": "2330.TWSE", "code": "2330", "name": "台積電", "instrument_type": "stock"},
                {"symbol": "2344.TWSE", "code": "2344", "name": "華邦電", "instrument_type": "stock"},
                {"symbol": "2025.TWSE", "code": "2025", "name": "千興", "instrument_type": "stock"},
                {"symbol": "5903.TWSE", "code": "5903", "name": "全家", "instrument_type": "stock"},
                {"symbol": "5347.TWSE", "code": "5347", "name": "世界", "instrument_type": "stock"},
            ]
        )


def test_stock_resolver_codes_names_alias_and_rejects_non_stock_number():
    resolver = StockMentionResolver(FakeSecurityMaster())
    found = resolver.resolve("2330 台積電 TSMC，2026 年價格 2345")
    assert found == {"2330": 3}


def test_stock_resolver_keeps_repeated_mentions():
    resolver = StockMentionResolver(FakeSecurityMaster())
    assert resolver.resolve("華邦電 2344 又提到華邦電") == {"2344": 3}


def test_stock_resolver_does_not_count_bare_year_but_keeps_adjacent_valid_code():
    resolver = StockMentionResolver(FakeSecurityMaster())
    assert resolver.resolve("2025 年營收展望") == {}
    assert resolver.resolve("2025 千興") == {"2025": 2}


def test_stock_resolver_does_not_count_ordinary_short_names_without_context():
    resolver = StockMentionResolver(FakeSecurityMaster())
    assert resolver.resolve("全家都看好全世界") == {}
    assert resolver.resolve("全家股票值得研究") == {"5903": 1}
    assert resolver.resolve("2025 年全家一起出遊") == {}


def test_ptt_index_and_post_parser():
    index = '''
    <div class="r-ent"><div class="title"><a href="/bbs/Stock/M.1.html">[標的] 台積電 2330</a></div>
    <div class="meta"><div class="author">alice</div><div class="date"> 9/28</div></div></div>
    <a class="btn wide" href="/bbs/Stock/index1.html">上頁</a>
    '''
    entries = parse_ptt_index(index)
    assert entries[0]["post_id"] == "M.1"
    assert entries[0]["title"] == "[標的] 台積電 2330"
    assert entries[0]["previous_url"].endswith("index1.html")

    post = '''
    <span class="article-meta-tag">作者</span><span class="article-meta-value">alice</span>
    <span class="article-meta-tag">標題</span><span class="article-meta-value">[標的] 台積電</span>
    <span class="article-meta-tag">時間</span><span class="article-meta-value">Mon Sep 28 12:00:00 2026</span>
    <div id="main-content">正文 2330<div class="push"><span class="push-tag">推 </span><span class="push-content">: 好文</span></div>
    <div class="push"><span class="push-tag">→ </span><span class="push-content">: 觀望</span></div></div>
    '''
    parsed = parse_ptt_post(post, post_id="M.1", url="https://ptt.cc/M.1.html")
    assert parsed.author == "alice"
    assert parsed.comments == ("好文", "觀望")
    assert parsed.engagement == 3


def test_ptt_post_parser_accepts_real_multi_token_push_classes():
    markup = """
    <span class="article-meta-tag">時間</span>
    <span class="article-meta-value">Mon Sep 28 12:00:00 2026</span>
    <div id="main-content">正文 2330
      <div class="push"><span class="hl push-tag">推 </span>
        <span class="push-userid">alice</span>
        <span class="f3 push-content">: 好文</span>
      </div>
    </div>
    """
    parsed = parse_ptt_post(markup, post_id="M.real", url="https://ptt.cc/M.real.html")
    assert parsed.comments == ("好文",)
    assert parsed.engagement == 2
    assert parsed.published_at == datetime(2026, 9, 28, 12, tzinfo=TAIPEI)


def test_ptt_post_parser_excludes_article_metadata_from_content():
    markup = """
    <div id="main-content">
      <span class="article-meta-tag">標題</span><span class="article-meta-value">[標的] 台積電 2330</span>
      <span class="article-meta-tag">時間</span><span class="article-meta-value">Mon Sep 28 12:00:00 2026</span>
      正文 2330
    </div>
    """
    parsed = parse_ptt_post(markup, post_id="M.meta", url="https://ptt.cc/M.meta.html")
    assert parsed.title == "[標的] 台積電 2330"
    assert parsed.content == "正文 2330"
    assert parsed.text.count("台積電") == 1


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class _FakePttClient:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url: str):
        return _FakeResponse(self.responses[url])


class _FakeDcardErrorResponse(_FakeResponse):
    def raise_for_status(self):
        raise RuntimeError("blocked")


class _FakeDcardClient:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url: str, **kwargs):
        if "/service/api/v2/forums/" in url:
            return _FakeDcardErrorResponse("")
        return _FakeResponse('<a href="/f/stock/p/123"><span>台積電 2330</span></a>')


def _ptt_index_entry(post_id: str, *, previous: str = "") -> str:
    previous_link = f'<a class="btn wide" href="{previous}">‹ 上頁</a>' if previous else ""
    return f"""
    <div class="r-ent"><div class="title"><a href="/bbs/Stock/{post_id}.html">[標的] 台積電 2330</a></div>
    <div class="meta"><div class="author">alice</div><div class="date"> 9/28</div></div></div>
    {previous_link}
    """


def _ptt_post_with_time(post_id: str, timestamp: str) -> str:
    return f"""
    <span class="article-meta-tag">時間</span><span class="article-meta-value">{timestamp}</span>
    <div id="main-content">正文 2330</div>
    """


def test_ptt_collector_filters_by_article_time_in_taipei_and_pages(monkeypatch):
    first_index = _ptt_index_entry("M.new", previous="/bbs/Stock/index1.html")
    second_index = _ptt_index_entry("M.old")
    responses = {
        "https://www.ptt.cc/bbs/Stock/index.html": first_index,
        "https://www.ptt.cc/bbs/Stock/M.new.html": _ptt_post_with_time(
            "M.new", "Mon Sep 28 07:00:00 2026"
        ),
        "https://www.ptt.cc/bbs/Stock/index1.html": second_index,
        "https://www.ptt.cc/bbs/Stock/M.old.html": _ptt_post_with_time(
            "M.old", "Mon Sep 28 05:00:00 2026"
        ),
    }
    monkeypatch.setattr(
        "app.taiwan.social_sentiment.httpx.Client",
        lambda **kwargs: _FakePttClient(responses),
    )
    collector = PTTStockCollector(request_delay=0)
    fetched = collector.fetch(since=datetime(2026, 9, 28, 6, tzinfo=TAIPEI), max_pages=2)
    assert fetched.pages == 2
    assert [post.post_id for post in fetched.posts] == ["M.new"]
    assert fetched.posts[0].published_at.tzinfo == TAIPEI


def test_dcard_undated_html_fallback_remains_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.taiwan.social_sentiment.httpx.Client",
        lambda **kwargs: _FakeDcardClient(),
    )
    collector = DcardCollector(request_delay=0)
    result = collector.fetch(since=datetime(2026, 9, 28, 6, tzinfo=TAIPEI), max_pages=1)
    assert result.posts == []
    assert result.fetch_succeeded is False
    assert result.status == "unavailable"


def test_source_success_with_no_posts_is_available_empty(tmp_path):
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", succeeded=True),),
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, tzinfo=UTC))
    assert payload["sources"]["ptt"]["status"] == "available"
    assert payload["sources"]["ptt"]["posts"] == 0


def test_report_date_and_history_use_asia_taipei(tmp_path):
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", succeeded=True),),
        output_dir=tmp_path,
    )
    payload = service.run(
        run_ai=False,
        now=datetime(2026, 9, 27, 16, 30, tzinfo=UTC),
    )
    assert payload["as_of"] == "2026-09-28"
    assert payload["generated_at"].endswith("+08:00")
    assert (tmp_path / "history" / "2026-09-28.json").exists()


def test_dcard_parser_normalizes_and_filters_old_posts():
    now = datetime(2026, 9, 28, 8, tzinfo=UTC)
    items = [
        {"id": 1, "title": "華邦電", "excerpt": "2344 噴", "createdAt": now.isoformat(), "likeCount": 2, "commentCount": 3},
        {"id": 2, "title": "舊文", "excerpt": "2344", "createdAt": (now - timedelta(days=3)).isoformat()},
    ]
    posts = parse_dcard_posts(items, forum="stock", since=now - timedelta(hours=24))
    assert len(posts) == 1
    assert posts[0].url.endswith("/stock/p/1")
    assert posts[0].engagement == 5


def test_dcard_parser_rejects_missing_timestamp_in_bounded_window():
    posts = parse_dcard_posts(
        [{"id": 3, "title": "缺時間", "excerpt": "2344"}],
        forum="stock",
        since=datetime(2026, 9, 28, 8, tzinfo=TAIPEI),
    )
    assert posts == []


def test_dcard_html_fallback_parser():
    markup = '<a href="/f/stock/p/123" class="post"><span>台積電 2330 噴</span></a>'
    posts = parse_dcard_html(markup, forum="stock")
    assert posts[0].post_id == "123"
    assert posts[0].title == "台積電 2330 噴"


def test_dcard_html_fallback_does_not_bypass_time_window():
    markup = '<a href="/f/stock/p/123" class="post"><span>台積電 2330 噴</span></a>'
    posts = parse_dcard_html(
        markup,
        forum="stock",
        since=datetime(2026, 9, 28, 8, tzinfo=TAIPEI),
    )
    assert posts == []


class FakeCollector:
    def __init__(
        self,
        source: str,
        posts: list[SocialPost] | None = None,
        error: str | None = None,
        succeeded: bool = False,
    ):
        self.source = source
        self.posts = posts or []
        self.error = error
        self.succeeded = succeeded

    def fetch(self, *, since, max_pages):
        return SourceResult(
            self.source,
            self.posts,
            [self.error] if self.error else [],
            pages=1,
            fetch_succeeded=self.succeeded,
        )


def _post(source: str, post_id: str, text: str, engagement: int = 2) -> SocialPost:
    return SocialPost(source, post_id, f"https://example/{post_id}", text, "", comments=("留言",), engagement=engagement)


def test_source_failure_does_not_stop_other_source(tmp_path):
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", error="blocked"), FakeCollector("dcard", [_post("dcard", "1", "2344 華邦電")])) ,
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, tzinfo=UTC))
    assert payload["sources"]["ptt"]["status"] == "unavailable"
    assert payload["sources"]["dcard"]["status"] == "available"
    assert payload["status"] == "partial"
    assert payload["identified_symbols"] == 1


def test_aggregation_and_heat_score(tmp_path):
    posts = [_post("ptt", "1", "2330 台積電 噴"), _post("ptt", "2", "2330 台積電 崩")]
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", posts), FakeCollector("dcard", [_post("dcard", "3", "2330")])) ,
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, tzinfo=UTC))
    row = payload["rankings"][0]
    assert row["total_mentions"] == 5
    assert row["unique_posts"] == 3
    assert row["ptt_mentions"] == 4
    assert row["dcard_mentions"] == 1
    assert row["social_heat_score"] > 0


def test_ai_json_is_applied_without_leaking_raw_text(tmp_path, monkeypatch):
    posts = [_post("ptt", "1", "2344 華邦電 看好")]
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", posts),),
        output_dir=tmp_path,
    )

    snapshot = object()

    async def fake_generate(messages, **kwargs):
        assert "2344" in messages[0]["content"]
        assert kwargs["config_snapshot"] is snapshot
        return '{"items":[{"symbol":"2344","sentiment":"bullish","score":0.72,"confidence":0.86,"bullish_count":1,"neutral_count":0,"bearish_count":0,"reason":"看好記憶體"}]}'

    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: True)
    monkeypatch.setattr("app.services.ai_provider.snapshot_ai_provider_config", lambda: snapshot)
    monkeypatch.setattr("app.services.ai_provider.generate_ai_text", fake_generate)
    payload = service.run(now=datetime(2026, 9, 28, tzinfo=UTC))
    row = payload["rankings"][0]
    assert row["sentiment"] == "bullish"
    assert row["sentiment_score"] == 0.72
    assert row["sentiment_status"] == "available"
    assert "2344 華邦電 看好" not in json.dumps(payload, ensure_ascii=False)


def test_ai_unavailable_fallback_and_output_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330")]),),
        output_dir=tmp_path,
    )
    payload = service.run(now=datetime(2026, 9, 28, tzinfo=UTC))
    assert payload["ai"]["status"] == "unavailable"
    assert payload["rankings"][0]["sentiment_score"] is None
    assert payload["rankings"][0]["sentiment_status"] == "unavailable"
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "history" / "2026-09-28.json").exists()
    assert (tmp_path / "social_sentiment_history.csv").exists()


def test_no_ai_run_is_explicitly_not_queried(tmp_path):
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330")]),),
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, tzinfo=TAIPEI))
    assert payload["ai"]["status"] == "not_queried"


def test_ai_partial_results_are_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: True)

    async def partial_generate(*args, **kwargs):
        return '{"items":[{"symbol":"2330","sentiment":"bullish","score":0.5,"confidence":0.8,"bullish_count":1,"neutral_count":0,"bearish_count":0,"reason":"偏多"}]}'

    monkeypatch.setattr("app.services.ai_provider.generate_ai_text", partial_generate)
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(
            FakeCollector("ptt", [_post("ptt", "1", "2330"), _post("ptt", "2", "2344")]),
        ),
        output_dir=tmp_path,
    )
    payload = service.run(now=datetime(2026, 9, 28, tzinfo=TAIPEI))
    assert payload["ai"]["status"] == "degraded"
    assert payload["ai"]["analyzed_symbols"] == 1


def test_ai_invalid_json_degrades_only_batch(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: True)

    async def bad_generate(*args, **kwargs):
        return "not json"

    monkeypatch.setattr("app.services.ai_provider.generate_ai_text", bad_generate)
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330")]),),
        output_dir=tmp_path,
    )
    payload = service.run(now=datetime(2026, 9, 28, tzinfo=UTC))
    # The run survives, but zero analyzed symbols means AI is unavailable, not partial.
    assert payload["ai"]["status"] == "unavailable"
    assert payload["rankings"][0]["sentiment"] == "unavailable"


def test_manual_run_writes_immutable_snapshot_with_bounded_discussions(tmp_path):
    post = SocialPost(
        source="ptt",
        post_id="M.manual",
        url="https://www.ptt.cc/bbs/Stock/M.manual.html",
        title="台積電 2330 討論",
        content="看好台積電後續表現",
        published_at=datetime(2026, 9, 30, 9, 30, tzinfo=TAIPEI),
        comments=tuple(f"代表留言 {index}" for index in range(10)),
        engagement=20,
    )
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [post], succeeded=True),),
        output_dir=tmp_path,
    )

    payload = service.run(
        run_ai=False,
        trigger="manual",
        now=datetime(2026, 9, 30, 9, 38, 12, 123456, tzinfo=TAIPEI),
    )

    assert payload["trigger"] == "manual"
    assert payload["snapshot_slot"] == "manual"
    assert payload["started_at"].endswith("+08:00")
    assert payload["finished_at"].endswith("+08:00")
    assert len(payload["discussions"]) == 1
    discussion = payload["discussions"][0]
    assert discussion["symbols"] == ["2330.TWSE"]
    assert discussion["comments_count"] == 10
    assert discussion["representative_comments"] == ["代表留言 0", "代表留言 1", "代表留言 2"]
    snapshots = list((tmp_path / "history" / "snapshots").glob("*-manual.json"))
    assert len(snapshots) == 1
    assert snapshots[0].name == payload["snapshot_id"]
    assert not (tmp_path / "history" / "snapshots" / "2026-09-30-pre_open.json").exists()
    assert "代表留言 9" not in snapshots[0].read_text(encoding="utf-8")


def test_explicit_late_schedule_is_recorded_as_missed_schedule(tmp_path):
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330")], succeeded=True),),
        output_dir=tmp_path,
    )

    payload = service.run(
        run_ai=False,
        trigger="pre_open",
        now=datetime(2026, 9, 30, 9, 31, tzinfo=TAIPEI),
    )

    assert payload["trigger"] == "missed_schedule"
    assert payload["snapshot_slot"] == "pre_open"
    assert (tmp_path / "history" / "snapshots" / "2026-09-30-pre_open.json").exists()


def test_social_sentiment_lock_rejects_second_process_style_run(tmp_path):
    lock = SocialSentimentRunLock(tmp_path / "run.lock")
    assert lock.acquire() is True
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", succeeded=True),),
        output_dir=tmp_path,
    )
    try:
        with pytest.raises(SocialSentimentAlreadyRunningError):
            service.run(run_ai=False, trigger="manual")
    finally:
        lock.release()


def test_manual_background_job_completes_partial_without_blocking_start(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(
            FakeCollector("ptt", [_post("ptt", "1", "2330")], succeeded=True),
            FakeCollector("dcard", error="HTTP 403"),
        ),
        output_dir=tmp_path,
    )
    manager = SocialSentimentJobManager(service_factory=lambda: service, output_dir=tmp_path)

    started = manager.start_manual()

    assert started["status"] == "running"
    job = None
    for _ in range(100):
        job = manager.get_job(started["job_id"])
        if job and job["status"] in {"completed", "partial", "failed"}:
            break
        time.sleep(0.01)
    assert job is not None
    assert job["status"] == "partial"
    assert job["ptt_status"] == "available"
    assert job["dcard_status"] == "unavailable"
    assert job["ai_status"] == "unavailable"
    assert job["symbols_identified"] == 1
    assert job["snapshot_id"].endswith("-manual.json")


def test_manual_job_response_does_not_expose_provider_secrets(tmp_path):
    manager = SocialSentimentJobManager(output_dir=tmp_path)
    manager._jobs["safe-job"] = {"job_id": "safe-job", "status": "completed"}
    manager._payloads["safe-job"] = {
        "rankings": [],
        "discussions": [],
        "ai": {"api_key": "sk-test-secret"},
        "provider_config": {"token": "private-token"},
    }

    response = manager.get_job("safe-job")

    assert response is not None
    serialized = json.dumps(response)
    assert "sk-test-secret" not in serialized
    assert "private-token" not in serialized


def test_volume_change_uses_exact_previous_calendar_day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    history_dir = tmp_path / "history" / "snapshots"
    history_dir.mkdir(parents=True)
    for day, total in (("2026-09-28", 100), ("2026-09-27", 4), ("2026-09-26", 2)):
        (history_dir / f"{day}-pre_open.json").write_text(
            json.dumps(
                {
                    "as_of": day,
                    "snapshot_slot": "pre_open",
                    "window_hours": 24,
                    "sources": {"ptt": {"status": "available"}},
                    "rankings": [{"symbol": "2330.TWSE", "code": "2330", "total_mentions": total}],
                }
            ),
            encoding="utf-8",
        )
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330 " * 8)]),),
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, tzinfo=TAIPEI))
    assert payload["rankings"][0]["volume_change_24h"] == 1.0


def test_volume_change_requires_comparable_source_coverage(tmp_path):
    history_dir = tmp_path / "history" / "snapshots"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-09-27-pre_open.json").write_text(
        json.dumps(
            {
                "as_of": "2026-09-27",
                "snapshot_slot": "pre_open",
                "window_hours": 24,
                "sources": {
                    "ptt": {"status": "available"},
                    "dcard": {"status": "unavailable"},
                },
                "rankings": [{"symbol": "2330.TWSE", "code": "2330", "total_mentions": 4}],
            }
        ),
        encoding="utf-8",
    )
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(
            FakeCollector("ptt", [_post("ptt", "1", "2330 " * 8)], succeeded=True),
            FakeCollector("dcard", [_post("dcard", "2", "2344")], succeeded=True),
        ),
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, tzinfo=TAIPEI))
    row = next(row for row in payload["rankings"] if row["code"] == "2330")
    assert row["volume_change_24h"] is None


def test_volume_change_requires_matching_window(tmp_path):
    history_dir = tmp_path / "history" / "snapshots"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-09-27-pre_open.json").write_text(
        json.dumps(
            {
                "as_of": "2026-09-27",
                "snapshot_slot": "pre_open",
                "window_hours": 24,
                "sources": {"ptt": {"status": "available"}},
                "rankings": [{"symbol": "2330.TWSE", "code": "2330", "total_mentions": 4}],
            }
        ),
        encoding="utf-8",
    )
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330 " * 8)], succeeded=True),),
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, window_hours=6, now=datetime(2026, 9, 28, tzinfo=TAIPEI))
    assert payload["rankings"][0]["volume_change_24h"] is None


def test_volume_change_uses_same_time_snapshot_slot(tmp_path):
    history_dir = tmp_path / "history" / "snapshots"
    history_dir.mkdir(parents=True)
    for slot, total in (("pre_open", 4), ("after_close", 100)):
        (history_dir / f"2026-09-27-{slot}.json").write_text(
            json.dumps(
                {
                    "as_of": "2026-09-27",
                    "snapshot_slot": slot,
                    "window_hours": 24,
                    "sources": {"ptt": {"status": "available"}},
                    "rankings": [{"symbol": "2330.TWSE", "code": "2330", "total_mentions": total}],
                }
            ),
            encoding="utf-8",
        )
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330 " * 8)], succeeded=True),),
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, 8, 30, tzinfo=TAIPEI))
    assert payload["snapshot_slot"] == "pre_open"
    assert payload["rankings"][0]["volume_change_24h"] == 1.0


def test_history_rerun_replaces_complete_same_day_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    collector = FakeCollector(
        "ptt",
        [_post("ptt", "1", "2330"), _post("ptt", "2", "2344")],
    )
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(collector,),
        output_dir=tmp_path,
    )
    now = datetime(2026, 9, 28, tzinfo=TAIPEI)
    first = service.run(run_ai=False, now=now)
    with (tmp_path / "social_sentiment_history.csv").open(encoding="utf-8", newline="") as handle:
        first_rows = list(csv.DictReader(handle))
    assert [int(row["rank"]) for row in first_rows] == [1, 2]
    collector.posts = [_post("ptt", "1", "2330")]
    second = service.run(run_ai=False, now=now)
    with (tmp_path / "social_sentiment_history.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["symbol"] for row in rows} == {"2330.TWSE"}
    assert [int(row["rank"]) for row in rows] == [1]
    assert {row["symbol"] for row in first["rankings"]} == {"2330.TWSE", "2344.TWSE"}
    assert {row["symbol"] for row in second["rankings"]} == {"2330.TWSE"}


def test_output_json_is_valid_and_csv_contains_contract_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("dcard", [_post("dcard", "1", "2344")]),),
        output_dir=tmp_path,
    )
    service.run(now=datetime(2026, 9, 28, tzinfo=UTC))
    data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    csv_path = tmp_path / "social_sentiment_history.csv"
    csv_text = csv_path.read_text(encoding="utf-8")
    assert data["rankings"][0]["symbol"] == "2344.TWSE"
    assert "sentiment_score" in csv_text
    assert "sentiment_status" in csv_text
    assert data["snapshot_slot"] == "pre_open"
    assert (tmp_path / "history" / "snapshots" / "2026-09-28-pre_open.json").exists()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["snapshot_slot"] == "pre_open"
    assert row["window_hours"] == "24"
    assert json.loads(row["source_statuses"]) == {"dcard": "available"}


def test_both_sources_unavailable_keeps_overall_unavailable(tmp_path):
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", error="blocked"), FakeCollector("dcard", error="HTTP 403")),
        output_dir=tmp_path,
    )
    payload = service.run(run_ai=False, now=datetime(2026, 9, 28, tzinfo=TAIPEI))
    assert payload["status"] == "unavailable"
    assert payload["rankings"] == []


def test_snapshot_and_history_metadata_use_local_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.taiwan.social_sentiment.settings.data_dir", tmp_path)
    snapshot_dir = tmp_path / "social_sentiment" / "history" / "snapshots"
    snapshot_dir.mkdir(parents=True)
    payload = {
        "as_of": "2026-09-29",
        "generated_at": "2026-09-29T15:30:00+08:00",
        "snapshot_slot": "after_close",
        "status": "partial",
        "identified_symbols": 65,
        "rankings": [],
    }
    (snapshot_dir / "2026-09-29-after_close.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_social_sentiment_snapshot(date(2026, 9, 29), "after_close")
    assert loaded is not None
    assert loaded["as_of"] == payload["as_of"]
    assert loaded["trigger"] == "after_close"
    assert loaded["discussions"] == []
    assert list_social_sentiment_history() == [{
        "as_of": "2026-09-29",
        "generated_at": "2026-09-29T15:30:00+08:00",
        "snapshot_slot": "after_close",
        "trigger": "after_close",
        "status": "partial",
        "identified_symbols": 65,
    }]


def test_load_legacy_snapshot_adds_product_ui_status_fields(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "sources": {
                    "ptt": {"status": "available"},
                    "dcard": {"status": "unavailable"},
                },
                "rankings": [
                    {"symbol": "2330.TWSE", "sentiment_score": 0.6},
                    {"symbol": "2344.TWSE", "sentiment_score": None},
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = load_social_sentiment(path)

    assert payload is not None
    assert payload["status"] == "partial"
    assert payload["rankings"][0]["sentiment_status"] == "available"
    assert payload["rankings"][1]["sentiment_status"] == "unavailable"

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import polars as pl

from app.taiwan.social_sentiment import (
    SocialPost,
    SocialSentimentService,
    SourceResult,
    StockMentionResolver,
    parse_dcard_posts,
    parse_dcard_html,
    parse_ptt_index,
    parse_ptt_post,
)


class FakeSecurityMaster:
    def to_dataframe(self, *, supported_only: bool):
        assert supported_only is True
        return pl.DataFrame(
            [
                {"symbol": "2330.TWSE", "code": "2330", "name": "台積電", "instrument_type": "stock"},
                {"symbol": "2344.TWSE", "code": "2344", "name": "華邦電", "instrument_type": "stock"},
                {"symbol": "2025.TWSE", "code": "2025", "name": "千興", "instrument_type": "stock"},
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


def test_dcard_html_fallback_parser():
    markup = '<a href="/f/stock/p/123" class="post"><span>台積電 2330 噴</span></a>'
    posts = parse_dcard_html(markup, forum="stock")
    assert posts[0].post_id == "123"
    assert posts[0].title == "台積電 2330 噴"


class FakeCollector:
    def __init__(self, source: str, posts: list[SocialPost] | None = None, error: str | None = None):
        self.source = source
        self.posts = posts or []
        self.error = error

    def fetch(self, *, since, max_pages):
        return SourceResult(self.source, self.posts, [self.error] if self.error else [], pages=1)


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

    async def fake_generate(messages, **kwargs):
        assert "2344" in messages[0]["content"]
        return '{"items":[{"symbol":"2344","sentiment":"bullish","score":0.72,"confidence":0.86,"bullish_count":1,"neutral_count":0,"bearish_count":0,"reason":"看好記憶體"}]}'

    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: True)
    monkeypatch.setattr("app.services.ai_provider.generate_ai_text", fake_generate)
    payload = service.run(now=datetime(2026, 9, 28, tzinfo=UTC))
    row = payload["rankings"][0]
    assert row["sentiment"] == "bullish"
    assert row["sentiment_score"] == 0.72
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
    assert (tmp_path / "latest.json").exists()
    assert (tmp_path / "history" / "2026-09-28.json").exists()
    assert (tmp_path / "social_sentiment_history.csv").exists()


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
    assert payload["ai"]["status"] == "degraded"
    assert payload["rankings"][0]["sentiment"] == "unavailable"


def test_history_rerun_upserts_same_day(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("ptt", [_post("ptt", "1", "2330")]),),
        output_dir=tmp_path,
    )
    now = datetime(2026, 9, 28, tzinfo=UTC)
    service.run(now=now)
    service.run(now=now)
    lines = (tmp_path / "social_sentiment_history.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_output_json_is_valid_and_csv_contains_contract_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.ai_provider.ai_configured", lambda: False)
    service = SocialSentimentService(
        security_master=FakeSecurityMaster(),
        collectors=(FakeCollector("dcard", [_post("dcard", "1", "2344")]),),
        output_dir=tmp_path,
    )
    service.run(now=datetime(2026, 9, 28, tzinfo=UTC))
    data = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    csv_text = (tmp_path / "social_sentiment_history.csv").read_text(encoding="utf-8")
    assert data["rankings"][0]["symbol"] == "2344.TWSE"
    assert "sentiment_score" in csv_text

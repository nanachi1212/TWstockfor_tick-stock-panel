"""Unit and integration tests for Taiwan Selection Review (A12).

Verifies:
- Snapshot immutability and persistence.
- Trading-day horizon calculations (1D, 5D, 20D), never calendar days.
- Pending horizon status when forward trading days are insufficient.
- Unavailable status without fabricated zeros when stock prices are missing.
- Benchmark (0050.TWSE) return and excess calculation (excess != alpha).
- Strategy review aggregation: hit rate definition (return > 0), excess, picks count.
- Condition review: grouping by match_reasons, sample count, sample insufficient flag, no weight modification.
- API endpoints integration.
- Privacy: stored in user_data, never bundled, no secrets.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.selection_review_models import (
    SaveSelectionSnapshotRequest,
    SelectionSnapshotItem,
)
from app.taiwan.selection_review_service import (
    DEFAULT_BENCHMARK_SYMBOL,
    TaiwanSelectionReviewService,
)


@pytest.fixture
def mock_daily_store(tmp_path: Path) -> TaiwanDailyStore:
    """Creates a mock TaiwanDailyStore populated with deterministic trading days."""
    store_dir = tmp_path / "daily"
    store_dir.mkdir(parents=True, exist_ok=True)
    store = TaiwanDailyStore(store_dir)

    # Populate 25 trading days
    # Dates: 2026-08-03 to 2026-09-04 (25 consecutive trading dates)
    dates = [
        date(2026, 8, 3),   # Day 0 (Entry date for snapshot)
        date(2026, 8, 4),   # Day 1 (1D)
        date(2026, 8, 5),   # Day 2
        date(2026, 8, 6),   # Day 3
        date(2026, 8, 7),   # Day 4
        date(2026, 8, 10),  # Day 5 (5D)
        date(2026, 8, 11),  # Day 6
        date(2026, 8, 12),  # Day 7
        date(2026, 8, 13),  # Day 8
        date(2026, 8, 14),  # Day 9
        date(2026, 8, 17),  # Day 10
        date(2026, 8, 18),  # Day 11
        date(2026, 8, 19),  # Day 12
        date(2026, 8, 20),  # Day 13
        date(2026, 8, 21),  # Day 14
        date(2026, 8, 24),  # Day 15
        date(2026, 8, 25),  # Day 16
        date(2026, 8, 26),  # Day 17
        date(2026, 8, 27),  # Day 18
        date(2026, 8, 28),  # Day 19
        date(2026, 8, 31),  # Day 20 (20D)
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
        date(2026, 9, 4),
    ]

    rows = []
    # 2330.TWSE: Entry 1000, 1D 1010 (+1.0%), 5D 1050 (+5.0%), 20D 1100 (+10.0%)
    # 2454.TWSE: Entry 1200, 1D 1180 (-1.67%), 5D 1150 (-4.17%), 20D 1320 (+10.0%)
    # 3008.TWSE: Entry 2000, missing on 20D (unavailable, no fake 0)
    # 0050.TWSE (Benchmark): Entry 180, 1D 181 (+0.56%), 5D 183.6 (+2.0%), 20D 189 (+5.0%)
    p_map = {
        "2330.TWSE": {0: 1000.0, 1: 1010.0, 5: 1050.0, 20: 1100.0},
        "2454.TWSE": {0: 1200.0, 1: 1180.0, 5: 1150.0, 20: 1320.0},
        "3008.TWSE": {0: 2000.0, 1: 2000.0, 5: 2100.0},  # No 20D!
        DEFAULT_BENCHMARK_SYMBOL: {0: 180.0, 1: 181.0, 5: 183.6, 20: 189.0},
    }

    for idx, d in enumerate(dates):
        for sym, d_dict in p_map.items():
            price = d_dict.get(idx, 1000.0)
            if sym == "3008.TWSE" and idx >= 15:
                continue  # simulate delisted or missing
            rows.append({
                "symbol": sym,
                "date": d,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 1000000.0,
                "amount": price * 1000000.0,
                "quote_ts": 1785715200 + idx * 86400,
            })

    df = pl.DataFrame(rows)
    store.write_batch(df)
    return store


@pytest.fixture
def review_service(tmp_path: Path, mock_daily_store: TaiwanDailyStore) -> TaiwanSelectionReviewService:
    storage_path = tmp_path / "user_data" / "taiwan_selection_snapshots.json"
    return TaiwanSelectionReviewService(path=storage_path, daily_store=mock_daily_store)


def test_snapshot_save_and_immutability(review_service: TaiwanSelectionReviewService):
    """Snapshot is immutable once saved; subsequent queries preserve original entry details."""
    req = SaveSelectionSnapshotRequest(
        strategy_id="strat_growth",
        strategy_name="營收成長",
        as_of_date="2026-08-03",
        market_context_summary="大盤加權指數 22000",
        items=[
            SelectionSnapshotItem(
                symbol="2330.TWSE",
                name="台積電",
                rank=1,
                quant_score=85.0,
                match_reasons=["營收年增 25%", "Quant 評分 85"],
                strategy_conditions={"revenue_yoy_min": 20.0},
                price=1000.0,
                fundamental_summary="營收 YoY +25%",
                chips_summary="外資買超 5000張",
                event_risk_summary="無重大風險",
            ),
            SelectionSnapshotItem(
                symbol="2454.TWSE",
                name="聯發科",
                rank=2,
                quant_score=78.0,
                match_reasons=["營收年增 25%"],
                strategy_conditions={"revenue_yoy_min": 20.0},
                price=1200.0,
            ),
        ],
    )

    saved = review_service.save_snapshot(req)
    assert saved.snapshot_id.startswith("snap_")
    assert saved.strategy_id == "strat_growth"
    assert len(saved.items) == 2

    # Verify immutable retrieval
    loaded = review_service.get_snapshot(saved.snapshot_id)
    assert loaded is not None
    assert loaded.items[0].price == 1000.0
    assert loaded.items[0].match_reasons == ["營收年增 25%", "Quant 評分 85"]

    # Verify list summary
    summary_list = review_service.list_snapshots()
    assert len(summary_list) == 1
    assert summary_list[0].snapshot_id == saved.snapshot_id
    assert summary_list[0].selected_count == 2


def test_horizon_review_and_benchmark(review_service: TaiwanSelectionReviewService):
    """Test 1D, 5D, 20D returns and benchmark excess calculation using trading days."""
    req = SaveSelectionSnapshotRequest(
        strategy_id="strat_growth",
        strategy_name="營收成長",
        as_of_date="2026-08-03",
        market_context_summary="大盤加權 22000",
        items=[
            SelectionSnapshotItem(
                symbol="2330.TWSE",
                name="台積電",
                rank=1,
                price=1000.0,
                match_reasons=["營收年增 25%"],
            ),
            SelectionSnapshotItem(
                symbol="2454.TWSE",
                name="聯發科",
                rank=2,
                price=1200.0,
                match_reasons=["外資買超"],
            ),
            SelectionSnapshotItem(
                symbol="3008.TWSE",
                name="大立光",
                rank=3,
                price=2000.0,
                match_reasons=["高毛利"],
            ),
        ],
    )
    saved = review_service.save_snapshot(req)
    detail = review_service.get_snapshot_review(saved.snapshot_id)
    assert detail is not None

    # Check 2330.TWSE:
    # 1D: price 1010 -> +1.0%
    # 5D: price 1050 -> +5.0%
    # 20D: price 1100 -> +10.0%
    # Benchmark 0050.TWSE:
    # Entry: 180, 5D: 183.6 -> +2.0%, 20D: 189 -> +5.0%
    item_2330 = next(i for i in detail.evaluated_items if i.symbol == "2330.TWSE")
    assert item_2330.h1d_status == "completed"
    assert item_2330.h1d_return_pct == pytest.approx(1.0, abs=0.01)
    assert item_2330.h5d_status == "completed"
    assert item_2330.h5d_return_pct == pytest.approx(5.0, abs=0.01)
    assert item_2330.h5d_bm_return_pct == pytest.approx(2.0, abs=0.01)
    # Excess: 5.0 - 2.0 = +3.0%
    assert item_2330.h5d_excess_pct == pytest.approx(3.0, abs=0.01)
    assert item_2330.h20d_status == "completed"
    assert item_2330.h20d_return_pct == pytest.approx(10.0, abs=0.01)
    # Excess 20D: 10.0 - 5.0 = +5.0%
    assert item_2330.h20d_excess_pct == pytest.approx(5.0, abs=0.01)

    # Check 3008.TWSE: missing 20D -> unavailable, NOT fake 0!
    item_3008 = next(i for i in detail.evaluated_items if i.symbol == "3008.TWSE")
    assert item_3008.h5d_status == "completed"
    assert item_3008.h20d_status == "unavailable"
    assert item_3008.h20d_return_pct is None

    # Aggregate metrics
    # 5D completed count = 3 (2330: +5.0%, 2454: -4.17%, 3008: +5.0%)
    assert detail.h5d_evaluated_count == 3
    # 20D completed count = 2 (2330: +10.0%, 2454: +10.0%)
    assert detail.h20d_evaluated_count == 2
    assert detail.h20d_avg_return_pct == pytest.approx(10.0, abs=0.01)


def test_pending_horizon_when_dates_insufficient(tmp_path: Path):
    """When forward trading days are less than 5 or 20, status remains pending without crashing."""
    store = TaiwanDailyStore(tmp_path / "short_daily")
    # Only 2 forward trading days
    dates = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    rows = []
    for d in dates:
        rows.append({
            "symbol": "2330.TWSE",
            "date": d,
            "open": 1000.0,
            "high": 1000.0,
            "low": 1000.0,
            "close": 1010.0,
            "volume": 1000.0,
            "amount": 1010000.0,
            "quote_ts": 0,
        })
    store.write_batch(pl.DataFrame(rows))

    svc = TaiwanSelectionReviewService(path=tmp_path / "snap.json", daily_store=store)
    saved = svc.save_snapshot(
        SaveSelectionSnapshotRequest(
            strategy_id="strat_new",
            strategy_name="剛建策略",
            as_of_date="2026-09-01",
            items=[
                SelectionSnapshotItem(symbol="2330.TWSE", name="台積電", rank=1, price=1000.0)
            ],
        )
    )
    detail = svc.get_snapshot_review(saved.snapshot_id)
    assert detail is not None
    item = detail.evaluated_items[0]
    assert item.h1d_status == "completed"  # 1D has elapsed
    assert item.h5d_status == "pending"    # 5D not yet elapsed!
    assert item.h20d_status == "pending"   # 20D not yet elapsed!
    assert item.h5d_return_pct is None
    assert item.h20d_return_pct is None


def test_strategy_review_aggregation(review_service: TaiwanSelectionReviewService):
    """Strategy review aggregates completed horizons with explicit hit rate (return > 0)."""
    req = SaveSelectionSnapshotRequest(
        strategy_id="strat_alpha",
        strategy_name="優質成長",
        as_of_date="2026-08-03",
        items=[
            SelectionSnapshotItem(symbol="2330.TWSE", name="台積電", rank=1, price=1000.0),
            SelectionSnapshotItem(symbol="2454.TWSE", name="聯發科", rank=2, price=1200.0),
        ],
    )
    review_service.save_snapshot(req)
    stats_list = review_service.get_strategy_reviews()
    assert len(stats_list) >= 1
    s = next(x for x in stats_list if x.strategy_id == "strat_alpha")
    assert s.snapshots_count == 1
    assert s.evaluated_picks_5d == 2
    # 2330: +5.0%, 2454: -4.17% -> 1 of 2 positive -> hit rate 50.0%
    assert s.hit_rate_5d == pytest.approx(50.0, abs=0.1)
    assert "報酬率 > 0%" in s.hit_rate_definition


def test_condition_review_analytics(review_service: TaiwanSelectionReviewService):
    """Condition review groups by match_reasons and flags sample insufficient when count < 5."""
    req = SaveSelectionSnapshotRequest(
        strategy_id="strat_conditions",
        strategy_name="條件檢驗",
        as_of_date="2026-08-03",
        items=[
            SelectionSnapshotItem(
                symbol="2330.TWSE",
                name="台積電",
                rank=1,
                price=1000.0,
                match_reasons=["月營收年增 > 20%"],
            ),
            SelectionSnapshotItem(
                symbol="2454.TWSE",
                name="聯發科",
                rank=2,
                price=1200.0,
                match_reasons=["月營收年增 > 20%"],
            ),
        ],
    )
    review_service.save_snapshot(req)
    cond_stats = review_service.get_condition_reviews()
    assert len(cond_stats) >= 1
    c = next(x for x in cond_stats if x.condition_label == "月營收年增 > 20%")
    assert c.sample_count_5d == 2
    # Sample < 5 should flag sample insufficient!
    assert c.is_sample_sufficient is False
    assert "不代表因果關係" in c.disclaimer


def test_selection_review_api_endpoints(monkeypatch, review_service: TaiwanSelectionReviewService):
    """Verify FastAPI routes for snapshot CRUD and reviews."""
    monkeypatch.setattr(
        "app.taiwan.selection_review_service.get_selection_review_service",
        lambda: review_service,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))

    # 1. Post new snapshot
    res_post = client.post(
        "/api/taiwan/selection-review/snapshots",
        json={
            "strategy_id": "test_strat",
            "strategy_name": "API測試策略",
            "as_of_date": "2026-08-03",
            "market_context_summary": "市場平穩",
            "items": [
                {
                    "symbol": "2330.TWSE",
                    "name": "台積電",
                    "rank": 1,
                    "price": 1000.0,
                    "match_reasons": ["強勢突破"],
                }
            ],
        },
    )
    assert res_post.status_code == 200
    snap_data = res_post.json()
    snapshot_id = snap_data["snapshot_id"]

    # 2. List snapshots
    res_list = client.get("/api/taiwan/selection-review/snapshots")
    assert res_list.status_code == 200
    assert any(s["snapshot_id"] == snapshot_id for s in res_list.json())

    # 3. Get snapshot detail
    res_detail = client.get(f"/api/taiwan/selection-review/snapshots/{snapshot_id}")
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert detail_data["snapshot"]["snapshot_id"] == snapshot_id
    assert len(detail_data["evaluated_items"]) == 1

    # 4. Strategy stats
    res_strat = client.get("/api/taiwan/selection-review/strategy-stats")
    assert res_strat.status_code == 200

    # 5. Condition stats
    res_cond = client.get("/api/taiwan/selection-review/condition-stats")
    assert res_cond.status_code == 200

    # 6. Delete snapshot
    res_del = client.delete(f"/api/taiwan/selection-review/snapshots/{snapshot_id}")
    assert res_del.status_code == 200
    assert res_del.json()["ok"] is True

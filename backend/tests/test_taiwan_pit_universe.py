"""PIT universe (market truth) and quant eligibility (model policy) contract tests.

The whole point of these two modules is that they are separate: truth must not
be edited to make a model's universe look better.  These tests pin that seam.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.taiwan.historical_classification import HistoricalClassificationStore
from app.taiwan.observed_universe import ObservedUniverseStore
from app.taiwan.pit_universe import LISTING_METADATA_STATUS, PitUniverse
from app.taiwan.quant_eligibility import (
    PIT_POLICY_EXCEPTIONS,
    PRIMARY_VERIFIED,
    SECONDARY_OBSERVED,
    EligibilityPolicy,
    assert_oos_claimable,
    describe_tier,
    eligible,
    market_truth_gate,
    pit_usable,
)

D1 = date(2015, 1, 5)
D2 = date(2015, 1, 6)


def _observation(code: str, exchange: str, day: date) -> dict:
    return {
        "date": day, "raw_code": code, "exchange": exchange, "observed": True,
        "raw_name": code, "raw_source_category": "每日收盤行情",
        "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5,
        "volume": 1000.0, "amount": 10500.0,
        "instrument_type": None, "instrument_type_status": "data_insufficient",
        "source": "test", "retrieved_at": "now",
    }


def _classification(code: str, kind: str, day: date) -> dict:
    return {
        "code": code, "exchange": "TWSE", "instrument_type": kind,
        "industry": None, "industry_status": "data_insufficient",
        "classification_effective_from": day,
        "classification_source": f"twse:MI_INDEX@{day.isoformat()}",
        "classification_status": "verified", "retrieved_at": "now",
    }


def _universe(tmp_path: Path) -> PitUniverse:
    census = ObservedUniverseStore(tmp_path / "observed_universe")
    classification = HistoricalClassificationStore(tmp_path / "cls")
    census.write("TWSE", D1, [_observation("2330", "TWSE", D1),
                              _observation("0050", "TWSE", D1),
                              _observation("9999", "TWSE", D1)])
    census.write("TPEX", D1, [_observation("8069", "TPEX", D1),
                              _observation("5371", "TPEX", D1)])
    classification.write(D1, [_classification("2330", "stock", D1),
                              _classification("0050", "etf", D1)])
    return PitUniverse(census=census, classification=classification)


# ── Market truth ───────────────────────────────────────────────

def test_three_facts_are_recorded_independently(tmp_path: Path) -> None:
    frame = _universe(tmp_path).as_of(D1)
    rows = {r["market_symbol"]: r for r in frame.iter_rows(named=True)}

    assert rows["2330.TWSE"]["observed_on_market"] is True
    assert rows["2330.TWSE"]["tradable_source"] == "official_daily_snapshot"
    assert rows["2330.TWSE"]["listing_metadata_status"] == "verified"
    assert rows["2330.TWSE"]["instrument_type_status"] == "verified"

    # observed, but never classified → stays data_insufficient, not "stock"
    assert rows["9999.TWSE"]["observed_on_market"] is True
    assert rows["9999.TWSE"]["instrument_type"] is None
    assert rows["9999.TWSE"]["instrument_type_status"] == "data_insufficient"

    # TPEx: observed market fact, but neither listing nor type is evidenced
    assert rows["5371.TPEX"]["observed_on_market"] is True
    assert rows["5371.TPEX"]["listing_metadata_status"] == "unknown"
    assert rows["5371.TPEX"]["instrument_type_status"] == "data_insufficient"


def test_listing_metadata_status_reflects_official_archive_availability() -> None:
    assert LISTING_METADATA_STATUS["TWSE"] == "verified"
    assert LISTING_METADATA_STATUS["TPEX"] == "unknown"


def test_absence_yields_no_row_and_no_listing_status(tmp_path: Path) -> None:
    """The suspended-but-listed case: no row, and no way to call it delisted."""
    universe = _universe(tmp_path)
    frame = universe.as_of(D1)
    assert "2358.TWSE" not in set(frame["market_symbol"].to_list())
    # There is no delisting concept in this layer at all.
    for forbidden in ("listing_status", "delisted", "delisting_date", "transition_type"):
        assert forbidden not in frame.columns


def test_classification_is_not_back_applied_to_earlier_sessions(tmp_path: Path) -> None:
    """A type established on D2 must not make D1 look verified."""
    census = ObservedUniverseStore(tmp_path / "observed_universe")
    classification = HistoricalClassificationStore(tmp_path / "cls")
    census.write("TWSE", D1, [_observation("2330", "TWSE", D1)])
    census.write("TWSE", D2, [_observation("2330", "TWSE", D2)])
    classification.write(D2, [_classification("2330", "stock", D2)])
    universe = PitUniverse(census=census, classification=classification)

    earlier = universe.as_of(D1).to_dicts()[0]
    assert earlier["instrument_type_status"] == "data_insufficient"
    later = universe.as_of(D2).to_dicts()[0]
    assert later["instrument_type_status"] == "verified"


def test_current_industry_is_never_carried_into_the_universe(tmp_path: Path) -> None:
    """Probe §4.3: the official industry label is current, not point-in-time."""
    frame = _universe(tmp_path).as_of(D1)
    assert "industry" not in frame.columns
    stored = HistoricalClassificationStore(tmp_path / "cls").read()
    assert stored["industry"].null_count() == stored.height
    assert set(stored["industry_status"].to_list()) == {"data_insufficient"}


# ── Policy layer ───────────────────────────────────────────────

def test_primary_tier_admits_only_verified_twse_stocks(tmp_path: Path) -> None:
    frame = _universe(tmp_path).as_of(D1)
    policy = EligibilityPolicy(min_warmup_sessions=0)
    gated = market_truth_gate(frame, policy)
    symbols = set(gated["market_symbol"].to_list())

    assert symbols == {"2330.TWSE"}
    assert "0050.TWSE" not in symbols, "ETF is classified but is not the stock universe"
    assert "9999.TWSE" not in symbols, "unclassified must fail closed"
    assert not any(s.endswith(".TPEX") for s in symbols)


def test_tpex_can_never_reach_the_primary_tier(tmp_path: Path) -> None:
    frame = _universe(tmp_path).as_of(D1)
    gated = market_truth_gate(frame, EligibilityPolicy(min_warmup_sessions=0))
    assert set(gated["exchange"].to_list()) == {"TWSE"}

    secondary = market_truth_gate(
        frame, EligibilityPolicy(tier=SECONDARY_OBSERVED, min_warmup_sessions=0))
    assert "5371.TPEX" in set(secondary["market_symbol"].to_list())


def test_tiers_are_labelled_and_only_primary_may_claim_oos() -> None:
    assert describe_tier(PRIMARY_VERIFIED) == "TWSE Verified OOS"
    assert describe_tier(SECONDARY_OBSERVED) == "TWSE + TPEx Observed Experimental"
    assert_oos_claimable(PRIMARY_VERIFIED)
    with pytest.raises(ValueError, match="may not carry an out-of-sample"):
        assert_oos_claimable(SECONDARY_OBSERVED)


def test_warmup_and_liquidity_are_policy_not_truth(tmp_path: Path) -> None:
    frame = _universe(tmp_path).as_of(D1)
    policy = EligibilityPolicy(min_warmup_sessions=20, min_adv20_twd=1_000_000.0)

    # market truth still contains the symbol …
    assert "2330.TWSE" in set(market_truth_gate(frame, policy)["market_symbol"].to_list())
    # … but policy excludes it without evidence of warm-up / liquidity
    assert eligible(frame, policy).is_empty()

    passed = eligible(frame, policy,
                      warmup_sessions={"2330.TWSE": 60},
                      adv20_twd={"2330.TWSE": 5_000_000.0})
    assert set(passed["market_symbol"].to_list()) == {"2330.TWSE"}


def test_missing_liquidity_evidence_fails_closed(tmp_path: Path) -> None:
    frame = _universe(tmp_path).as_of(D1)
    policy = EligibilityPolicy(min_warmup_sessions=0, min_adv20_twd=1.0)
    assert eligible(frame, policy, adv20_twd={}).is_empty()


def test_policy_is_versioned_and_inspectable() -> None:
    described = EligibilityPolicy().describe()
    assert described["version"] == "v1"
    assert described["tier_label"] == "TWSE Verified OOS"
    assert described["oos_claimable"] is True
    assert described["instrument_types"] == ["stock"]
    assert EligibilityPolicy(tier=SECONDARY_OBSERVED).describe()["oos_claimable"] is False


# ── Availability policy exception ──────────────────────────────

def test_corporate_action_exception_is_narrow() -> None:
    assert {("corporate_action", "market_mechanism_inferred")} == PIT_POLICY_EXCEPTIONS

    assert pit_usable("corporate_action", available_at=None,
                      availability_policy="market_mechanism_inferred")
    # the same policy string on an alpha feature must NOT inherit the exception
    for dataset in ("monthly_revenue", "financial_statement",
                    "institutional_flow", "news"):
        assert not pit_usable(dataset, available_at=None,
                              availability_policy="market_mechanism_inferred"), dataset
    # a verified timestamp always qualifies
    assert pit_usable("monthly_revenue", available_at="2025-01-10T08:00:00+08:00",
                      availability_policy="exact_timestamp")


def test_empty_inputs_do_not_explode(tmp_path: Path) -> None:
    universe = PitUniverse(
        census=ObservedUniverseStore(tmp_path / "a"),
        classification=HistoricalClassificationStore(tmp_path / "b"),
    )
    assert universe.as_of(D1).is_empty()
    assert eligible(pl.DataFrame(), EligibilityPolicy()).is_empty()

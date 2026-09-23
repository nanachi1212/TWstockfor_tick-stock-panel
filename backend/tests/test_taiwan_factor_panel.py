"""B2/B3 offline PIT, admission, storage and fold-isolation contracts."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.adjust import adjust_prices_for_presentation
from app.taiwan.corporate_actions import CorporateActionEvent, event_market_open
from app.taiwan.providers.taiwan_values import TAIPEI, market_close
from app.taiwan.quant.baseline import run_baseline_dry_run
from app.taiwan.quant.cross_section import industry_neutralize, transform_cross_section
from app.taiwan.quant.feature_manifest import (
    DatasetCapability,
    EligibilityResolution,
    FeatureManifest,
    FeatureSpec,
    FeatureVerdict,
)
from app.taiwan.quant.panel import build_factor_panel
from app.taiwan.quant.storage import FactorPanelStore, factor_meta
from app.taiwan.quant.training import TrainingMatrixResult, panel_training_matrix
from app.taiwan.quant.validation.folds import PurgedFold
from app.taiwan.quant_eligibility import EligibilityPolicy


def _days(n: int, start: date = date(2018, 1, 2)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _daily(n: int = 25, symbols: tuple[str, ...] = ("2330.TWSE",)) -> pl.DataFrame:
    rows = []
    for offset, symbol in enumerate(symbols):
        for i, day in enumerate(_days(n)):
            close = 100.0 + i + offset * 10
            rows.append({"date": day, "symbol": symbol, "open": close - 1,
                         "high": close + 2, "low": close - 2, "close": close,
                         "volume": 1000.0, "amount": 100000.0 + i})
    return pl.DataFrame(rows)


def _event(day: date, kind: str = "stock_dividend") -> CorporateActionEvent:
    return CorporateActionEvent(
        symbol="2330.TWSE", exchange="TWSE", effective_date=day,
        effective_at=event_market_open(day), event_type=kind,
        previous_close=100.0, reference_price=50.0, factor=0.5,
        cash_dividend=None, free_share_ratio=1.0 if kind == "stock_dividend" else None,
        reduction_ratio=None, source="TWT49U", source_url="https://www.twse.com.tw/",
        retrieved_at=datetime(2026, 9, 23, tzinfo=TAIPEI), status="verified",
        precision_method="official_reference_ratio",
    )


def _panel(n: int = 25, **kwargs):
    return build_factor_panel(_daily(n), events=kwargs.pop("events", ()),
                              policy_version=kwargs.pop("policy_version", "v1"),
                              universe_tier=kwargs.pop("universe_tier", "primary_verified"), **kwargs)


def _universe(n: int, symbols: tuple[str, ...]) -> pl.DataFrame:
    return pl.DataFrame([{"date": day, "market_symbol": symbol,
                          "observed_on_market": True, "exchange": "TWSE",
                          "instrument_type_status": "verified", "instrument_type": "stock"}
                         for day in _days(n) for symbol in symbols])


def _training(panel, universe):
    manifest = FeatureManifest("schema-1", (
        FeatureSpec("ma20", "corporate_action", "technical"),
        FeatureSpec("foreign_net_1d", "institutional", "chip"),
        FeatureSpec("industry_rank", "historical_industry", "industry"),
    ))
    capabilities = {
        "corporate_action": DatasetCapability("corporate_action", available_sessions=100,
            coverage_ratio=1, availability_policy="market_mechanism_inferred",
            revision_stability="append_only"),
        "institutional": DatasetCapability("institutional", available_sessions=0),
        "historical_industry": DatasetCapability("historical_industry", available_sessions=0),
    }
    return panel_training_matrix(panel, universe,
                                 EligibilityPolicy(version=panel.policy_version,
                                                   tier=panel.universe_tier,
                                                   min_warmup_sessions=0),
                                 manifest, capabilities)


def _admitted(frame: pl.DataFrame, features: tuple[str, ...]) -> TrainingMatrixResult:
    resolution = EligibilityResolution("s", tuple(
        FeatureVerdict(feature, "synthetic_verified", "technical", True)
        for feature in features
    ))
    return TrainingMatrixResult(frame, resolution, (), {})


def test_pit_panel_full_series_warmup_and_presentation_rejection():
    panel = _panel(25)
    assert panel.values.height == 25
    assert panel.values["date"].to_list() == _days(25)
    assert panel.values["ma20"][0] is None
    assert panel.values["ma20"][19] == pytest.approx(sum(100 + i for i in range(20)) / 20)
    assert panel.values["momentum_5d"][5] == pytest.approx(105 / 100 - 1)
    assert panel.values["adjustment_as_of"][0] == market_close(_days(25)[0]).isoformat()
    assert panel.coverage.filter((pl.col("factor") == "ma20") &
                                 (pl.col("date") == _days(25)[0]))["status"].item() == "insufficient_history"
    display = adjust_prices_for_presentation(_daily(), as_of=_days(25)[-1], events=())
    with pytest.raises(ValueError, match="presentation_only"):
        build_factor_panel(display.to_frame(), events=(), policy_version="v1",
                           universe_tier="primary_verified")


def test_future_event_and_future_market_rows_do_not_change_2018_factor_hash():
    before = _panel(25)
    future = _event(date(2024, 1, 2))
    later = _panel(25, events=[future])
    def digest(panel):
        return hashlib.sha256(panel.values.write_json().encode()).hexdigest()

    assert digest(before) == digest(later)
    assert before.coverage.equals(later.coverage)


def test_volume_share_change_blocks_relative_volume_but_amount_adv_remain():
    panel = _panel(25, events=[_event(_days(25)[10])])
    event_day = panel.values.filter(pl.col("date") == _days(25)[19]).to_dicts()[0]
    assert event_day["relative_volume"] is None
    assert event_day["amount"] == 100019
    assert event_day["adv20_twd"] == pytest.approx(sum(100000 + i for i in range(20)) / 20)
    assert panel.coverage.filter((pl.col("date") == _days(25)[19]) &
                                 (pl.col("factor") == "relative_volume"))["status"].item() == "data_insufficient"


def test_unavailable_chip_margin_and_industry_remain_null_with_status():
    legacy_inst = pl.DataFrame({"date": [_days(1)[0]], "symbol": ["2330.TWSE"],
                                "foreign_net": [100], "investment_trust_net": [20], "dealer_net": [10]})
    panel = _panel(2, institutional=legacy_inst)
    first = panel.values.to_dicts()[0]
    assert first["foreign_net_1d"] is None
    assert first["margin_balance"] is None
    assert first["industry_rank"] is None
    status = {(row["date"], row["factor"]): row["status"] for row in panel.coverage.to_dicts()}
    assert status[(_days(1)[0], "foreign_net_1d")] == "unavailable"
    assert status[(_days(1)[0], "industry_rank")] == "not_pit_safe"
    assert status[(_days(1)[0], "margin_balance")] == "unavailable"


def test_available_at_enables_chip_and_margin_without_future_leakage():
    days = _days(6)
    inst = pl.DataFrame([{"date": day, "symbol": "2330.TWSE", "foreign_net": i + 1,
                          "investment_trust_net": 2, "dealer_net": 3,
                          "available_at": market_close(day).isoformat()}
                         for i, day in enumerate(days)])
    margin = pl.DataFrame([{"date": day, "symbol": "2330.TWSE", "margin_balance": 100 + i,
                            "short_balance": 10 + i,
                            "available_at": market_close(day).isoformat()}
                           for i, day in enumerate(days)])
    panel = _panel(6, institutional=inst, margin=margin)
    last = panel.values.tail(1).to_dicts()[0]
    assert last["foreign_net_5d"] == 20
    assert last["institutional_sync_buy"] == 1
    assert last["margin_change_5d"] == 5
    assert last["short_margin_ratio"] == pytest.approx(15 / 105)
    future = inst.with_columns(pl.lit("2024-01-01T13:30:00+08:00").alias("available_at"))
    blocked = _panel(6, institutional=future)
    assert blocked.values["foreign_net_1d"].null_count() == 6


def test_market_relative_uses_as_of_index_rows_only():
    days = _days(6)
    market = pl.DataFrame([{"date": day, "close": 200.0 + i,
                            "available_at": market_close(day).isoformat()}
                           for i, day in enumerate(days)])
    first = _panel(6, market=market)
    last = first.values.tail(1).to_dicts()[0]
    assert last["stock_return_5d"] == pytest.approx(105 / 100 - 1)
    assert last["market_return_5d"] == pytest.approx(205 / 200 - 1)
    assert last["relative_to_market_5d"] == pytest.approx(105 / 100 - 205 / 200)
    future = pl.concat([market, pl.DataFrame({"date": [date(2024, 1, 2)],
                                              "close": [9999.0],
                                              "available_at": ["2024-01-02T13:30:00+08:00"]})])
    assert first.values.equals(_panel(6, market=future).values, null_equal=True)


def test_chip_coverage_growth_changes_training_eligibility():
    days = _days(6)
    inst = pl.DataFrame([{"date": day, "symbol": "2330.TWSE", "foreign_net": i,
                          "investment_trust_net": 1, "dealer_net": 1,
                          "available_at": market_close(day).isoformat()}
                         for i, day in enumerate(days)])
    panel = _panel(6, institutional=inst)
    manifest = FeatureManifest("s", (FeatureSpec("foreign_net_5d", "institutional", "chip",
                                                 required_history_sessions=5,
                                                 required_coverage_ratio=0.8),))
    policy = EligibilityPolicy(version="v1", tier="primary_verified", min_warmup_sessions=0)
    low = DatasetCapability("institutional", available_sessions=2, coverage_ratio=0.3,
                            has_verified_available_at=True)
    high = DatasetCapability("institutional", available_sessions=6, coverage_ratio=1.0,
                             has_verified_available_at=True)
    missing = panel_training_matrix(panel, _universe(6, ("2330.TWSE",)), policy,
                                    manifest, {"institutional": low})
    ready = panel_training_matrix(panel, _universe(6, ("2330.TWSE",)), policy,
                                  manifest, {"institutional": high})
    assert "foreign_net_5d" not in missing.matrix.columns
    assert "foreign_net_5d" in ready.matrix.columns
    assert ready.matrix["foreign_net_5d"].to_list()[-1] == 15


def test_training_filters_symbols_features_and_cross_section_population():
    panel = build_factor_panel(_daily(21, ("2330.TWSE", "2317.TWSE")), events=(),
                               policy_version="v1", universe_tier="primary_verified")
    universe = _universe(21, ("2330.TWSE", "2317.TWSE"))
    universe = universe.with_columns(
        pl.when(pl.col("market_symbol") == "2317.TWSE").then(pl.lit("data_insufficient"))
        .otherwise(pl.col("instrument_type_status")).alias("instrument_type_status"))
    result = _training(panel, universe)
    assert "2317.TWSE" in result.rejected_symbols
    assert set(result.matrix["symbol"].to_list()) == {"2330.TWSE"}
    assert "ma20" in result.matrix.columns
    assert "foreign_net_1d" not in result.matrix.columns
    assert "industry_rank" not in result.matrix.columns
    assert result.matrix["feature_schema_version"].to_list()[-1] == "schema-1"
    x = transform_cross_section(result, day=_days(21)[-1], factor="ma20",
                                policy_version="v1", universe_tier="primary_verified")
    assert x.eligible_count == 1
    assert x.values["rank_pct"].to_list() == [1.0]
    with pytest.raises(ValueError, match="not_pit_safe"):
        industry_neutralize()


def test_training_warmup_and_liquidity_are_date_specific():
    days = _days(4)
    symbol = "2330.TWSE"
    panel = _panel(4)
    universe = _universe(4, (symbol,))
    policy = EligibilityPolicy(min_warmup_sessions=2, min_adv20_twd=10)
    manifest = FeatureManifest("schema-1", ())
    warmup = {(day, symbol): i + 1 for i, day in enumerate(days)}
    liquidity = {(days[0], symbol): 100, (days[1], symbol): 1, (days[2], symbol): 100}
    before = panel_training_matrix(panel, universe, policy, manifest, {},
                                   warmup_sessions=warmup, adv20_twd=liquidity)
    # IPO warmup, low turnover, and missing turnover reject independent dates.
    assert before.matrix["date"].to_list() == [days[2]]
    warmup[(days[3], symbol)] = 1000000
    liquidity[(days[3], symbol)] = 1000000
    after = panel_training_matrix(panel, universe, policy, manifest, {},
                                  warmup_sessions=warmup, adv20_twd=liquidity)
    assert after.matrix["date"].to_list() == days[2:]
    assert after.matrix.filter(pl.col("date") < days[3]).equals(before.matrix)


@pytest.mark.parametrize("field", ["warmup_sessions", "adv20_twd"])
def test_training_rejects_symbol_only_policy_evidence(field):
    with pytest.raises(ValueError, match=r"requires \(date, symbol\) keys"):
        panel_training_matrix(_panel(2), _universe(2, ("2330.TWSE",)),
                              EligibilityPolicy(), FeatureManifest("s", ()), {},
                              **{field: {"2330.TWSE": 1000000}})


def test_cross_section_ties_versions_and_numeric_transforms():
    base = {"date": [_days(1)[0]] * 3, "symbol": ["B", "A", "C"],
            "ma20": [1.0, 1.0, 100.0], "policy_version": ["v1"] * 3,
            "universe_tier": ["secondary_observed"] * 3,
            "feature_schema_version": ["s"] * 3}
    frame = pl.DataFrame(base)
    admission = _admitted(frame, ("ma20",))
    out = transform_cross_section(admission, day=_days(1)[0], factor="ma20",
                                  policy_version="v1", universe_tier="secondary_observed",
                                  winsor_tail=0.25)
    rows = {row["symbol"]: row for row in out.values.to_dicts()}
    assert rows["A"]["rank_pct"] == pytest.approx(1 / 3)
    assert rows["B"]["rank_pct"] == pytest.approx(2 / 3)
    assert rows["C"]["winsorized"] == pytest.approx(50.5)
    assert rows["C"]["zscore"] > 0
    with pytest.raises(ValueError, match="policy"):
        transform_cross_section(admission, day=_days(1)[0], factor="ma20",
                                policy_version="v2", universe_tier="secondary_observed")
    with pytest.raises(TypeError, match="admitted"):
        transform_cross_section(frame, day=_days(1)[0], factor="ma20",
                                policy_version="v1", universe_tier="secondary_observed")


def test_storage_is_idempotent_atomic_and_preserves_nulls(tmp_path):
    first = _panel(2)
    store = FactorPanelStore(tmp_path / "factors")
    paths = store.save(first)
    assert paths == store.save(first)
    assert (tmp_path / "factors" / f"factor_version={first.factor_version}" / "_factor_meta.json").exists()
    values, coverage = store.read(first, _days(2)[0])
    assert values["ma20"][0] is None
    assert coverage.filter(pl.col("factor") == "ma20")["status"].item() == "insufficient_history"
    assert values["factor_version"][0] == first.factor_version
    assert values["policy_version"][0] == first.policy_version
    assert "training_requirement" in factor_meta()["factors"]["ma20"]
    second = _panel(2, policy_version="v2")
    assert store.save(second)[0] != paths[0]
    changed = replace(first, values=first.values.with_columns((pl.col("amount") + 1).alias("amount")))
    with pytest.raises(ValueError, match="immutable"):
        store.save(changed)


@pytest.mark.parametrize("legacy", [False, True])
def test_factor_metadata_versions_coexist_without_rewriting_old_partitions(tmp_path, monkeypatch, legacy):
    import app.taiwan.quant.storage as storage_module

    first = _panel(2)
    store = FactorPanelStore(tmp_path / "factors")
    paths = store.save(first)
    old_values = (paths[0] / "values.parquet").read_bytes()
    old_meta_path = paths[0].parents[2] / "_factor_meta.json"
    old_metadata = old_meta_path.read_bytes()
    if legacy:
        (store.root / "_factor_meta.json").write_bytes(old_metadata)
        old_meta_path.unlink()
    new_metadata = factor_meta()
    new_metadata["factors"]["ma5"]["formula"] = "new version formula"
    monkeypatch.setattr(storage_module, "factor_meta", lambda: new_metadata)
    second = _panel(2, factor_version="tw-factors-v2")
    new_paths = store.save(second)
    assert (new_paths[0].parents[2] / "_factor_meta.json").read_bytes() != old_metadata
    assert (paths[0] / "values.parquet").read_bytes() == old_values
    assert store.read(first, _days(2)[0])[0]["factor_version"][0] == first.factor_version
    with pytest.raises(ValueError, match="bump version"):
        store.save(first)
    if legacy:
        assert (store.root / "_factor_meta.json").read_bytes() == old_metadata
        assert not old_meta_path.exists()
        monkeypatch.setattr(storage_module, "factor_meta", factor_meta)
        assert store.save(first) == paths
    assert old_meta_path.read_bytes() == old_metadata


def test_metadata_publication_does_not_overwrite_a_competing_contract(tmp_path, monkeypatch):
    import app.taiwan.quant.storage as storage_module

    panel = _panel(1)
    store = FactorPanelStore(tmp_path)
    original_link = storage_module.os.link
    competing = b'{"different_contract": true}'

    def competing_publish(source, target):
        target.write_bytes(competing)
        original_link(source, target)

    monkeypatch.setattr(storage_module.os, "link", competing_publish)
    with pytest.raises(ValueError, match="bump version"):
        store.save(panel)
    target = store._partition(panel, _days(1)[0])
    assert not target.exists()
    assert (target.parents[2] / "_factor_meta.json").read_bytes() == competing


def test_storage_partition_publication_failure_leaves_no_partial_target(tmp_path, monkeypatch):
    import app.taiwan.quant.storage as storage_module

    panel = _panel(1)
    store = FactorPanelStore(tmp_path / "factors")
    original_rename = storage_module.os.rename

    def fail_publish(source, target):
        if str(target).endswith(f"date={_days(1)[0]}"):
            raise OSError("publication failure")
        return original_rename(source, target)

    monkeypatch.setattr(storage_module.os, "rename", fail_publish)
    with pytest.raises(OSError, match="publication failure"):
        store.save(panel)
    target = store._partition(panel, _days(1)[0])
    assert not target.exists()
    assert list(target.parent.glob(".factor_*")) == []


def test_baseline_fits_only_train_tunes_only_validation_and_never_publishes():
    days = _days(8)
    rows = []
    for day in days:
        for symbol, factor, label in (("A", 1.0, 0.01), ("B", 2.0, 0.02)):
            rows.append({"date": day, "symbol": symbol, "ma20": factor,
                         "industry_rank": factor * 100, "forward_return": label,
                         "factor_version": "fv", "policy_version": "v1",
                         "universe_tier": "secondary_observed", "feature_schema_version": "s"})
    matrix = pl.DataFrame(rows)
    admission = _admitted(matrix, ("ma20",))
    fold = PurgedFold(0, days[0], days[2], days[3], days[4], days[5], days[7], 0, 0, 0)
    first = run_baseline_dry_run(admission, fold, factor_groups={"ma20": "technical"})
    poisoned = matrix.with_columns(
        pl.when(pl.col("date") >= days[5]).then(-pl.col("forward_return") * 1000)
        .otherwise(pl.col("forward_return")).alias("forward_return"),
        pl.when(pl.col("date") >= days[5]).then(-pl.col("ma20") * 1000)
        .otherwise(pl.col("ma20")).alias("ma20"),
    )
    second = run_baseline_dry_run(_admitted(poisoned, ("ma20",)), fold,
                                  factor_groups={"ma20": "technical"})
    assert first.weights == second.weights
    assert first.validation_threshold == second.validation_threshold
    assert first.selected_features == ("ma20",)
    assert first.ranks["usage_scope"].to_list() == ["experimental_only"] * 6
    assert "forward_return" not in first.ranks.columns
    assert first.ranks["fold_index"].to_list() == [0] * 6
    assert first.ranks.equals(run_baseline_dry_run(admission, fold,
                              factor_groups={"ma20": "technical"}).ranks)
    assert run_baseline_dry_run(admission, fold, factor_groups={"ma20": "technical"},
                                primary_ready=True).usage_scope == "experimental_only"
    with pytest.raises(ValueError, match="disabled"):
        run_baseline_dry_run(_admitted(matrix, ("industry_rank",)), fold,
                             factor_groups={"industry_rank": "industry"})
    with pytest.raises(ValueError, match="rejected"):
        run_baseline_dry_run(admission, fold, factor_groups={"industry_rank": "technical"})
    with pytest.raises(TypeError, match="admitted"):
        run_baseline_dry_run(matrix, fold, factor_groups={"ma20": "technical"})

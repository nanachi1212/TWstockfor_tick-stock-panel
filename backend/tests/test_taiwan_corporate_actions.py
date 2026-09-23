"""A1 golden numbers + deterministic A6 storage/transport failure contracts."""
# ruff: noqa: RUF001 -- Fixtures retain official units.
import json
from dataclasses import replace
from datetime import date, datetime

import polars as pl
import pytest

from app.taiwan.corporate_actions import (
    PRICE_COLUMNS,
    PRICE_SUPPORT,
    CorporateActionStore,
    derive_factor,
)
from app.taiwan.providers.corporate_actions import (
    CorporateActionProvider,
    CorporateActionSourceError,
    parse_corporate_actions,
    parse_detail,
)
from app.taiwan.providers.taiwan_values import TAIPEI

RETRIEVED = datetime(2026, 9, 23, tzinfo=TAIPEI)


def parsed(source="TWT49U", **overrides):
    if source == "TWT49U":
        fields = {"資料日期": "114年03月18日", "股票代號": "2330",
                  "除權息前收盤價": "970.00", "除權息參考價": "965.49",
                  "減除股利參考價": "965.49", "權值+息值": "4.50002", "權/息": "息"}
    elif source == "exDailyQ":
        fields = {"除權息日期": "114/06/13", "代號": "TEST",
                  "除權息前收盤價": "151", "除權息參考價": "123.75",
                  "減除股利參考價": "123.75", "現金股利": "2.5",
                  "每仟股無償配股": "200", "權/息": "除權息"}
    elif source == "revivt":
        fields = {"恢復買賣日期": "1130205", "股票代號": "TEST",
                  "最後交易日之收盤價格": "100", "減資恢復買賣開始日參考價格": "200"}
    else:
        fields = {"恢復買賣日期": "100/01/25", "股票代號": "2412",
                  "停止買賣前收盤價格": "73.10", "恢復買賣參考價": "88.87"}
    fields.update(overrides)
    payload = {"stat": "OK", "fields": list(fields), "data": [list(fields.values())]}
    if source in {"exDailyQ", "revivt"}:
        payload = {"tables": [payload]}
    return parse_corporate_actions(payload, source=source, source_url="https://official.example/query",
                                   retrieved_at=RETRIEVED)[0]


def test_parser_does_not_derive_a_factor():
    event = parsed()
    assert event.factor is None
    assert event.status == "data_insufficient"


def test_audit_twse_high_precision_reference():
    event = derive_factor(parsed())
    assert event.status == "verified"
    assert event.reference_price == pytest.approx(965.49998)
    assert event.factor == pytest.approx(1 - 4.50002 / 970)
    assert event.factor != 965.49 / 970
    assert event.precision_method == "twse_rights_value_high_precision"
    assert json.loads(event.raw_fields)["減除股利參考價"] == "965.49"


def test_factor_derivation_is_idempotent():
    event = derive_factor(parsed())
    assert derive_factor(event) == event


def test_verified_factor_does_not_bypass_source_event_contract():
    event = replace(parsed(), event_type="capital_reduction")
    assert derive_factor(event).status == "data_insufficient"


def test_detail_uses_exact_numeric_units_and_rejects_duplicates():
    fields = ["股票代號", "(每股配發現金股利)除息", "A. 按普通股股東持股比例每千股無償配股"]
    with pytest.raises(CorporateActionSourceError):
        parse_detail({"fields": fields, "data": [["2330", "-", "0 股"]]}, "2330")
    with pytest.raises(CorporateActionSourceError):
        parse_detail({"fields": fields, "data": [["2330", "1 元／股", "0 股"]] * 2}, "2330")


@pytest.mark.parametrize("source,override,expected", [
    ("TWTAUU", {}, 88.87 / 73.1),
    ("TWTB8U", {"恢復買賣日期": "109/08/17", "股票代號": "8070",
                "停止買賣前收盤價格": "190", "恢復買賣參考價": "19"}, 0.1),
    ("TWTB8U", {"恢復買賣日期": "114/08/25", "股票代號": "2327",
                "停止買賣前收盤價格": "546", "恢復買賣參考價": "136.5"}, 0.25),
    ("revivt", {}, 2),
    ("exDailyQ", {}, 123.75 / 151),
])
def test_official_event_reference_paths(source, override, expected):
    event = derive_factor(parsed(source, **override))
    assert event.status == "verified"
    assert event.factor == pytest.approx(expected)
    assert event.reduction_ratio is None  # not reverse-engineered from prices


def test_twse_stock_dividend_audit_1773():
    event = derive_factor(parsed(**{"股票代號": "1773", "資料日期": "114年06月13日",
        "除權息前收盤價": "151", "除權息參考價": "123.75", "減除股利參考價": "123.75",
        "權值+息值": "27.25", "權/息": "權息"}))
    assert event.event_type == "stock_dividend"
    assert event.factor == pytest.approx((151 - 2.5) / (151 * 1.2))


def test_pure_cash_subscription_does_not_create_a_fake_return():
    event = parsed(**{"股票代號": "6658", "資料日期": "114年01月06日",
        "除權息前收盤價": "76.80", "除權息參考價": "75.66", "減除股利參考價": "76.80",
        "權值+息值": "1.135552", "權/息": "權"})
    assert derive_factor(event).status == "data_insufficient"
    detail = {"stat": "ok", "fields": ["股票代號", "(每股配發現金股利)除息",
              "A. 按普通股股東持股比例每千股無償配股"], "data": [["6658  ", "0 元／股", "0 股"]]}
    raw = json.loads(event.raw_fields) | parse_detail(detail, "6658")
    result = derive_factor(replace(event, raw_fields=json.dumps(raw)))
    assert result.status == "verified"
    assert result.factor == 1
    assert result.event_type == "cash_capital_increase"


@pytest.mark.parametrize("field,value", [
    ("除權息前收盤價", None), ("除權息前收盤價", "-"), ("除權息前收盤價", "0"),
    ("除權息前收盤價", "NaN"), ("減除股利參考價", "-"), ("權值+息值", "-"),
    ("權/息", "unknown"), ("權值+息值", "9999"), ("減除股利參考價", "1"),
])
def test_missing_malformed_unsupported_fails_closed(field, value):
    event = derive_factor(parsed(**{field: value}))
    assert event.status == "data_insufficient"
    assert event.factor is None


def test_tpex_recompute_mismatch_fails_closed():
    assert derive_factor(parsed("exDailyQ", **{"現金股利": "3"})).status == "data_insufficient"


def test_tpex_live_audited_3105_20250613_field_values():
    event = derive_factor(parsed("exDailyQ", **{
        "代號": "3105", "除權息前收盤價": "88.00", "除權息參考價": "87.00",
        "減除股利參考價": "87.00", "權/息": "除息",
        "現金股利": "1.00000000", "每仟股無償配股": "0.00000000"}))
    assert event.status == "verified"
    assert event.event_type == "cash_dividend"
    assert event.factor == pytest.approx(87 / 88)


@pytest.mark.parametrize("source,field", [("TWTAUU", "停止買賣前收盤價格"),
                                         ("TWTAUU", "恢復買賣參考價"),
                                         ("TWTB8U", "恢復買賣參考價"),
                                         ("revivt", "減資恢復買賣開始日參考價格")])
def test_unpriced_resume_event_has_no_factor(source, field):
    event = derive_factor(parsed(source, **{field: "-"}))
    assert event.status == "data_insufficient"
    assert event.factor is None


def test_tpex_par_change_is_still_insufficient():
    event = replace(parsed("revivt"), event_type="par_change")
    assert derive_factor(event).status == "data_insufficient"
    assert ("TPEX", "par_change") not in PRICE_SUPPORT


def test_price_matrix_has_no_volume_and_has_all_audited_ohlc():
    for exchange in ("TWSE", "TPEX"):
        for kind in ("cash_dividend", "stock_dividend", "capital_reduction"):
            assert PRICE_SUPPORT[exchange, kind] == frozenset(PRICE_COLUMNS)
    assert PRICE_SUPPORT["TWSE", "par_change"] == frozenset(PRICE_COLUMNS)


def test_effective_at_never_becomes_available_at():
    event = derive_factor(parsed())
    assert event.effective_at == datetime(2025, 3, 18, 9, tzinfo=TAIPEI)
    assert event.available_at is None
    assert event.availability_policy == "market_mechanism_inferred"
    with pytest.raises(ValueError, match="predictive"):
        replace(event, available_at=event.effective_at)


def test_schema_mismatch_rejects_source_batch():
    with pytest.raises(CorporateActionSourceError) as error:
        parse_corporate_actions({"fields": [], "data": []}, source="TWT49U",
                               source_url="x", retrieved_at=RETRIEVED)
    assert error.value.status == "provider_error"


def test_ambiguous_duplicate_rows_are_not_multiplied():
    event = parsed()
    raw = json.loads(event.raw_fields)
    changed = raw | {"除權息前收盤價": "980"}
    batch = {"fields": list(raw), "data": [list(raw.values()), list(changed.values())]}
    events = parse_corporate_actions(batch, source="TWT49U", source_url="x", retrieved_at=RETRIEVED)
    assert all(derive_factor(e).status == "data_insufficient" for e in events)
    assert all(e.revision_status == "conflict" for e in events)


def test_store_is_idempotent_preserves_revisions_and_atomic(tmp_path, monkeypatch):
    store = CorporateActionStore(tmp_path)
    event = derive_factor(parsed())
    assert store.save([event, event]) == 1
    assert store.save([replace(event, retrieved_at=RETRIEVED.replace(hour=1))]) == 1
    before = store.path.read_bytes()
    def denied(*args):
        raise PermissionError("simulated replacement failure")
    with monkeypatch.context() as m:
        m.setattr("app.taiwan.corporate_actions.os.replace", denied)
        with pytest.raises(PermissionError):
            store.save([event])
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    assert not store.path.with_suffix(".lock").exists()
    changed = derive_factor(parsed(**{"權值+息值": "4.50003"}))
    assert store.save([changed]) == 2
    assert all(e.status == "data_insufficient" and e.revision_status == "conflict" for e in store.read())


def test_old_event_schema_does_not_acquire_verified_status(tmp_path):
    store = CorporateActionStore(tmp_path)
    store.save([derive_factor(parsed())])
    pl.read_parquet(store.path).drop("precision_method").write_parquet(store.path)
    event = store.read()[0]
    assert event.status == "data_insufficient"
    assert "legacy_schema_missing" in event.reason


def test_store_respects_data_dir_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr("app.taiwan.data_root.taiwan_data_root", lambda: tmp_path / "taiwan")
    store = CorporateActionStore()
    assert store.path == tmp_path / "taiwan" / "adj_factor" / "events.parquet"
    store.save([derive_factor(parsed())])
    lock = store.path.with_suffix(".lock")
    lock.write_text("another writer")
    before = store.path.read_bytes()
    with pytest.raises(RuntimeError, match="locked"):
        store.save([])
    assert store.path.read_bytes() == before


def test_provider_only_fetches_detail_for_subscription_cases():
    cash = json.loads(parsed().raw_fields)
    subscription = cash | {"股票代號": "6658", "除權息前收盤價": "76.80",
        "除權息參考價": "75.66", "減除股利參考價": "76.80", "權/息": "權"}
    payload = {"fields": list(cash), "data": [list(cash.values()), list(subscription.values())]}
    detail = {"fields": ["股票代號", "(每股配發現金股利)除息", "A. 按普通股股東持股比例每千股無償配股"],
              "data": [["6658", "0 元／股", "0 股"]]}
    calls = []
    class Response:
        def __init__(self, data):
            self.content = json.dumps(data).encode()
        def raise_for_status(self):
            return None
    class Client:
        def get(self, url):
            calls.append(url)
            return Response(detail if "Detail" in url else payload)
    events = CorporateActionProvider(Client()).fetch("TWT49U", date(2025, 3, 18), date(2025, 3, 18))
    assert len(calls) == 2
    assert all(e.status == "verified" for e in events)

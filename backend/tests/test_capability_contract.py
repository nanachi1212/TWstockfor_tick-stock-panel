# ruff: noqa: RUF002, RUF003 -- Traditional Chinese prose uses full-width punctuation.
"""現行 capability contract 回歸測試（台股官方免 Key 模式）。

取代已廢棄的 tests/test_capability_augment.py。

原檔驗證的是 ``app.tickflow.policy._augment_custom_sources``：在 TickFlow API-Key
分檔時代，某資料集的當前 provider 非 tickflow 且宣告了該資料集時，才「補授」對應能力。
commit f8aef18 把整套 API-Key 分檔偵測連同該函式一起移除，改為台股官方免 Key 模式，
``detect_capabilities()`` 現在**無條件**授予全部能力（包含原本條件式補授的那四項）。
該函式已不存在，那 7 個測試因此無法 collect，且其描述的行為也已不存在。

本檔只驗證目前 production 真實行為，不引用任何已移除的 private function。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.tickflow.capabilities import Cap, CapabilityLimits, CapabilitySet
from app.tickflow.policy import detect_capabilities

#: 免 Key 模式下 detect_capabilities() 的完整契約。
#: 這是四個歷史上「條件式補授」的能力 (daily/adj_factor/minute/financial) 現在
#: 無條件可用的直接證據。
EXPECTED_BATCH_LIMITS: dict[Cap, int | None] = {
    Cap.QUOTE_BY_SYMBOL: None,
    Cap.QUOTE_BATCH: 100,
    Cap.QUOTE_POOL: None,
    Cap.KLINE_DAILY_BY_SYMBOL: None,
    Cap.KLINE_DAILY_BATCH: 100,
    Cap.KLINE_MINUTE_BY_SYMBOL: None,
    Cap.KLINE_MINUTE_BATCH: 100,
    Cap.INTRADAY: None,
    Cap.INTRADAY_BATCH: 100,
    Cap.DEPTH5: None,
    Cap.DEPTH5_BATCH: 100,
    Cap.WEBSOCKET: None,
    Cap.FINANCIAL: None,
    Cap.ADJ_FACTOR: None,
}


def test_official_mode_grants_the_full_capability_set() -> None:
    """免 Key 模式授予全部能力，且不對任何能力宣告 rpm。"""
    capset = detect_capabilities()
    assert set(capset.all()) == set(EXPECTED_BATCH_LIMITS)
    for cap, batch in EXPECTED_BATCH_LIMITS.items():
        limits = capset.limits(cap)
        assert limits is not None, cap
        assert limits.batch == batch, cap
        # rpm 一律 None：節流由 app/rate_limits.py 的 slot table 負責，
        # 不再由 capability 分檔決定。
        assert limits.rpm is None, cap


def test_previously_augmented_capabilities_are_now_unconditional() -> None:
    """daily / adj_factor / minute / financial 不再需要任何 custom-source 條件。"""
    capset = detect_capabilities()
    for cap in (Cap.KLINE_DAILY_BATCH, Cap.ADJ_FACTOR,
                Cap.KLINE_MINUTE_BATCH, Cap.FINANCIAL):
        assert capset.has(cap), cap


def test_capabilities_do_not_depend_on_data_provider_preferences(monkeypatch) -> None:
    """能力集與使用者選的資料源無關（增廣機制已移除的回歸保護）。"""
    from app.services import preferences

    baseline = detect_capabilities().to_dict()
    for getter in ("get_daily_data_provider", "get_adj_factor_provider",
                   "get_minute_data_provider", "get_financial_provider"):
        monkeypatch.setattr(preferences, getter, lambda: "mock_src")
    assert detect_capabilities().to_dict() == baseline

    # force=True 是舊探測快取的旗標，免 Key 模式下不得改變結果。
    assert detect_capabilities(force=True).to_dict() == baseline


def test_grant_never_overrides_an_existing_capability() -> None:
    """CapabilitySet.grant 的既有語意：已存在的能力與其限制不被覆蓋。"""
    capset = CapabilitySet()
    capset.grant(Cap.KLINE_MINUTE_BATCH, CapabilityLimits(rpm=30, batch=100))
    capset.grant(Cap.KLINE_MINUTE_BATCH, CapabilityLimits(rpm=999, batch=999))
    limits = capset.limits(Cap.KLINE_MINUTE_BATCH)
    assert limits is not None and limits.rpm == 30 and limits.batch == 100


def test_update_data_providers_refreshes_capability_snapshot(monkeypatch) -> None:
    """切換資料源後 app.state.capabilities 快照應刷新 (讀快取+無網路)。

    這是原 test_capability_augment.py 中唯一仍對應現行 production 行為的期望，
    逐字保留：app/api/settings.py::update_data_providers 目前仍執行
    ``request.app.state.capabilities = detect_capabilities()``。
    """
    from app.api import settings as settings_api

    monkeypatch.setattr("app.services.preferences.save", lambda upd: None)
    sentinel = CapabilitySet()
    monkeypatch.setattr(settings_api, "detect_capabilities", lambda: sentinel)

    mock_request = MagicMock()
    settings_api.update_data_providers(
        MagicMock(model_dump=lambda exclude_none: {"daily_data_provider": "mock_src"}),
        mock_request,
    )
    assert mock_request.app.state.capabilities is sentinel

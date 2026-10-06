from datetime import date
from types import SimpleNamespace

import polars as pl

from app.taiwan.enrichment.models import InstitutionalFlow, SourceMeta
from app.taiwan.institutional_margin_refresh import TaiwanInstitutionalRefreshService
from app.taiwan.institutional_store import TaiwanInstitutionalStore


def _adapter(symbol: str, source: str) -> SimpleNamespace:
    def parse_payload(_payload, trade_date, url):
        return [
            InstitutionalFlow(
                symbol=symbol,
                trade_date=trade_date,
                foreign_buy=10,
                foreign_sell=5,
                foreign_net=5,
                investment_trust_buy=2,
                investment_trust_sell=1,
                investment_trust_net=1,
                dealer_buy=3,
                dealer_sell=1,
                dealer_net=2,
                meta=SourceMeta(
                    source=source,
                    source_url=url,
                    fetched_at="2026-10-06T00:00:00",
                    trade_date=trade_date,
                    status="stale",
                    source_type="official_open_data",
                ),
            )
        ]

    return SimpleNamespace(
        build_url=lambda trade_date: f"mock://{source}/{trade_date}",
        parse_payload=parse_payload,
    )


def test_persisted_official_history_remains_valid_for_session_windows(tmp_path):
    store = TaiwanInstitutionalStore(tmp_path / "institutional")
    provider = SimpleNamespace(
        twse=_adapter("2330.TWSE", "twse:t86"),
        tpex=_adapter("8069.TPEX", "tpex:daily_trade"),
    )
    service = TaiwanInstitutionalRefreshService(store=store, provider=provider)
    service._fetch_json = lambda _url: {}

    result = service.refresh_dates(date(2026, 10, 1), date(2026, 10, 1))

    assert result["dates_fetched"] == 1
    assert set(store.read_all()["status"].to_list()) == {"official"}


def test_existing_complete_official_rows_are_normalized_on_read(tmp_path):
    store = TaiwanInstitutionalStore(tmp_path / "institutional")
    store.write_batch(
        pl.DataFrame(
            {
                "symbol": ["2330.TWSE", "9999.TWSE"],
                "date": [date(2026, 10, 1)] * 2,
                "foreign_net": [5, 5],
                "investment_trust_net": [1, 1],
                "dealer_net": [2, 2],
                "status": ["stale", "stale"],
                "source": ["twse:t86", "unknown"],
            }
        )
    )

    statuses = dict(store.read_all().select("symbol", "status").iter_rows())

    assert statuses == {"2330.TWSE": "official", "9999.TWSE": "stale"}

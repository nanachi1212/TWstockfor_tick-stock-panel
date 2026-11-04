"""Taiwan-only instrument search contract; retired caches must never be read."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.kline import router


class _ExplodingRepo:
    def __getattr__(self, name):
        raise AssertionError(f"search must not read legacy repository: {name}")


def _patch_security_master(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    fake = _FakeSecurityMaster(rows)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: fake)


class _FakeSecurityMaster:
    """最小台股证券主档桩: 直接按 symbol/code/name 子串匹配, 不依赖真实 adapter。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def search(self, q: str, limit: int = 20) -> list[dict]:
        keyword = q.strip().upper()
        hits = [
            row for row in self._rows
            if keyword in row["symbol"].upper()
            or keyword in row["code"].upper()
            or keyword in row["name"].upper()
        ]
        return hits[:limit]


_TAIWAN_ROWS = [
    {
        "symbol": "2330.TWSE", "code": "2330", "name": "台積電",
        "exchange": "TWSE", "instrument_type": "stock", "is_supported": True,
    },
    {
        "symbol": "6488.TPEX", "code": "6488", "name": "環球晶",
        "exchange": "TPEX", "instrument_type": "stock", "is_supported": True,
    },
    {
        "symbol": "0050.TWSE", "code": "0050", "name": "元大台灣50",
        "exchange": "TWSE", "instrument_type": "etf", "is_supported": True,
    },
    {
        # 未支援标的(如权证): 即使命中查询也不应出现在合并结果中。
        "symbol": "7999.TWSE", "code": "7999", "name": "測試權證",
        "exchange": "TWSE", "instrument_type": "warrant", "is_supported": False,
    },
]



@pytest.fixture
def client(monkeypatch):
    _patch_security_master(monkeypatch, _TAIWAN_ROWS)
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _ExplodingRepo()
    return TestClient(app)


@pytest.mark.parametrize("market", [None, "taiwan", "all"])
@pytest.mark.parametrize("query,expected", [
    ("2330", "2330.TWSE"), ("2330.TWSE", "2330.TWSE"),
    ("台積電", "2330.TWSE"), ("6488", "6488.TPEX"), ("0050", "0050.TWSE"),
])
def test_search_only_taiwan(client, market, query, expected):
    params = {"q": query}
    if market is not None:
        params["market"] = market
    response = client.get("/api/kline/instruments/search", params=params)
    assert response.status_code == 200
    assert [r["symbol"] for r in response.json()["results"]] == [expected]
    assert response.json()["results"][0]["market"] == "taiwan"


@pytest.mark.parametrize("market", ["ashare", "us"])
def test_removed_or_unknown_market_is_rejected(client, market):
    response = client.get("/api/kline/instruments/search", params={"q": "2330", "market": market})
    assert response.status_code == 422


@pytest.mark.parametrize("query", ["   ", "7999", "000001.SZ"])
def test_empty_unsupported_and_legacy_queries_return_empty(client, query):
    response = client.get("/api/kline/instruments/search", params={"q": query})
    assert response.json() == {"results": []}


def test_limit_is_respected(client):
    response = client.get("/api/kline/instruments/search", params={"q": "TWSE", "limit": 1})
    assert len(response.json()["results"]) == 1

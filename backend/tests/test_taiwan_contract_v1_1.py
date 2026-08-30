import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.taiwan.fundamentals import FundamentalRecord, latest_as_of
from app.taiwan.providers.taiwan_values import TAIPEI

FIXTURE_PATH = Path(__file__).parents[2] / "docs" / "taiwan_market_contract_v1_1.json"
FIXTURE_BYTES = FIXTURE_PATH.read_bytes()
FIXTURE = json.loads(FIXTURE_BYTES)
FIXTURE_SHA256 = "a76aee8a566d838c582d2281b0cdc18b8941ea97e2f4780ea6693775fd97ee28"


def _record(revision: dict) -> FundamentalRecord:
    return FundamentalRecord(
        symbol="2330.TWSE",
        dataset="financial_statement_revision",
        period_start="2026-01-01",
        period_end="2026-03-31",
        published_at=None,
        available_at=datetime.fromisoformat(revision["available_at"]),
        retrieved_at=datetime(2026, 5, 30, tzinfo=TAIPEI),
        revision=revision["revision_identity"],
        provider="official",
        source="contract-fixture",
        source_url="https://official.example/evidence",
        status="official",
        normalized_unit="TWD",
        raw_unit="thousand_TWD",
        values={"value": revision["value"]},
    )


def test_taiwan_market_contract_v1_1_strict_availability_and_revisions():
    assert FIXTURE["contract_version"] == "1.1.0"
    assert FIXTURE["extends"] == "1.0.0"
    boundary = FIXTURE["availability_boundary"]
    available_at = datetime.fromisoformat(boundary["available_at"])
    assert all(datetime.fromisoformat(value) <= available_at for value in boundary["strict_unavailable"])
    assert datetime.fromisoformat(boundary["first_available"]) > available_at

    records = [_record(revision) for revision in FIXTURE["revisions"]]
    for query in FIXTURE["revision_queries"]:
        selected = latest_as_of(records, "2330.TWSE", datetime.fromisoformat(query["query_at"]))
        assert selected is not None
        assert selected.revision == query["expected_revision"]
        assert selected.values["value"] == query["expected_value"]


def test_taiwan_market_contract_v1_1_evidence_and_share_concepts():
    insufficient = FIXTURE["insufficient_availability"]
    assert insufficient == {
        "availability_policy": "insufficient",
        "available_at": None,
        "historically_available": False,
    }
    exact = FIXTURE["exact_evidence"]
    assert exact["availability_policy"] == "exact_timestamp"
    assert exact["availability_evidence_identifier"] == "official-stable-id"
    shares = FIXTURE["share_capital"]
    assert shares["issued_shares"] != shares["float_shares"]
    assert shares["issued_equals_float"] is False
    assert shares["capital_implies_float"] is False
    assert hashlib.sha256(FIXTURE_BYTES).hexdigest() == FIXTURE_SHA256

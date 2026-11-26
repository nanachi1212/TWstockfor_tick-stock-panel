import json
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.taiwan.observed_universe import TPEX_SOURCE, ObservedUniverseStore
from scripts import backfill_census_fields as backfill

DAY = date(2026, 11, 25)


def _reports(day=DAY):
    return [dict(date=day, exchange="TPEX", source=TPEX_SOURCE, observed=True, raw_code=code,
                 open=1., high=2., low=.5, close=1.5, amount=300., volume=200.,
                 official_direction="up", next_upper_limit=1.65, next_lower_limit=1.35,
                 retrieved_at="2026-11-26T15:00:00+08:00") for code in ("8069", "1815")]


def _source(root, day=DAY):
    official = _reports(day)
    # Deliberate reverse order vs the official response, existing nullable field, custom columns and metadata.
    table = pa.Table.from_pylist([{k: v for k, v in r.items() if k not in backfill.FIELDS}
                                 for r in reversed(official)])
    table = table.append_column(pa.field("next_lower_limit", pa.float64(), metadata={b"field": b"old"}),
                                pa.array([None, 1.35]))
    table = table.append_column("custom", pa.array([321, 123], type=pa.int16()))
    table = table.replace_schema_metadata({b"legacy": b"preserve", b"taiwan_trading_day_evidence_v1":
        json.dumps({"date": day.isoformat(), "exchange": "TPEX", "status": "trading",
                    "evidence_source": TPEX_SOURCE, "reason": "valid_market_rows"}).encode()})
    original = ObservedUniverseStore(root).partition_path("TPEX", day)
    original.parent.mkdir(parents=True, exist_ok=True)
    with pq.ParquetWriter(original, table.schema) as writer:
        writer.add_key_value_metadata({b"footer-only": b"preserve too"})
        writer.write_table(table)
    return original, table, official


def test_cli_copy_checks_hash_keeps_fields_row_order_and_is_idempotent(tmp_path, monkeypatch, capsys):
    source, output = tmp_path / "source", tmp_path / "enriched"
    original, table, official = _source(source)
    source_hash = backfill._sha(original)
    monkeypatch.setattr(backfill.ObservedUniverseCensus, "fetch_tpex", lambda self, day: official)
    args = ["--source", str(source), "--output", str(output), "--date", str(DAY), "--exchange", "TPEX"]
    assert backfill.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    copy = output / original.relative_to(source)
    assert result["status"] == "copied"
    assert result["coverage"]["next_lower_limit"] == {"official": 2, "filled": 1, "missing": 0}
    assert source_hash == result["source_sha256"] == backfill._sha(original)
    assert result["output_sha256"] == backfill._sha(copy)
    stored = pq.ParquetFile(copy)
    augmented = stored.read()
    assert augmented["raw_code"].to_pylist() == ["1815", "8069"]
    for name in table.column_names:
        assert augmented.schema.field(name).equals(table.schema.field(name), check_metadata=True)
        if name != "next_lower_limit":
            assert augmented[name].equals(table[name])
    assert stored.schema_arrow.metadata == pq.ParquetFile(original).schema_arrow.metadata
    assert augmented.schema.metadata == pq.ParquetFile(original).read().schema.metadata
    assert stored.metadata.metadata[b"footer-only"] == b"preserve too"
    stored.close()
    stamp = copy.stat().st_mtime_ns
    official[0]["retrieved_at"] = "later"
    assert backfill.main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "unchanged"
    assert copy.stat().st_mtime_ns == stamp
    # A valid, newly revised direction must not replace a published copy.
    official[0]["official_direction"] = "down"
    assert backfill.main(args) == 1
    assert "existing copy or official evidence differs" in capsys.readouterr().out
    assert backfill._sha(copy) == result["output_sha256"]
    official[0]["official_direction"] = "up"
    copy.write_bytes(b"interrupted or externally changed")
    assert backfill.main(args) == 1
    assert copy.read_bytes() == b"interrupted or externally changed"
    # Even a matching sidecar hash does not certify a replacement with wrong fields.
    copy.write_bytes(original.read_bytes())
    audit_path = copy.with_name("audit.json")
    record = json.loads(audit_path.read_text("utf-8"))
    audit_path.write_text(json.dumps({**record, "output_sha256": backfill._sha(copy)}), "utf-8")
    assert backfill.main(args) == 1
    assert "differ from the verified observations" in capsys.readouterr().out


@pytest.mark.parametrize("problem", ["close", "date", "exchange", "source", "membership", "duplicate", "known_limit"])
def test_partition_conflicts_never_publish(tmp_path, problem):
    source, output = tmp_path / "source", tmp_path / "out"
    original, _, rows = _source(source)
    source_hash = backfill._sha(original)
    if problem == "membership":
        rows.pop()
    elif problem == "duplicate":
        rows.append(rows[0])
    elif problem == "known_limit":
        rows[0]["next_lower_limit"] = 1.34
    else:
        rows[0][problem] = "changed"
    with pytest.raises(ValueError):
        backfill._partition(source, output, "TPEX", DAY, lambda _: rows)
    assert backfill._sha(original) == source_hash
    assert not output.exists()


def test_partial_failure_can_resume_without_replacing_a_success(tmp_path, monkeypatch, capsys):
    source, output = tmp_path / "source", tmp_path / "out"
    later = date(2026, 11, 26)
    first, _, rows = _source(source)
    second, _, next_rows = _source(source, later)
    replies = {DAY: rows, later: []}
    monkeypatch.setattr(backfill.ObservedUniverseCensus, "fetch_tpex", lambda self, day: replies[day])
    args = ["--source", str(source), "--output", str(output), "--date", str(DAY), "--date", str(later),
            "--exchange", "TPEX"]
    assert backfill.main(args) == 1
    assert [json.loads(line)["status"] for line in capsys.readouterr().out.splitlines()] == ["copied", "failed"]
    first_copy, next_copy = [output / p.relative_to(source) for p in (first, second)]
    first_hash = backfill._sha(first_copy)
    assert not next_copy.exists()
    replies[later] = next_rows
    assert backfill.main(args) == 0
    assert [json.loads(line)["status"] for line in capsys.readouterr().out.splitlines()] == ["unchanged", "copied"]
    assert next_copy.is_file() and backfill._sha(first_copy) == first_hash


def test_output_must_be_independent_owned_copy(tmp_path):
    source, other, new = tmp_path / "source", tmp_path / "existing", tmp_path / "new"
    source.mkdir()
    other.mkdir()
    for target in (source, source / "sub", tmp_path, other):
        with pytest.raises(ValueError):
            backfill._output_directory(source, target)
    assert not list(other.iterdir())
    backfill._output_directory(source, new)
    with pytest.raises(ValueError):
        backfill._output_directory(other, new)


def test_empty_partition_keeps_its_exact_holiday_evidence(tmp_path):
    source, output = tmp_path / "source", tmp_path / "out"
    store = ObservedUniverseStore(source)
    store.write("TWSE", DAY, [], confirmed_non_trading_source="calendar:test")
    original = store.partition_path("TWSE", DAY)
    result = backfill._partition(source, output, "TWSE", DAY, lambda _: [])
    assert result["rows"] == 0 and result["retrieved_at"] is None
    assert result["source_sha256"] == result["output_sha256"]
    assert (output / original.relative_to(source)).read_bytes() == original.read_bytes()


@pytest.mark.parametrize("problem", ["wrong_day", "unknown_status", "source_missing"])
def test_footer_only_evidence_conflicts_never_publish(tmp_path, problem):
    source, output = tmp_path / "source", tmp_path / "out"
    store = ObservedUniverseStore(source)
    store.write("TWSE", DAY, [], confirmed_non_trading_source="calendar:test")
    original = store.partition_path("TWSE", DAY)
    with pq.ParquetFile(original) as reader:
        table = reader.read().replace_schema_metadata(None)
    evidence = {"date": DAY.isoformat(), "exchange": "TWSE", "status": "non_trading",
                "evidence_source": "calendar:test", "reason": "verified_holiday"}
    key, value = {"wrong_day": ("date", "2026-11-24"), "unknown_status": ("status", "unknown"),
                  "source_missing": ("evidence_source", "")}[problem]
    evidence[key] = value
    with pq.ParquetWriter(original, table.schema) as writer:
        writer.add_key_value_metadata({b"taiwan_trading_day_evidence_v1": json.dumps(evidence).encode()})
        writer.write_table(table)
    with pytest.raises(ValueError):
        backfill._partition(source, output, "TWSE", DAY, lambda _: [])
    assert not output.exists()


def test_missing_fields_remain_unknown_and_unrepresentable_older_type_is_rejected(tmp_path):
    source, output = tmp_path / "source", tmp_path / "out"
    original, _, rows = _source(source)
    rows[0]["next_upper_limit"] = None
    partial = backfill._partition(source, output, "TPEX", DAY, lambda _: rows)
    assert partial["coverage"]["next_upper_limit"] == {"official": 1, "filled": 1, "missing": 1}
    old = pq.ParquetFile(original).read().append_column("next_upper_limit", pa.nulls(2, pa.int16()))
    pq.write_table(old, original)
    with pytest.raises(ValueError, match="cannot represent official values"):
        backfill._partition(source, tmp_path / "other", "TPEX", DAY, lambda _: rows)

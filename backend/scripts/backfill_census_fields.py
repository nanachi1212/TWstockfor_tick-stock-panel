"""Backfill exchange comparison fields into a separate, immutable census copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from app.taiwan.observed_universe import (
    TPEX_SOURCE,
    TWSE_SOURCE,
    ObservedUniverseCensus,
    ObservedUniverseStore,
)

FIELDS = {"official_direction": pa.large_string(), "next_upper_limit": pa.float64(), "next_lower_limit": pa.float64()}
QUOTE = ("open", "high", "low", "close", "volume", "amount")
MARKER = ".census-field-copy.json"


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _output_directory(source: Path, output: Path) -> None:
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("source and output must be independent directories")
    if not source.is_dir():
        raise ValueError(f"census source does not exist: {source}")
    marker = {"version": 1, "source": str(source)}
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError:
        pass
    else:
        with (output / MARKER).open("x", encoding="utf-8") as owner:
            json.dump(marker, owner)
    if not (output / MARKER).is_file() or json.loads((output / MARKER).read_text("utf-8")) != marker:
        raise ValueError("output must be new or a census copy made from this source")


def _checked_table(table: pa.Table, official: list[dict], exchange: str, day: date):
    provider = TWSE_SOURCE if exchange == "TWSE" else TPEX_SOURCE
    columns = ["date", "exchange", "source", "observed", "raw_code", *QUOTE]
    missing = set(columns) - set(table.column_names)
    if missing:
        raise ValueError(f"missing source fields: {sorted(missing)}")
    old = table.select(columns).to_pylist()
    fresh = {r["raw_code"]: r for r in official}
    codes = [r["raw_code"] for r in old]
    if len(fresh) != len(official) or len(set(codes)) != len(old):
        raise ValueError("duplicate raw_code in source or official rows")
    if set(codes) != set(fresh):
        raise ValueError(f"membership changed: source-only={sorted(set(codes)-set(fresh))[:10]}, "
                         f"official-only={sorted(set(fresh)-set(codes))[:10]}")
    for row in old:
        code = row["raw_code"]
        if not code or any(row[f] != v for f, v in (
            ("date", day), ("exchange", exchange), ("source", provider), ("observed", True)
        )):
            raise ValueError(f"invalid stored date/exchange/provenance for {code}")
        for name in columns:
            if row[name] != fresh[code][name]:
                raise ValueError(f"{code} {name}: source {row[name]!r}, official {fresh[code][name]!r}")

    fields = list(FIELDS) if exchange == "TPEX" else ["official_direction"]
    coverage = {}
    for name in fields:
        existing = name in table.column_names
        previous = table[name].to_pylist() if existing else [None] * len(old)
        supplied = [fresh[c].get(name) for c in codes]
        for code, a, b in zip(codes, previous, supplied, strict=True):
            if a is not None and b is not None and a != b:
                raise ValueError(f"{code} {name}: source {a!r}, official {b!r}")
        values = [a if a is not None else b for a, b in zip(previous, supplied, strict=True)]
        coverage[name] = {"official": sum(v is not None for v in supplied),
                          "filled": sum(a is None and b is not None for a, b in zip(previous, supplied, strict=True)),
                          "missing": values.count(None)}
        if not old or (existing and values == previous):
            continue
        field = table.schema.field(name) if existing else pa.field(name, FIELDS[name])
        array = pa.array(values, type=field.type)
        if array.to_pylist() != values:
            raise ValueError(f"{name}: source type {field.type} cannot represent official values exactly")
        table = (table.set_column(table.schema.get_field_index(name), field, array)
                 if existing else table.append_column(field, array))
    # The digest identifies the normalized official observations, excluding retrieval timestamps.
    evidence = [[fresh[c].get(k) for k in [*columns[1:], *fields]] for c in sorted(codes)]
    digest = hashlib.sha256(json.dumps(evidence, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return table, coverage, digest


def _partition(source: Path, output: Path, exchange: str, day: date, fetch) -> dict:
    rel = Path(f"exchange={exchange}", f"date={day.isoformat()}")
    original, target = source / rel / "part.parquet", output / rel
    if original.resolve() != original or target.resolve() != target:
        raise ValueError("partition path is redirected outside its copy")
    content = original.read_bytes()
    before = hashlib.sha256(content).hexdigest()
    parquet = pq.ParquetFile(pa.BufferReader(content))
    # Arrow's table reader also exposes footer-only keys as schema metadata.
    # Keep the original Arrow schema and the Parquet footer as separate contracts.
    initial = parquet.read().replace_schema_metadata(parquet.schema_arrow.metadata)
    footer = parquet.metadata.metadata or {}
    marker = footer.get(b"taiwan_trading_day_evidence_v1")
    if marker is not None:
        identity = json.loads(marker)
        # Use the same footer and evidence contract as readers of the published copy.
        evidence = ObservedUniverseStore(source).day_evidence(exchange, day)
        if (identity.get("date") != day.isoformat() or identity.get("exchange") != exchange
                or (evidence.status == "trading") != bool(initial.num_rows)):
            raise ValueError("stored trading-day evidence conflicts with partition identity or row count")
    rows = fetch(day)
    enriched, coverage, digest = _checked_table(initial, rows, exchange, day)
    if rows and not any(c["official"] for c in coverage.values()):
        raise ValueError("official response supplied none of the requested market fields")
    if _sha(original) != before:
        raise ValueError("source partition changed during validation")
    audit = {"version": 1, "exchange": exchange, "date": day.isoformat(), "source_sha256": before,
             "provider": TWSE_SOURCE if exchange == "TWSE" else TPEX_SOURCE, "normalized_official_sha256": digest,
             "rows": len(rows), "coverage": coverage}
    extra = {k: v for k, v in footer.items() if k != b"ARROW:schema"}

    def verify_content(path: Path) -> None:
        with pq.ParquetFile(path) as check:
            footer = {k: v for k, v in (check.metadata.metadata or {}).items() if k != b"ARROW:schema"}
            if (footer != extra or not check.schema_arrow.equals(enriched.schema, check_metadata=True)
                    or not check.read().equals(enriched)):
                raise ValueError("Parquet contents or metadata differ from the verified observations")

    def verify_existing() -> dict:
        old = json.loads((target / "audit.json").read_text("utf-8"))
        if (any(old.get(k) != v for k, v in audit.items())
                or _sha(target / "part.parquet") != old.get("output_sha256")):
            raise ValueError("existing copy or official evidence differs; keep it and choose a new output")
        verify_content(target / "part.parquet")
        return {**old, "status": "unchanged"}

    if target.exists():
        return verify_existing()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        staged = Path(tmp) / "partition"
        staged.mkdir()
        path = staged / "part.parquet"
        if initial.equals(enriched, check_metadata=True):
            path.write_bytes(content)
        else:
            with pq.ParquetWriter(path, enriched.schema, compression="zstd") as writer:
                writer.add_key_value_metadata(extra)
                writer.write_table(enriched)
            verify_content(path)
        audit["output_sha256"] = _sha(path)
        audit["retrieved_at"] = rows[0]["retrieved_at"] if rows else None
        (staged / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False), "utf-8")
        try:
            staged.rename(target)
        except OSError:
            if not target.is_dir():
                raise
            # A concurrent invocation published while we prepared this partition.
            audit.pop("retrieved_at")
            audit.pop("output_sha256")
            return verify_existing()
    return {**audit, "status": "copied"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Existing observed_universe directory (read-only)")
    parser.add_argument("--output", type=Path, required=True, help="New standalone census directory or a previous copy")
    parser.add_argument("--date", type=date.fromisoformat, action="append", required=True, dest="dates")
    parser.add_argument("--exchange", choices=("TWSE", "TPEX"), action="append", help="Default: both exchanges")
    args = parser.parse_args(argv)
    source, output = args.source.resolve(), args.output.resolve()
    try:
        _output_directory(source, output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")
    provider = ObservedUniverseCensus(store=ObservedUniverseStore(output))
    failed = False
    try:
        for day in sorted(set(args.dates)):
            for exchange in dict.fromkeys(args.exchange or ("TWSE", "TPEX")):
                fetch = provider.fetch_twse if exchange == "TWSE" else provider.fetch_tpex
                try:
                    result = _partition(source, output, exchange, day, fetch)
                except Exception as exc:
                    result = {"date": day.isoformat(), "exchange": exchange, "status": "failed", "error": str(exc)}
                    failed = True
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        provider.close()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

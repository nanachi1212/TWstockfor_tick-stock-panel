"""Build and privacy-audit the deterministic public release seed."""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.release_privacy import (  # noqa: E402
    PrivacyAuditReport,
    assert_privacy_pass,
    audit_release_paths,
    write_privacy_report,
)
from app.release_seed import build_release_seed, verify_release_seed  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "taiwan")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "release-assets" / "release-seed" / "release-seed.zip",
    )
    parser.add_argument("--seed-version")
    parser.add_argument("--audit-report", type=Path)
    parser.add_argument("--audit-staging", type=Path)
    args = parser.parse_args()

    output, manifest = build_release_seed(
        args.source,
        args.output,
        seed_version=args.seed_version,
    )
    verify_release_seed(output)

    staging = args.audit_staging or output.parent / f"audit-staging-{manifest.seed_version}"
    if staging.exists():
        raise RuntimeError(f"audit staging already exists; refuse to merge stale files: {staging}")
    staging.mkdir(parents=True)
    with zipfile.ZipFile(output, "r") as archive:
        archive.extractall(staging)

    report = PrivacyAuditReport()
    audit_release_paths([staging], scope="seed", report=report)
    report_path = args.audit_report or output.parent / "release-privacy-audit.json"
    write_privacy_report(report, report_path)
    assert_privacy_pass(report)

    print(
        f"seed={output}\n"
        f"seed_version={manifest.seed_version}\n"
        f"data_as_of={manifest.data_as_of}\n"
        f"file_count={manifest.file_count}\n"
        f"uncompressed_bytes={manifest.total_size_bytes}\n"
        f"compressed_bytes={output.stat().st_size}\n"
        f"audit_staging={staging}\n"
        f"privacy_report={report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

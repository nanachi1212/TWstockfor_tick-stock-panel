"""Run the fail-closed privacy gate over seed and built/installed artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.release_privacy import (  # noqa: E402
    PrivacyAuditReport,
    assert_privacy_pass,
    audit_release_paths,
    write_privacy_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="append", type=Path, default=[])
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = PrivacyAuditReport()
    if args.seed:
        audit_release_paths(args.seed, scope="seed", report=report)
    if args.artifact:
        audit_release_paths(args.artifact, scope="artifact", report=report)
    write_privacy_report(report, args.output)
    print(args.output)
    print(json.dumps(report.as_public_dict(), ensure_ascii=False, indent=2))
    assert_privacy_pass(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

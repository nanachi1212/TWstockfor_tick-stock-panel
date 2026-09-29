"""Fetch the pinned public core bundle and turn it into a release seed."""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import urllib.request
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
from app.taiwan.bootstrap import (  # noqa: E402
    DOWNLOAD_URL,
    EXPECTED_SHA256,
    locate_taiwan_in_extracted,
    safe_extract_zip,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "release-assets" / "release-seed" / "release-seed.zip",
    )
    parser.add_argument("--audit-report", type=Path)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nanachi_pinned_seed_") as temp_name:
        temp = Path(temp_name)
        bundle = args.bundle.resolve() if args.bundle else temp / "pinned-core.zip"
        if args.bundle is None:
            urllib.request.urlretrieve(DOWNLOAD_URL, bundle)
        if _sha256(bundle).lower() != EXPECTED_SHA256.lower():
            raise RuntimeError("pinned public core bundle SHA256 mismatch")
        # The pinned public download predates the current bundle manifest format.
        # Its immutable SHA authenticates the input; safe extraction plus the
        # release-seed allowlist/schema/checksum pass below validates the output.
        extracted = temp / "extracted"
        safe_extract_zip(bundle, extracted)
        source = locate_taiwan_in_extracted(extracted)
        output, manifest = build_release_seed(source, args.output)
        verify_release_seed(output)

        staging = output.parent / f"audit-staging-{manifest.seed_version}"
        if staging.exists():
            raise RuntimeError(f"audit staging already exists: {staging}")
        staging.mkdir(parents=True)
        with zipfile.ZipFile(output) as archive:
            archive.extractall(staging)

        report = PrivacyAuditReport()
        audit_release_paths([staging], scope="seed", report=report)
        report_path = args.audit_report or output.parent / "release-privacy-audit.json"
        write_privacy_report(report, report_path)
        assert_privacy_pass(report)
        print(f"seed={output}\ndata_as_of={manifest.data_as_of}\naudit_staging={staging}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

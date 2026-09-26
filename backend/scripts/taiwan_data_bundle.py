"""CLI for Taiwan Market Data Bundle Packaging, Verification, and Installation.

Usage:
  # Package Core Bundle (security_master + daily):
  python -m scripts.taiwan_data_bundle --build-core [--out dist/nanachi-core.zip]

  # Package Historical Bundle (TWSE census + classification + evidence + corporate actions):
  python -m scripts.taiwan_data_bundle --build-historical [--out dist/nanachi-historical.zip]

  # Verify an existing bundle against its internal manifest and SHA-256 sums:
  python -m scripts.taiwan_data_bundle --verify dist/nanachi-core.zip

  # Install/extract a bundle into target data directory:
  python -m scripts.taiwan_data_bundle --install dist/nanachi-core.zip [--data-dir data/taiwan]

  # Inspect manifest of a bundle without extracting:
  python -m scripts.taiwan_data_bundle --inspect dist/nanachi-core.zip
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.taiwan.bundle import (
    build_core_bundle,
    build_historical_bundle,
    install_bundle,
    verify_bundle,
)
from app.taiwan.data_root import taiwan_data_root


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Taiwan Market Data Bundle CLI — Minimal Core & Historical Bundles"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build-core", action="store_true", help="Package Core Bundle (daily OHLCV + security_master)")
    group.add_argument("--build-historical", action="store_true", help="Package Historical Bundle (TWSE census + classification + evidence + corporate actions)")
    group.add_argument("--verify", metavar="ZIP_PATH", help="Verify integrity and SHA-256 checksums of a bundle archive")
    group.add_argument("--install", metavar="ZIP_PATH", help="Install and merge a bundle into the Taiwan data directory")
    group.add_argument("--inspect", metavar="ZIP_PATH", help="Inspect manifest and statistics of a bundle archive")

    parser.add_argument("--out", metavar="OUTPUT_ZIP", help="Destination path for built bundle archive")
    parser.add_argument("--source-dir", metavar="SRC_DIR", help="Source Taiwan data directory (defaults to current data/taiwan)")
    parser.add_argument("--data-dir", metavar="DEST_DIR", help="Target Taiwan data directory for installation")

    args = parser.parse_args()

    try:
        if args.build_core:
            src = Path(args.source_dir) if args.source_dir else taiwan_data_root()
            out = Path(args.out) if args.out else None
            print(f"Building Core Bundle from {src}...")
            zip_path, manifest = build_core_bundle(output_zip=out, source_taiwan_dir=src)
            print(f"[OK] Core Bundle created at: {zip_path}")
            print(f"  Package: {manifest.package_name}")
            print(f"  Through: {manifest.data_through}")
            print(f"  Files: {len(manifest.files)}")
            print(f"  SHA-256: {manifest.bundle_sha256}")

        elif args.build_historical:
            src = Path(args.source_dir) if args.source_dir else taiwan_data_root()
            out = Path(args.out) if args.out else None
            print(f"Building Historical Bundle from {src}...")
            zip_path, manifest = build_historical_bundle(output_zip=out, source_taiwan_dir=src)
            print(f"[OK] Historical Bundle created at: {zip_path}")
            print(f"  Package: {manifest.package_name}")
            print(f"  Through: {manifest.data_through}")
            print(f"  Files: {len(manifest.files)}")
            print(f"  SHA-256: {manifest.bundle_sha256}")

        elif args.verify:
            zip_path = Path(args.verify)
            print(f"Verifying {zip_path}...")
            manifest = verify_bundle(zip_path)
            print(f"[OK] Bundle verification PASSED: {manifest.package_name}")
            print(f"  Type: {manifest.bundle_type}")
            print(f"  Date range: {manifest.date_range.get('start')} ~ {manifest.date_range.get('end')}")
            print(f"  Files verified: {len(manifest.files)}")
            print(f"  Contains user data: {manifest.contains_user_data}")

        elif args.inspect:
            zip_path = Path(args.inspect)
            manifest = verify_bundle(zip_path)
            print(json.dumps(manifest.model_dump(), indent=2, ensure_ascii=False))

        elif args.install:
            zip_path = Path(args.install)
            dest = Path(args.data_dir) if args.data_dir else taiwan_data_root()
            print(f"Installing {zip_path} into {dest}...")
            result = install_bundle(zip_path, target_taiwan_dir=dest, verify_checksums=True)
            print("[OK] Installation complete:")
            print(f"  Bundle: {result['package_name']} ({result['bundle_type']})")
            print(f"  Data through: {result['data_through']}")
            print(f"  Files installed: {result['installed_files']}")
            print(f"  Target: {result['target_directory']}")

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

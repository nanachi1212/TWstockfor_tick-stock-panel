"""Install the previous public bundle into an empty data dir and fetch newer official sessions.

Used by .github/workflows/data-bundle.yml; build_release_seed.py then packs and audits the result.
Only public official sources are fetched (no FinMind, no user data).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise SystemExit(f"data dir must be empty: {data_dir}")
    # settings.data_dir is read at import time.
    os.environ["DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(ROOT / "backend"))

    from app.release_seed import install_release_seed
    from app.taiwan.daily_update import TaiwanDailyUpdateService
    from app.taiwan.universe import refresh_security_master

    installed = install_release_seed(args.bundle, data_dir)
    if installed.status != "installed":
        raise SystemExit(f"bundle install failed: {installed.status} {installed.error}")
    print(f"previous_data_as_of={installed.data_as_of}")

    # Before fetching: daily rows of stocks listed after the previous bundle are kept
    # only if the security master already knows them.
    print(f"security_master={refresh_security_master(max_age_hours=0)}")
    result = TaiwanDailyUpdateService().run_update(refresh_daily=True)
    print(f"update overall={result.overall_status} daily={result.daily.status} "
          f"inst={result.institutional.status} margin={result.margin.status}")
    if result.overall_status != "success":
        # A partial bundle would advance data_as_of while a dataset is missing; the next run retries.
        raise SystemExit(f"update incomplete ({result.overall_status}); not publishing a partial bundle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

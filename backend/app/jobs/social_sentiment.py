"""Run the daily PTT/Dcard social sentiment aggregation."""
from __future__ import annotations

import argparse
import logging

from app.taiwan.social_sentiment import SocialSentimentService


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 PTT Stock/Dcard 並產生台股社群聲量排行榜")
    parser.add_argument("--hours", type=int, default=24, help="回溯小時數，預設 24")
    parser.add_argument("--pages", type=int, default=3, help="每個來源的輕量頁面上限")
    parser.add_argument("--no-ai", action="store_true", help="只抓取與聚合，不呼叫 AI")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        payload = SocialSentimentService().run(
            window_hours=args.hours,
            max_pages=args.pages,
            run_ai=not args.no_ai,
        )
    except Exception:  # noqa: BLE001 - CLI must return a useful non-zero code
        logging.getLogger(__name__).exception("social sentiment job failed")
        return 2

    source_status = ", ".join(f"{name}={data['status']}" for name, data in payload["sources"].items())
    print(
        f"social sentiment: sources[{source_status}], symbols={payload['identified_symbols']}, "
        f"ai={payload['ai']['status']}, output=data/social_sentiment"
    )
    return 0 if any(data["status"] != "unavailable" for data in payload["sources"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())

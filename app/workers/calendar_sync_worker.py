"""Run once for a manual refresh, or every six hours as a managed worker."""

import argparse
from datetime import UTC, datetime
import json
import logging
import time

from app.db.database import SessionLocal
from app.services.calendar_sync_service import sync_calendar


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=21600)
    args = parser.parse_args()
    if args.interval < 300:
        parser.error("Minimum interval is 300 seconds.")
    while True:
        try:
            with SessionLocal() as db:
                result = sync_calendar(db, args.year or datetime.now(UTC).year)
            print(json.dumps(result), flush=True)
            if args.once and result["status"] in {"FAILED", "PARTIAL"}:
                raise SystemExit(1)
        except Exception:
            logging.exception("Calendar sync failed")
            if args.once:
                raise
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

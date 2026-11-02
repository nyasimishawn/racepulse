"""Refresh live calendar/Fantasy once, or every 15 minutes as a worker."""

import argparse
from datetime import UTC, datetime
import json
import logging
import time

from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.calendar import CalendarSyncState
from app.services.calendar_sync_service import sync_calendar
from app.services.fantasy_live_sync_service import sync_live_fantasy


def run_sync(db, year):
    lock = None
    if db is not None and db.bind.dialect.name == "postgresql":
        lock = db.bind.connect()
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"),
                            {"key": 910000 + year}).scalar():
            lock.close()
            return {"status": "BUSY"}
    try:
        return _run_sync(db, year)
    finally:
        if lock is not None:
            try:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"),
                             {"key": 910000 + year})
            finally:
                lock.close()


def _run_sync(db, year):
    result = sync_calendar(db, year)
    if result["status"] in {"FAILED", "BUSY"} or db is None:
        return result
    live = sync_live_fantasy(db, year)
    result["live_fantasy"] = live
    if live["status"] == "PARTIAL":
        result["status"] = "PARTIAL"
    state = db.get(CalendarSyncState, year)
    if state:
        state.result = result
        db.commit()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=900)
    args = parser.parse_args()
    if args.interval < 300:
        parser.error("Minimum interval is 300 seconds.")
    while True:
        try:
            with SessionLocal() as db:
                result = run_sync(db, args.year or datetime.now(UTC).year)
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

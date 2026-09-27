"""Import race laps needed for fastest-lap and speed-trap Fantasy picks."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.database import SessionLocal
from app.models.durable_job import DurableJob, DurableJobStatus
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.weekend_download import WeekendDownload
from app.services.lap_service import LapImportService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--through-round", type=int)
    parser.add_argument("--include-active", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    failures = []
    try:
        statement = select(WeekendDownload).where(
            WeekendDownload.year == args.year
        ).order_by(WeekendDownload.round_number)
        if args.through_round is not None:
            statement = statement.where(
                WeekendDownload.round_number <= args.through_round
            )
        for weekend in db.scalars(statement).all():
            job = db.get(DurableJob, weekend.durable_job_id)
            if (not args.include_active and job is not None
                    and job.status == DurableJobStatus.RUNNING):
                print(f"DEFER {weekend.year} {weekend.round_number}", flush=True)
                continue
            race = db.scalar(
                select(RaceSession)
                .join(Meeting, Meeting.id == RaceSession.meeting_id)
                .where(
                    Meeting.source == "FASTF1",
                    Meeting.year == weekend.year,
                    Meeting.name == weekend.event_name,
                    RaceSession.session_identifier == "R",
                )
            )
            if race is None:
                failures.append(weekend.round_number)
                print(f"MISSING {weekend.year} {weekend.round_number} R",
                      flush=True)
                continue
            count = db.scalar(select(func.count(Lap.id)).where(
                Lap.race_session_id == race.id
            ))
            if count:
                print(f"SKIP {weekend.year} {weekend.round_number} {count} laps",
                      flush=True)
                continue
            try:
                written, skipped = LapImportService(db).import_laps(race.id)
                print(
                    f"OK {weekend.year} {weekend.round_number} "
                    f"{written} laps, {skipped} skipped", flush=True,
                )
            except Exception as error:
                db.rollback()
                failures.append(weekend.round_number)
                print(
                    f"FAILED {weekend.year} {weekend.round_number}: "
                    f"{type(error).__name__}", flush=True,
                )
        if failures:
            print(f"Race laps need retry: {failures}", flush=True)
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

"""Import session classifications early while full weekend jobs run.

Example: ``python scripts/import_fantasy_results.py --year 2026 --through-round 13``.
The normal durable weekend jobs remain responsible for laps and telemetry.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.durable_job import DurableJob, DurableJobStatus
from app.models.import_job import DataSource, ImportJob
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.weekend_download import WeekendDownload
from app.services.session_import_service import SessionImportService


def already_imported(db, weekend: WeekendDownload, identifier: str) -> bool:
    return db.scalar(
        select(SessionResult.id)
        .join(RaceSession, RaceSession.id == SessionResult.race_session_id)
        .join(Meeting, Meeting.id == RaceSession.meeting_id)
        .where(
            Meeting.source == DataSource.FASTF1.value,
            Meeting.year == weekend.year,
            Meeting.name == weekend.event_name,
            RaceSession.session_identifier == identifier,
        )
        .limit(1)
    ) is not None


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
            durable_job = (
                db.get(DurableJob, weekend.durable_job_id)
                if weekend.durable_job_id else None
            )
            if (not args.include_active and durable_job is not None
                    and durable_job.status == DurableJobStatus.RUNNING):
                print(
                    f"DEFER {weekend.year} {weekend.round_number}: "
                    "full import is running", flush=True,
                )
                continue
            for session in weekend.sessions:
                identifier = session["identifier"]
                if already_imported(db, weekend, identifier):
                    print(f"SKIP {weekend.year} {weekend.round_number} {identifier}",
                          flush=True)
                    continue
                job = ImportJob(
                    source=DataSource.FASTF1,
                    year=weekend.year,
                    event_name=weekend.event_name,
                    session_type=session["name"],
                )
                db.add(job)
                db.commit()
                try:
                    imported = SessionImportService(db).run(job.id)
                    print(
                        f"OK {weekend.year} {weekend.round_number} {identifier} "
                        f"{imported.imported_session_id}", flush=True,
                    )
                except Exception as error:
                    db.rollback()
                    failures.append((weekend.round_number, identifier))
                    print(
                        f"FAILED {weekend.year} {weekend.round_number} "
                        f"{identifier}: {type(error).__name__}",
                        flush=True,
                    )
        if failures:
            print(f"{len(failures)} sessions need retry: {failures}", flush=True)
            raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

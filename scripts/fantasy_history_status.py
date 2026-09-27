"""Show progress of the 2025–Monza 2026 weekend imports."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select

from app.db.database import SessionLocal
from app.models.durable_job import DurableJob
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.weekend_download import WeekendDownload


def main() -> None:
    db = SessionLocal()
    try:
        weekends = db.scalars(
            select(WeekendDownload).where(
                (WeekendDownload.year == 2025)
                | ((WeekendDownload.year == 2026)
                   & (WeekendDownload.round_number <= 13))
            ).order_by(WeekendDownload.year, WeekendDownload.round_number)
        ).all()
        for year in (2025, 2026):
            chosen = [w for w in weekends if w.year == year]
            complete = 0
            results = 0
            sessions = 0
            race_laps = 0
            for weekend in chosen:
                job = db.get(DurableJob, weekend.durable_job_id)
                complete += int(job is not None and job.status.value == "COMPLETED")
                for item in weekend.sessions:
                    sessions += 1
                    has_result = db.scalar(
                        select(SessionResult.id)
                        .join(RaceSession,
                              RaceSession.id == SessionResult.race_session_id)
                        .join(Meeting, Meeting.id == RaceSession.meeting_id)
                        .where(
                            Meeting.source == "FASTF1",
                            Meeting.year == weekend.year,
                            Meeting.name == weekend.event_name,
                            RaceSession.session_identifier == item["identifier"],
                        ).limit(1)
                    )
                    results += int(has_result is not None)
                laps = db.scalar(
                    select(func.count(Lap.id))
                    .join(RaceSession, RaceSession.id == Lap.race_session_id)
                    .join(Meeting, Meeting.id == RaceSession.meeting_id)
                    .where(
                        Meeting.source == "FASTF1",
                        Meeting.year == weekend.year,
                        Meeting.name == weekend.event_name,
                        RaceSession.session_identifier == "R",
                    )
                )
                race_laps += int(bool(laps))
            print(
                f"{year}: {len(chosen)} weekends, "
                f"{results}/{sessions} sessions with results, "
                f"{race_laps}/{len(chosen)} races with laps, "
                f"{complete}/{len(chosen)} full imports complete"
            )
        active = [
            (weekend, db.get(DurableJob, weekend.durable_job_id))
            for weekend in weekends if weekend.durable_job_id
        ]
        for weekend, job in active:
            if job.status.value in {"RUNNING", "RETRY_WAIT", "FAILED"}:
                print(
                    f"  {weekend.year} round {weekend.round_number:02d} "
                    f"{job.status.value} {job.progress_percentage}%"
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()

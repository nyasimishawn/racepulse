from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.services.durable_job_service import JobCancellationRequested
from app.services.lap_service import LapImportService


def test_bulk_lap_import_propagates_cooperative_cancellation(
    db_session: Session,
) -> None:
    meeting = Meeting(source="TEST", year=2026, name="Queue Test")
    db_session.add(meeting)
    db_session.flush()
    race_session = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=datetime.now(UTC),
    )
    db_session.add(race_session)
    db_session.commit()

    def request_cancel() -> None:
        raise JobCancellationRequested("cancel requested")

    service = LapImportService(
        db_session,
        cancellation_check=request_cancel,
    )

    with pytest.raises(JobCancellationRequested):
        service.import_laps(race_session.id)

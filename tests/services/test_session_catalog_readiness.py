from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.services.session_query_service import SessionQueryService


def test_calendar_sessions_are_not_advertised_as_recordings(db_session):
    meeting = Meeting(source="FASTF1", year=2026, name="Singapore Grand Prix")
    db_session.add(meeting)
    db_session.flush()
    scheduled = RaceSession(
        meeting_id=meeting.id, name="Race", session_identifier="R",
        session_type="Race", source_metadata={"calendar_only": True},
    )
    recorded = RaceSession(
        meeting_id=meeting.id, name="Sprint", session_identifier="S",
        session_type="Sprint", source_metadata={},
    )
    db_session.add_all([scheduled, recorded])
    db_session.flush()
    service = SessionQueryService(db_session)
    assert [row.id for row in service.list_sessions()] == [recorded.id]
    assert service.get_session_detail(scheduled.id).imported is False
    assert service.get_session_detail(recorded.id).imported is True
    assert {row.id: row.imported for row in service.list_meeting_sessions(meeting.id)} == {
        scheduled.id: False, recorded.id: True,
    }

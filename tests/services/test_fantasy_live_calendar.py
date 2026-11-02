from datetime import UTC, datetime, timedelta
import json

import httpx
from sqlalchemy import func, select

from app.models.calendar import CalendarRevision, CalendarWeekend
from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.user_profile import UserProfile
from app.services.calendar_service import CalendarService
from app.services.calendar_sync_service import BASE, sync_calendar
from app.services.fantasy_live_sync_service import (
    parse_results, sync_live_fantasy,
)
from app.services.fantasy_service import FantasyService


URL = BASE + "/en/racing/2026/singapore"
NOW = datetime(2026, 10, 10, 10, 5, tzinfo=UTC)


def flight(value):
    return '<script>self.__next_f.push([1,' + json.dumps(
        "1:" + json.dumps(value) + "\n"
    ) + '])</script>'


def schedule_page():
    dates = {
        "Practice 1": "2026-10-09T08:30:00Z",
        "Sprint Qualifying": "2026-10-09T12:30:00Z",
        "Sprint": "2026-10-10T09:00:00Z",
        "Qualifying": "2026-10-10T13:00:00Z",
        "Race": "2026-10-11T12:00:00Z",
    }
    event = {"@id": URL, "subEvent": [
        {"name": name + " - Singapore Grand Prix", "startDate": start,
         "eventStatus": "https://schema.org/EventScheduled"}
        for name, start in dates.items()
    ]}
    return (
        '<script type="application/ld+json">' + json.dumps(event) + '</script>'
        + flight({
            "meetingNumber": "17", "meetingName": "Singapore Grand Prix",
            "meetingCountryName": "Singapore", "meetingLocation": "Marina Bay",
            "meetingSessions": [
                {"description": name,
                 "state": "completed" if name not in {"Qualifying", "Race"}
                 else "live" if name == "Qualifying" else "scheduled"}
                for name in dates
            ],
        })
        + '<a href="/en/results/2026/races/1290/italy/race-result">Teaser</a>'
        + '<a href="/en/results/2026/races/1296/singapore/practice/1">Results</a>'
    )


def results_page():
    return flight({"rows": [
        {"racingNumber": str(index), "driverFirstName": "Driver",
         "driverLastName": str(index), "driverTLA": f"D{index}",
         "driverReference": f"driver{index}", "teamName": f"Team {index % 2}",
         "teamKey": str(index % 2), "positionValue": str(index),
         "q1": f"1:3{index}.100", "q2": f"1:3{index}.000",
         "q3": f"1:2{index}.500", "version": "Provisional"}
        for index in range(1, 7)
    ]})


def mock_client(requested):
    def handler(request):
        requested.append(request.url.path)
        if request.url.path == "/en/racing/2026":
            return httpx.Response(200, text='<a href="/en/racing/2026/singapore">GP</a>')
        if request.url.path == "/en/racing/2026/singapore":
            return httpx.Response(200, text=schedule_page())
        return httpx.Response(200, text=results_page())
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_live_calendar_creates_stable_fantasy_before_telemetry(db_session):
    requested = []
    with mock_client(requested) as client:
        assert sync_calendar(db_session, 2026, client)["created"] == 1
        weekend = db_session.scalar(select(CalendarWeekend))
        overview = CalendarService(db_session).overview(weekend)
        assert weekend.meeting_id is not None
        assert all(session["race_session_id"] for session in overview["sessions"])
        assert not any(session["imported"] for session in overview["sessions"])
        ids = [session["race_session_id"] for session in overview["sessions"]]
        assert sync_calendar(db_session, 2026, client)["unchanged"] == 1
        assert [session["race_session_id"] for session in
                CalendarService(db_session).overview(weekend)["sessions"]] == ids
        assert db_session.scalar(select(func.count()).select_from(CalendarRevision)) == 1
    demo = Meeting(source="SIM", year=2026, name="Retained test weekend")
    db_session.add(demo)
    db_session.flush()
    db_session.add(RaceSession(
        meeting_id=demo.id, name="Race", session_type="Race",
        session_identifier="R", started_at=NOW + timedelta(days=20),
    ))
    db_session.commit()
    races = FantasyService(db_session, now=NOW).list_races()
    assert races[0].is_current and races[0].round_number == 17
    assert not races[1].is_current
    context = FantasyService(db_session, now=NOW)._load_race_context(races[0].race_session_id)
    assert FantasyService(db_session, now=NOW)._candidate_options(context) == ([], [])


def test_live_results_roster_and_provisional_scoring(db_session):
    requested = []
    with mock_client(requested) as client:
        sync_calendar(db_session, 2026, client)
        live = sync_live_fantasy(db_session, 2026, client=client, now=NOW)
        assert live["results_updated"] == 3
        assert live["roster_size"] == 6
        # An in-progress qualifying table is deliberately ignored.
        assert not any(path.endswith("/qualifying") for path in requested)
        assert not any(path.endswith("/race-result") for path in requested)
        race = next(session for session in db_session.scalars(select(RaceSession)).all()
                    if session.session_identifier == "R")
        assert (race.source_metadata or {}).get("calendar_only") is True
        profile = UserProfile(keycloak_subject="live-fantasy-test")
        db_session.add(profile)
        db_session.commit()
        service = FantasyService(db_session, now=NOW)
        prediction = service.get_prediction(profile.id, race.id)
        assert len(prediction.drivers) == 6
        assert len(prediction.teams) == 2
        keys = {question.key: question for question in prediction.questions}
        assert keys["SPRINT_P1"].state == "LOCKED"
        assert keys["SQ3_FASTEST"].state == "LOCKED"
        assert keys["Q3_FASTEST"].state == "OPEN"
        assert keys["RACE_P1"].state == "OPEN"
        scoring = service.score_available_questions(race.id)
        assert "SPRINT_P1" in scoring.resolved_question_keys
        assert "RACE_P1" in scoring.pending_question_keys
        updated = service.get_prediction(profile.id, race.id)
        sprint = next(q for q in updated.questions if q.key == "SPRINT_P1")
        assert "PROVISIONAL_RESULT" in sprint.resolution.data_quality_flags
        assert sprint.resolution.source_reference.endswith("/sprint-results")
        assert db_session.scalar(select(func.count()).select_from(Driver)) == 6
        assert db_session.scalar(select(func.count()).select_from(SessionResult)) == 18
        # Repeat refreshes preserve identities, results, and Fantasy targets.
        sync_live_fantasy(db_session, 2026, client=client, now=NOW)
        assert db_session.scalar(select(func.count()).select_from(Driver)) == 6
        assert db_session.scalar(select(func.count()).select_from(SessionResult)) == 18


def test_current_weekend_stays_on_race_day_until_confirmed_complete(db_session):
    with mock_client([]) as client:
        sync_calendar(db_session, 2026, client)
    weekend = db_session.scalar(select(CalendarWeekend))
    sunday = datetime(2026, 10, 11, 14, tzinfo=UTC)
    assert CalendarService(db_session, now=sunday).next()["id"] == weekend.id
    weekend.schedule = {**weekend.schedule, "status": "COMPLETED"}
    db_session.commit()
    assert CalendarService(db_session, now=sunday).next() is None


def test_official_parser_rejects_empty_entry_lists():
    import pytest

    with pytest.raises(ValueError):
        parse_results(flight({"rows": [{"racingNumber": "1"}]}))


def test_live_sync_reuses_unambiguous_imported_venue(db_session):
    meeting = Meeting(
        source="FASTF1", year=2026, name="Provider Singapore alias",
        country_name="Singapore", location="Marina Bay",
        event_date=datetime(2026, 10, 11, tzinfo=UTC),
    )
    db_session.add(meeting)
    db_session.flush()
    race = RaceSession(
        meeting_id=meeting.id, name="Race", session_identifier="R",
        session_type="Race", started_at=datetime(2026, 10, 11, 12, tzinfo=UTC),
    )
    db_session.add(race)
    db_session.commit()
    with mock_client([]) as client:
        sync_calendar(db_session, 2026, client)
    weekend = db_session.scalar(select(CalendarWeekend))
    assert weekend.meeting_id == meeting.id
    assert db_session.scalar(select(func.count()).select_from(Meeting)) == 1
    assert next(session for session in CalendarService(db_session).overview(
        weekend
    )["sessions"] if session["identifier"] == "R")["race_session_id"] == race.id
    assert race.source_metadata["calendar"]["results_url"] == (
        BASE + "/en/results/2026/races/1296/singapore/race-result"
    )


def test_official_archive_table_parses_driver_and_qualifying_times():
    def span(class_name, text):
        return ["$", "span", None, {"className": class_name, "children": text}]

    table = {"tableId": "results-table", "cols": [
        {"label": name} for name in ("Pos.", "No.", "Driver", "Team", "Q1", "Q2", "Q3")
    ], "rows": [], "footnote": "Provisional results."}
    for index in range(1, 7):
        table["rows"].append([
            {"content": [str(index)]}, {"content": [str(index)]},
            {"content": ["$", "span", None, {"children": [
                span("max-lg:hidden", "Driver"), span("max-md:hidden", str(index)),
                span("md:hidden", f"D{index}"),
            ]}]},
            {"content": span("", "Team 1")},
            {"content": "1:33.477"}, {"content": "1:32.701"}, {"content": "1:31.156"},
        ])
    parsed = parse_results(flight(table))
    assert len(parsed) == 6
    assert parsed[0]["driverFirstName"] == "Driver"
    assert parsed[0]["driverLastName"] == "1"
    assert parsed[0]["q3"] == "1:31.156"
    assert parsed[0]["version"] == "Provisional"

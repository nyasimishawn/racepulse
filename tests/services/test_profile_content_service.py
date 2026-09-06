from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.models.user_profile import UserProfile
from app.schemas.editorial import (
    EditorialPublicationStatus,
    EditorialUpdateCreateRequest,
    EditorialUpdateType,
)
from app.schemas.profiles import (
    ContentConfidence,
    DriverProfileUpsertRequest,
    ProfileNotableMomentCreateRequest,
)
from app.services.profile_content_service import (
    EditorialAssociationError,
    EditorialContentService,
    ProfileContentService,
)


def _seed_history(
    db_session: Session,
) -> tuple[Driver, Team, RaceSession, Meeting, UserProfile]:
    team = Team(
        source="FASTF1",
        source_identifier="team-1",
        name="Example Racing",
    )
    driver = Driver(
        source="FASTF1",
        source_identifier="driver-1",
        driver_number="1",
        full_name="Example Driver",
        country_code="EX",
    )
    editor = UserProfile(
        keycloak_subject="editor-subject",
        display_name="Editor",
    )
    meeting_2025 = Meeting(
        source="FASTF1",
        year=2025,
        name="Example 2025 Grand Prix",
        event_date=datetime(2025, 3, 1, tzinfo=UTC),
    )
    meeting_2026 = Meeting(
        source="FASTF1",
        year=2026,
        name="Example 2026 Grand Prix",
        event_date=datetime(2026, 3, 1, tzinfo=UTC),
    )
    db_session.add_all([team, driver, editor, meeting_2025, meeting_2026])
    db_session.flush()

    race_2025 = RaceSession(
        meeting_id=meeting_2025.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=datetime(2025, 3, 1, 14, tzinfo=UTC),
    )
    race_2026 = RaceSession(
        meeting_id=meeting_2026.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=datetime(2026, 3, 1, 14, tzinfo=UTC),
    )
    db_session.add_all([race_2025, race_2026])
    db_session.flush()

    db_session.add_all(
        [
            SessionResult(
                race_session_id=race_2025.id,
                driver_id=driver.id,
                team_id=team.id,
                position=1,
                points=Decimal("25"),
            ),
            SessionResult(
                race_session_id=race_2026.id,
                driver_id=driver.id,
                team_id=team.id,
                position=2,
                points=Decimal("18"),
            ),
            Lap(
                race_session_id=race_2025.id,
                driver_id=driver.id,
                lap_number=1,
                position=1,
            ),
            Lap(
                race_session_id=race_2025.id,
                driver_id=driver.id,
                lap_number=2,
                position=1,
            ),
            Lap(
                race_session_id=race_2026.id,
                driver_id=driver.id,
                lap_number=1,
                position=1,
            ),
            Lap(
                race_session_id=race_2026.id,
                driver_id=driver.id,
                lap_number=2,
                position=2,
            ),
        ]
    )
    db_session.commit()
    return driver, team, race_2026, meeting_2026, editor


def test_driver_and_team_history_are_derived_with_coverage_disclaimer(
    db_session: Session,
) -> None:
    driver, team, _race, _meeting, _editor = _seed_history(db_session)
    service = ProfileContentService(db_session)

    driver_history = service.get_driver_history(
        driver_id=driver.id,
        year=None,
    )
    team_history = service.get_team_history(team_id=team.id, year=2026)

    assert driver_history.totals.race_entries == 2
    assert driver_history.totals.wins == 1
    assert driver_history.totals.podiums == 2
    assert driver_history.totals.points == Decimal("43")
    assert driver_history.totals.recorded_laps_led == 3
    assert [season.year for season in driver_history.seasons] == [2026, 2025]
    assert driver_history.coverage.source_names == ["FASTF1"]
    assert driver_history.coverage.complete_historical_coverage is False
    assert "not complete career totals" in driver_history.coverage.disclaimer

    assert team_history.filter_year == 2026
    assert team_history.totals.race_entries == 1
    assert team_history.totals.podiums == 1
    assert team_history.totals.recorded_laps_led == 1


def test_profile_moments_and_editorial_updates_remain_separate(
    db_session: Session,
) -> None:
    driver, _team, race, meeting, editor = _seed_history(db_session)
    profiles = ProfileContentService(db_session)

    profile = profiles.upsert_driver_profile(
        driver_id=driver.id,
        editor_profile_id=editor.id,
        payload=DriverProfileUpsertRequest(
            biography="A sourced curated biography.",
            source_url="https://www.fia.com/example-driver",
            publisher="FIA",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            confidence=ContentConfidence.HIGH,
        ),
    )
    moment = profiles.create_driver_notable_moment(
        driver_id=driver.id,
        editor_profile_id=editor.id,
        payload=ProfileNotableMomentCreateRequest(
            title="Championship milestone",
            description="A separately curated notable moment.",
            occurred_at=datetime(2026, 3, 1, tzinfo=UTC),
            source_url="https://www.fia.com/example-milestone",
            publisher="FIA",
            published_at=datetime(2026, 3, 2, tzinfo=UTC),
            confidence=ContentConfidence.HIGH,
        ),
    )

    editorial = EditorialContentService(db_session)
    published = editorial.create_update(
        editor_profile_id=editor.id,
        payload=EditorialUpdateCreateRequest(
            update_type=EditorialUpdateType.GRID_DROP,
            publication_status=EditorialPublicationStatus.PUBLISHED,
            title="Grid-drop decision",
            body="A curated editorial summary with a source.",
            meeting_id=meeting.id,
            race_session_id=race.id,
            driver_id=driver.id,
            source_url="https://www.fia.com/example-grid-drop",
            publisher="FIA",
            published_at=datetime(2026, 3, 2, tzinfo=UTC),
            confidence=ContentConfidence.HIGH,
        ),
    )
    editorial.create_update(
        editor_profile_id=editor.id,
        payload=EditorialUpdateCreateRequest(
            update_type=EditorialUpdateType.TEAM_UPDATE,
            title="Draft team update",
            body="Not visible to public readers yet.",
            source_url="https://www.example.com/draft",
            publisher="Example Team",
            published_at=datetime(2026, 3, 2, tzinfo=UTC),
        ),
    )

    profile_after = profiles.get_driver_profile(driver.id)
    public_updates = editorial.list_public_updates(
        update_type=None,
        meeting_id=None,
        race_session_id=None,
        driver_id=None,
        team_id=None,
        limit=20,
    )

    assert profile.profile_id is not None
    assert profile_after.notable_moments[0].id == moment.id
    assert public_updates == [published]


def test_editorial_update_rejects_a_session_from_another_meeting(
    db_session: Session,
) -> None:
    _driver, _team, race, _meeting, editor = _seed_history(db_session)
    unrelated_meeting = Meeting(
        source="FASTF1",
        year=2026,
        name="Unrelated Grand Prix",
    )
    db_session.add(unrelated_meeting)
    db_session.commit()

    with pytest.raises(EditorialAssociationError):
        EditorialContentService(db_session).create_update(
            editor_profile_id=editor.id,
            payload=EditorialUpdateCreateRequest(
                update_type=EditorialUpdateType.FIA_UPDATE,
                title="Mismatched reference",
                body="This must be rejected.",
                meeting_id=unrelated_meeting.id,
                race_session_id=race.id,
                source_url="https://www.fia.com/example-reference",
                publisher="FIA",
                published_at=datetime(2026, 3, 2, tzinfo=UTC),
            ),
        )

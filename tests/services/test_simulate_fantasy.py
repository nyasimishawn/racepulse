from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.models.fantasy import FantasyQuestionResolution, FantasyPredictionPickScore
from app.models.session_result import SessionResult
from app.models.user_profile import UserProfile
from app.schemas.fantasy import FantasyQuestionSaveRequest
from app.services.fantasy_service import (
    FantasyPredictionLockedError,
    FantasyService,
)
from scripts.seed_fantasy_simulation import seed_fantasy_simulation
from scripts.simulate_fantasy import release_due_results, start_simulation


def test_timed_simulation_locks_and_scores_known_results(db_session):
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    started = start_simulation(db_session, now=now, minutes=2)
    race_id = UUID(started["race_session_id"])
    locks = {
        key: datetime.fromisoformat(value)
        for key, value in started["locks_at"].items()
    }
    assert locks == {
        "FP1": now + timedelta(minutes=2),
        "Q": now + timedelta(minutes=4),
        "R": now + timedelta(minutes=6),
    }

    player = UserProfile(
        keycloak_subject="timed-simulation-player",
        display_name="Timed Simulation Player",
    )
    db_session.add(player)
    db_session.commit()
    fantasy = FantasyService(db_session, now=now)
    prediction = fantasy.get_prediction(player.id, race_id)
    drivers = {driver.display_name: driver.id for driver in prediction.drivers}
    for key, name in (
        ("FP1_P1", "Apex One"),
        ("Q1_FASTEST", "Apex One"),
        ("RACE_P1", "Vector One"),
    ):
        fantasy.save_question(
            user_profile_id=player.id,
            race_session_id=race_id,
            question_key=key,
            payload=FantasyQuestionSaveRequest(driver_id=drivers[name]),
        )

    early = release_due_results(
        db_session, race_id, now=now + timedelta(minutes=1)
    )
    assert early["released_sessions"] == []
    assert db_session.scalar(
        select(func.count()).select_from(FantasyQuestionResolution)
    ) == 0
    assert db_session.scalar(
        select(func.count())
        .select_from(SessionResult)
        .where(SessionResult.position.is_not(None))
    ) == 0

    with pytest.raises(FantasyPredictionLockedError):
        FantasyService(db_session, now=locks["FP1"]).save_question(
            user_profile_id=player.id,
            race_session_id=race_id,
            question_key="FP1_P1",
            payload=FantasyQuestionSaveRequest(driver_id=drivers["Orbit One"]),
        )
    after_fp1 = release_due_results(db_session, race_id, now=locks["FP1"])
    assert after_fp1["released_sessions"] == ["FP1"]
    assert after_fp1["resolved_question_keys"] == ["FP1_P1"]
    assert FantasyService(db_session, now=locks["FP1"]).get_prediction(
        player.id, race_id
    ).total_points == 2

    after_race = release_due_results(db_session, race_id, now=locks["R"])
    assert after_race["released_sessions"] == ["Q", "R"]
    assert after_race["pending_question_keys"] == []
    assert after_race["finalized_group_count"] == 0
    scored = FantasyService(db_session, now=locks["R"]).get_prediction(
        player.id, race_id
    )
    assert scored.total_points == 15
    assert db_session.scalar(
        select(func.count()).select_from(FantasyQuestionResolution)
    ) == 11
    assert db_session.scalar(
        select(func.count()).select_from(FantasyPredictionPickScore)
    ) == 3
    again = release_due_results(db_session, race_id, now=locks["R"])
    assert again["released_sessions"] == []
    assert db_session.scalar(
        select(func.count()).select_from(FantasyPredictionPickScore)
    ) == 3


def test_release_rejects_a_nonaccelerated_weekend(db_session):
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    race_id = seed_fantasy_simulation(db_session, now=now)
    with pytest.raises(ValueError, match="not an accelerated"):
        release_due_results(db_session, race_id, now=now + timedelta(days=5))
    assert db_session.scalar(
        select(func.count())
        .select_from(SessionResult)
        .where(SessionResult.position.is_not(None))
    ) == 0

from datetime import UTC, datetime, timedelta

from app.models.meeting import Meeting
from app.models.user_profile import UserProfile
from app.schemas.fantasy import FantasyQuestionSaveRequest
from app.services.fantasy_service import FantasyService
from scripts.seed_fantasy_simulation import seed_fantasy_simulation


def test_seed_creates_reusable_open_weekend_with_pick_options(db_session):
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    race_id = seed_fantasy_simulation(db_session, now=now)
    assert seed_fantasy_simulation(db_session, now=now) == race_id

    meeting = db_session.query(Meeting).filter_by(source="FANTASY_SIM").one()
    assert meeting.year == 2026

    player = UserProfile(
        keycloak_subject="fantasy-simulation-player",
        display_name="Simulation Player",
    )
    db_session.add(player)
    db_session.commit()

    fantasy = FantasyService(db_session, now=now)
    prediction = fantasy.get_prediction(player.id, race_id)
    assert len(prediction.questions) == 11
    assert all(question.state == "OPEN" for question in prediction.questions)
    assert len(prediction.drivers) == 6
    assert len(prediction.teams) == 3

    saved = fantasy.save_question(
        user_profile_id=player.id,
        race_session_id=race_id,
        question_key="RACE_P1",
        payload=FantasyQuestionSaveRequest(driver_id=prediction.drivers[0].id),
    )
    assert saved.question.is_answered is True

    later = now + timedelta(days=3)
    fresh_race_id = seed_fantasy_simulation(db_session, now=later)
    assert fresh_race_id != race_id
    fresh_prediction = FantasyService(db_session, now=later).get_prediction(
        player.id, fresh_race_id
    )
    assert all(question.state == "OPEN" for question in fresh_prediction.questions)

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.fantasy import FantasyGroupWeekendEligibility, FantasyWeekendQuestion
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.models.user_profile import UserProfile
from app.schemas.fantasy import (
    FantasyGroupCreateRequest,
    FantasyGroupJoinRequest,
    FantasyPredictionAnswerInput,
    FantasyPredictionUpdateRequest,
    FantasyQuestionResolutionRequest,
    FantasyQuestionSaveRequest,
)
from app.services.fantasy_service import (
    FantasyPredictionLockedError,
    FantasyService,
)


def seed_fantasy_weekend(db_session: Session) -> dict[str, object]:
    base = datetime.now(UTC).replace(microsecond=0)

    meeting = Meeting(
        source="TEST",
        year=2026,
        name="Fantasy Test Grand Prix",
    )
    db_session.add(meeting)
    db_session.flush()

    fp1 = RaceSession(
        meeting_id=meeting.id,
        name="Practice 1",
        session_identifier="FP1",
        session_type="Practice",
        started_at=base + timedelta(days=1),
    )
    qualifying = RaceSession(
        meeting_id=meeting.id,
        name="Qualifying",
        session_identifier="Q",
        session_type="Qualifying",
        started_at=base + timedelta(days=2),
    )
    race = RaceSession(
        meeting_id=meeting.id,
        name="Race",
        session_identifier="R",
        session_type="Race",
        started_at=base + timedelta(days=3),
    )

    team_a = Team(
        source="TEST",
        source_identifier="team-a",
        name="Team A",
    )
    team_b = Team(
        source="TEST",
        source_identifier="team-b",
        name="Team B",
    )

    driver_a = Driver(
        source="TEST",
        source_identifier="driver-a",
        driver_number="1",
        full_name="Driver A",
    )
    driver_b = Driver(
        source="TEST",
        source_identifier="driver-b",
        driver_number="2",
        full_name="Driver B",
    )
    driver_c = Driver(
        source="TEST",
        source_identifier="driver-c",
        driver_number="3",
        full_name="Driver C",
    )

    player_one = UserProfile(
        keycloak_subject="fantasy-player-one",
        display_name="Player One",
    )
    player_two = UserProfile(
        keycloak_subject="fantasy-player-two",
        display_name="Player Two",
    )

    db_session.add_all(
        [
            fp1,
            qualifying,
            race,
            team_a,
            team_b,
            driver_a,
            driver_b,
            driver_c,
            player_one,
            player_two,
        ]
    )
    db_session.flush()

    db_session.add_all(
        [
            SessionResult(
                race_session_id=fp1.id,
                driver_id=driver_a.id,
                team_id=team_a.id,
                position=1,
            ),
            SessionResult(
                race_session_id=qualifying.id,
                driver_id=driver_a.id,
                team_id=team_a.id,
                position=1,
                q1_time_ms=90_000,
                q2_time_ms=90_100,
                q3_time_ms=90_200,
            ),
            SessionResult(
                race_session_id=qualifying.id,
                driver_id=driver_b.id,
                team_id=team_b.id,
                position=2,
                q1_time_ms=90_300,
                q2_time_ms=90_400,
                q3_time_ms=90_500,
            ),
            SessionResult(
                race_session_id=race.id,
                driver_id=driver_a.id,
                team_id=team_a.id,
                position=1,
            ),
            SessionResult(
                race_session_id=race.id,
                driver_id=driver_b.id,
                team_id=team_b.id,
                position=2,
            ),
            SessionResult(
                race_session_id=race.id,
                driver_id=driver_c.id,
                team_id=team_a.id,
                position=3,
            ),
            Lap(
                race_session_id=race.id,
                driver_id=driver_a.id,
                lap_number=1,
                lap_time_ms=90_000,
                speed_st=Decimal("330.0"),
                is_accurate=True,
                deleted=False,
                fastf1_generated=False,
            ),
            Lap(
                race_session_id=race.id,
                driver_id=driver_c.id,
                lap_number=1,
                lap_time_ms=89_900,
                speed_st=Decimal("320.0"),
                is_accurate=True,
                deleted=False,
                fastf1_generated=False,
            ),
        ]
    )
    db_session.commit()

    return {
        "base": base,
        "fp1": fp1,
        "race": race,
        "team_a": team_a,
        "driver_a": driver_a,
        "driver_b": driver_b,
        "driver_c": driver_c,
        "player_one": player_one,
        "player_two": player_two,
    }


def test_fantasy_scores_and_finalizes_group_weekend(
    db_session: Session,
) -> None:
    weekend = seed_fantasy_weekend(db_session)
    base = weekend["base"]
    race = weekend["race"]
    team_a = weekend["team_a"]
    driver_a = weekend["driver_a"]
    driver_b = weekend["driver_b"]
    driver_c = weekend["driver_c"]
    player_one = weekend["player_one"]
    player_two = weekend["player_two"]

    assert isinstance(base, datetime)
    assert isinstance(race, RaceSession)
    assert isinstance(team_a, Team)
    assert isinstance(driver_a, Driver)
    assert isinstance(driver_b, Driver)
    assert isinstance(driver_c, Driver)
    assert isinstance(player_one, UserProfile)
    assert isinstance(player_two, UserProfile)

    before_weekend = FantasyService(
        db_session,
        now=base,
    )

    prediction = before_weekend.save_prediction(
        player_one.id,
        race.id,
        FantasyPredictionUpdateRequest(
            answers=[
                FantasyPredictionAnswerInput(
                    question_key="FP1_P1",
                    driver_id=driver_a.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="Q1_FASTEST",
                    driver_id=driver_a.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="RACE_P1",
                    driver_id=driver_a.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="RACE_P2",
                    driver_id=driver_b.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="RACE_P3",
                    driver_id=driver_c.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="RACE_FASTEST_LAP",
                    driver_id=driver_c.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="RACE_WINNING_TEAM",
                    team_id=team_a.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="RACE_TOP_SPEED",
                    driver_id=driver_a.id,
                ),
                FantasyPredictionAnswerInput(
                    question_key="RACE_DNF_DRIVERS",
                    driver_ids=[driver_c.id],
                ),
            ]
        ),
    )

    assert prediction.prediction_id is not None

    group = before_weekend.create_group(
        player_one.id,
        FantasyGroupCreateRequest(
            name="Race Engineers",
            max_members=2,
        ),
    )

    assert group.invite_code is not None

    before_weekend.join_group(
        player_two.id,
        FantasyGroupJoinRequest(
            invite_code=group.invite_code,
        ),
    )

    after_race = FantasyService(
        db_session,
        now=base + timedelta(days=4),
    )
    score_run = after_race.score_available_questions(race.id)

    assert score_run.pending_question_keys == ["RACE_DNF_DRIVERS"]

    after_race.set_question_resolution(
        race_session_id=race.id,
        question_key="RACE_DNF_DRIVERS",
        payload=FantasyQuestionResolutionRequest(
            status="RESOLVED",
            actual_driver_ids=[driver_c.id],
            source_reference="Official classification",
        ),
        resolved_by_profile_id=player_one.id,
    )

    scored_prediction = after_race.get_prediction(
        player_one.id,
        race.id,
    )
    finalized = after_race.finalize_weekend(race.id)
    podium = after_race.get_group_podium(
        group_id=group.id,
        race_session_id=race.id,
        profile_id=player_one.id,
    )

    assert scored_prediction.total_points == 54
    assert finalized.finalized_group_count == 1
    assert [row.rank for row in podium.rows] == [1, 2]
    assert podium.rows[0].display_name == "Player One"
    assert podium.rows[0].points == 54


def test_fantasy_prevents_changes_after_question_lock(
    db_session: Session,
) -> None:
    weekend = seed_fantasy_weekend(db_session)
    base = weekend["base"]
    race = weekend["race"]
    driver_a = weekend["driver_a"]
    driver_b = weekend["driver_b"]
    player_one = weekend["player_one"]

    assert isinstance(base, datetime)
    assert isinstance(race, RaceSession)
    assert isinstance(driver_a, Driver)
    assert isinstance(driver_b, Driver)
    assert isinstance(player_one, UserProfile)

    FantasyService(
        db_session,
        now=base,
    ).save_prediction(
        player_one.id,
        race.id,
        FantasyPredictionUpdateRequest(
            answers=[
                FantasyPredictionAnswerInput(
                    question_key="RACE_P1",
                    driver_id=driver_a.id,
                )
            ]
        ),
    )

    with pytest.raises(FantasyPredictionLockedError) as error:
        FantasyService(
            db_session,
            now=race.started_at,
        ).save_prediction(
            player_one.id,
            race.id,
            FantasyPredictionUpdateRequest(
                answers=[
                    FantasyPredictionAnswerInput(
                        question_key="RACE_P1",
                        driver_id=driver_b.id,
                    )
                ]
            ),
        )

    assert error.value.question_keys == ["RACE_P1"]


def test_fantasy_v2_reuses_schedule_question_snapshots_and_saves_one_question(
    db_session: Session,
) -> None:
    weekend = seed_fantasy_weekend(db_session)
    base = weekend["base"]
    fp1 = weekend["fp1"]
    race = weekend["race"]
    driver_a = weekend["driver_a"]
    player_one = weekend["player_one"]

    assert isinstance(base, datetime)
    assert isinstance(fp1, RaceSession)
    assert isinstance(race, RaceSession)
    assert isinstance(driver_a, Driver)
    assert isinstance(player_one, UserProfile)

    before_weekend = FantasyService(db_session, now=base)
    initial_entry = before_weekend.get_entry_progress(
        player_one.id,
        race.id,
    )
    question_keys = {question.key for question in initial_entry.questions}

    assert initial_entry.status == "DRAFT"
    assert "FP1_P1" in question_keys
    assert "FP2_P1" not in question_keys
    assert "FP3_P1" not in question_keys

    fp1_snapshot = db_session.scalar(
        select(FantasyWeekendQuestion).where(
            FantasyWeekendQuestion.race_session_id == race.id,
            FantasyWeekendQuestion.question_key == "FP1_P1",
        )
    )

    assert fp1_snapshot is not None
    original_lock = fp1_snapshot.locks_at
    fp1.started_at = base + timedelta(days=10)
    db_session.commit()

    after_fp1 = FantasyService(
        db_session,
        now=base + timedelta(days=1, seconds=1),
    )
    saved = after_fp1.save_question(
        user_profile_id=player_one.id,
        race_session_id=race.id,
        question_key="RACE_P1",
        payload=FantasyQuestionSaveRequest(driver_id=driver_a.id),
    )

    assert saved.entry.status == "IN_PROGRESS"
    assert saved.question.key == "RACE_P1"
    assert saved.question.is_answered is True
    assert saved.entry.next_deadline == base + timedelta(days=2)

    persisted_snapshot = db_session.scalar(
        select(FantasyWeekendQuestion).where(
            FantasyWeekendQuestion.id == fp1_snapshot.id
        )
    )

    assert persisted_snapshot is not None
    assert persisted_snapshot.locks_at == original_lock

    with pytest.raises(FantasyPredictionLockedError) as error:
        after_fp1.save_question(
            user_profile_id=player_one.id,
            race_session_id=race.id,
            question_key="FP1_P1",
            payload=FantasyQuestionSaveRequest(driver_id=driver_a.id),
        )

    assert error.value.question_keys == ["FP1_P1"]


def test_fantasy_group_eligibility_locks_at_first_question(
    db_session: Session,
) -> None:
    weekend = seed_fantasy_weekend(db_session)
    base = weekend["base"]
    race = weekend["race"]
    driver_a = weekend["driver_a"]
    player_one = weekend["player_one"]
    player_two = weekend["player_two"]

    assert isinstance(base, datetime)
    assert isinstance(race, RaceSession)
    assert isinstance(driver_a, Driver)
    assert isinstance(player_one, UserProfile)
    assert isinstance(player_two, UserProfile)

    before_weekend = FantasyService(db_session, now=base)
    before_weekend.save_question(
        user_profile_id=player_one.id,
        race_session_id=race.id,
        question_key="RACE_P1",
        payload=FantasyQuestionSaveRequest(driver_id=driver_a.id),
    )
    group = before_weekend.create_group(
        player_one.id,
        FantasyGroupCreateRequest(name="Fair Play", max_members=2),
    )

    assert group.invite_code is not None

    FantasyService(
        db_session,
        now=base + timedelta(days=1, seconds=1),
    ).join_group(
        player_two.id,
        FantasyGroupJoinRequest(invite_code=group.invite_code),
    )

    after_race = FantasyService(
        db_session,
        now=base + timedelta(days=4),
    )
    after_race.score_available_questions(race.id)
    after_race.set_question_resolution(
        race_session_id=race.id,
        question_key="RACE_DNF_DRIVERS",
        payload=FantasyQuestionResolutionRequest(
            status="NOT_SCORED",
            source_reference="Official classification is inconclusive",
        ),
        resolved_by_profile_id=player_one.id,
    )
    after_race.finalize_weekend(race.id)

    eligibility_rows = db_session.scalars(
        select(FantasyGroupWeekendEligibility).where(
            FantasyGroupWeekendEligibility.group_id == group.id,
            FantasyGroupWeekendEligibility.race_session_id == race.id,
        )
    ).all()
    podium = after_race.get_group_podium(
        group_id=group.id,
        race_session_id=race.id,
        profile_id=player_one.id,
    )

    assert [row.user_profile_id for row in eligibility_rows] == [
        player_one.id
    ]
    assert [row.display_name for row in podium.rows] == ["Player One"]


def test_fantasy_correction_refinalizes_existing_group_podium(
    db_session: Session,
) -> None:
    weekend = seed_fantasy_weekend(db_session)
    base = weekend["base"]
    race = weekend["race"]
    driver_a = weekend["driver_a"]
    driver_c = weekend["driver_c"]
    player_one = weekend["player_one"]
    player_two = weekend["player_two"]

    assert isinstance(base, datetime)
    assert isinstance(race, RaceSession)
    assert isinstance(driver_a, Driver)
    assert isinstance(driver_c, Driver)
    assert isinstance(player_one, UserProfile)
    assert isinstance(player_two, UserProfile)

    before_weekend = FantasyService(db_session, now=base)
    before_weekend.save_question(
        user_profile_id=player_one.id,
        race_session_id=race.id,
        question_key="RACE_FASTEST_LAP",
        payload=FantasyQuestionSaveRequest(driver_id=driver_c.id),
    )
    before_weekend.save_question(
        user_profile_id=player_two.id,
        race_session_id=race.id,
        question_key="RACE_FASTEST_LAP",
        payload=FantasyQuestionSaveRequest(driver_id=driver_a.id),
    )
    group = before_weekend.create_group(
        player_one.id,
        FantasyGroupCreateRequest(name="Correction Check", max_members=2),
    )

    assert group.invite_code is not None

    before_weekend.join_group(
        player_two.id,
        FantasyGroupJoinRequest(invite_code=group.invite_code),
    )

    after_race = FantasyService(
        db_session,
        now=base + timedelta(days=4),
    )
    after_race.score_available_questions(race.id)
    after_race.set_question_resolution(
        race_session_id=race.id,
        question_key="RACE_DNF_DRIVERS",
        payload=FantasyQuestionResolutionRequest(
            status="NOT_SCORED",
            source_reference="No reliable public DNF classification",
        ),
        resolved_by_profile_id=player_one.id,
    )
    after_race.finalize_weekend(race.id)

    initial_podium = after_race.get_group_podium(
        group_id=group.id,
        race_session_id=race.id,
        profile_id=player_one.id,
    )

    assert initial_podium.rows[0].display_name == "Player One"

    after_race.set_question_resolution(
        race_session_id=race.id,
        question_key="RACE_FASTEST_LAP",
        payload=FantasyQuestionResolutionRequest(
            status="RESOLVED",
            actual_driver_id=driver_a.id,
            source_reference="Official timing correction",
        ),
        resolved_by_profile_id=player_one.id,
    )

    corrected_podium = after_race.get_group_podium(
        group_id=group.id,
        race_session_id=race.id,
        profile_id=player_one.id,
    )

    assert corrected_podium.rows[0].display_name == "Player Two"
    assert corrected_podium.rows[0].points == 5

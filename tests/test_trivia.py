from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.trivia import (
    TriviaAnswer,
    TriviaGuest,
    TriviaQuestion,
    TriviaRace,
)
from app.services.trivia_seed_v1 import seed_rows
from app.services.trivia_service import answer_question, start_race
from app.api.v1.trivia import editor
from sqlalchemy.orm import Session
from app.api.v1.trivia import trivia_guest_limit
from app.main import app

PREFIX = "/api/v1/trivia"


@pytest.fixture(autouse=True)
def guest_rate_limit():
    app.dependency_overrides[trivia_guest_limit] = lambda: None
    yield
    app.dependency_overrides.pop(trivia_guest_limit, None)


@pytest.fixture
def bank(db_session):
    for row in seed_rows():
        if db_session.get(TriviaQuestion, row["id"]) is None:
            db_session.add(TriviaQuestion(**row))
    db_session.commit()


def guest(client):
    response = client.post(f"{PREFIX}/guest-session")
    assert response.status_code == 201
    return {"X-Trivia-Guest-Key": response.json()["guest_key"]}


def start(client, headers, difficulty="easy", key=None):
    response = client.post(
        f"{PREFIX}/races",
        headers=headers,
        json={"difficulty": difficulty, "start_key": str(key or uuid4())},
    )
    assert response.status_code == 201, response.text
    return response.json()


def submit(client, headers, race, option):
    return client.post(
        f"{PREFIX}/races/{race['id']}/answers",
        headers=headers,
        json={
            "assignment_id": race["question"]["assignment_id"],
            "option_id": option,
        },
    )


def correct_option(db, race):
    stored = db.get(TriviaRace, UUID(race["id"]))
    return stored.snapshot["questions"][race["answered"]]["correct_option_id"]


@pytest.mark.parametrize(
    "difficulty,reward", [("easy", 10), ("medium", 20), ("hard", 35)]
)
def test_win_resume_and_no_answer_leaks(
    api_client, db_session, bank, difficulty, reward
):
    headers = guest(api_client)
    race = start(api_client, headers, difficulty)
    serialized = str(race)
    assert "correct_option_id" not in serialized
    assert "explanation" not in serialized
    assert "bot_moves" not in serialized
    assert len(race["participants"]) == 4
    assert (
        len(
            set(
                q["prompt"]
                for q in db_session.get(TriviaRace, UUID(race["id"])).snapshot[
                    "questions"
                ]
            )
        )
        == 10
    )
    for turn in range(8):
        response = submit(
            api_client, headers, race, correct_option(db_session, race)
        )
        assert response.status_code == 200, response.text
        race = response.json()
        assert race["points"] == (turn + 1) * reward
        assert race["participants"][0]["progress"] == (turn + 1) / 8
        restored = api_client.get(
            f"{PREFIX}/races/{race['id']}", headers=headers
        ).json()
        assert restored == race
    assert race["status"] == "completed"
    assert race["question"] is None
    assert race["participants"][0]["rank"] == 1
    assert api_client.get(
        f"{PREFIX}/races/active", headers=headers
    ).json() == {"race": None}


def test_wrong_answer_retry_and_owner_isolation(api_client, db_session, bank):
    headers = guest(api_client)
    key = uuid4()
    race = start(api_client, headers, key=key)
    assert start(api_client, headers, key=key) == race
    option = next(
        o["id"]
        for o in race["question"]["options"]
        if o["id"] != correct_option(db_session, race)
    )
    response = submit(api_client, headers, race, option)
    assert response.status_code == 200
    updated = response.json()
    assert not updated["feedback"]["correct"]
    assert updated["feedback"]["explanation"]
    assert updated["points"] == 0
    assert updated["participants"][0]["progress"] == 0
    assert updated["participants"][1]["progress"] > 0
    assert submit(api_client, headers, race, option).json() == updated
    assert (
        submit(
            api_client, headers, race, correct_option(db_session, race)
        ).status_code
        == 409
    )
    assert (
        db_session.scalar(select(func.count()).select_from(TriviaAnswer)) == 1
    )
    other = guest(api_client)
    assert (
        api_client.get(
            f"{PREFIX}/races/{race['id']}", headers=other
        ).status_code
        == 404
    )
    assert submit(api_client, other, race, option).status_code == 404
    assert api_client.get(f"{PREFIX}/races/{race['id']}").status_code == 401
    assert (
        api_client.get(f"{PREFIX}/races/active", headers=headers).json()[
            "race"
        ]
        == updated
    )


def test_future_invalid_options_and_expired_guest(
    api_client, db_session, bank
):
    headers = guest(api_client)
    race = start(api_client, headers)
    assert submit(api_client, headers, race, "forged").status_code == 422
    forged = deepcopy(race)
    forged["question"]["assignment_id"] = str(uuid4())
    assert (
        submit(
            api_client, headers, forged, race["question"]["options"][0]["id"]
        ).status_code
        == 409
    )
    session = db_session.scalar(select(TriviaGuest))
    session.expires_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    assert (
        api_client.get(f"{PREFIX}/races/active", headers=headers).status_code
        == 401
    )


def test_snapshot_survives_question_retirement_and_loss(
    api_client, db_session, bank
):
    headers = guest(api_client)
    race = start(api_client, headers)
    question = db_session.scalar(select(TriviaQuestion))
    question.published = False
    question.content = {"prompt": "Retired"}
    db_session.commit()
    for _ in range(10):
        if race["status"] == "completed":
            break
        wrong = next(
            o["id"]
            for o in race["question"]["options"]
            if o["id"] != correct_option(db_session, race)
        )
        race = submit(api_client, headers, race, wrong).json()
    assert race["status"] == "completed"
    assert race["participants"][0]["rank"] == 4
    assert race["correct"] == 0
    assert race["answered"] <= 10


def test_editor_bank_not_public(api_client, bank):
    assert api_client.get(f"{PREFIX}/questions").status_code == 401
    assert api_client.put(
        f"{PREFIX}/questions/{uuid4()}", json={}
    ).status_code in (401, 422)
    assert all(
        d["available"]
        for d in api_client.get(f"{PREFIX}/config").json()["difficulties"]
    )


def test_editor_review_version_and_retirement(api_client, db_session, bank):
    app.dependency_overrides[editor] = lambda: None
    rows = api_client.get(f"{PREFIX}/questions").json()
    question = rows[0]
    payload = {
        key: value
        for key, value in question.items()
        if key not in {"id", "version"}
    }
    payload["expected_version"] = question["version"]
    payload["published"] = False
    saved = api_client.put(
        f"{PREFIX}/questions/{question['id']}", json=payload
    )
    assert saved.status_code == 200
    assert not saved.json()["published"]
    assert saved.json()["version"] == question["version"] + 1
    assert (
        api_client.put(
            f"{PREFIX}/questions/{question['id']}", json=payload
        ).status_code
        == 409
    )
    payload["expected_version"] += 1
    payload["correct_option_id"] = "invalid"
    assert (
        api_client.put(
            f"{PREFIX}/questions/{question['id']}", json=payload
        ).status_code
        == 422
    )
    app.dependency_overrides.pop(editor, None)


def test_concurrent_answer_retries_commit_once(request, db_session, bank):
    if not request.config.getoption("--postgresql"):
        pytest.skip("Separate connections require the PostgreSQL fixture.")
    owner = "guest:concurrency-test"
    race = start_race(db_session, owner, "easy", uuid4())
    option = correct_option(db_session, race)
    race_id = UUID(race["id"])
    assignment = UUID(race["question"]["assignment_id"])

    def answer_once(_):
        with Session(db_session.bind) as connection:
            return answer_question(
                connection, owner, race_id, assignment, option
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(answer_once, range(4)))
    assert all(response == responses[0] for response in responses)
    db_session.expire_all()
    assert db_session.get(TriviaRace, race_id).revision == 1
    assert (
        db_session.scalar(select(func.count()).select_from(TriviaAnswer)) == 1
    )

"""Turn-based, authoritative trivia. No simulation or worker loop needed."""

from copy import deepcopy
import random
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.trivia import TriviaAnswer, TriviaQuestion, TriviaRace

RULES = {
    "easy": {"reward": 10, "bot_pace": [0.06, 0.07, 0.08]},
    "medium": {"reward": 20, "bot_pace": [0.075, 0.085, 0.095]},
    "hard": {"reward": 35, "bot_pace": [0.085, 0.095, 0.105]},
}
QUESTION_LIMIT = 10


def race_for_owner(db: Session, race_id: UUID, owner: str) -> TriviaRace:
    race = db.get(TriviaRace, race_id)
    if race is None or race.owner != owner:
        raise HTTPException(404, "Trivia race was not found.")
    return race


def race_response(race: TriviaRace) -> dict:
    data = race.snapshot
    count = data["answered"]
    question = None
    if race.status == "racing":
        assigned = data["questions"][count]
        question = {
            "assignment_id": assigned["assignment_id"],
            "number": count + 1,
            "prompt": assigned["prompt"],
            "options": assigned["options"],
        }
    participants = deepcopy(data["participants"])
    ordered = sorted(
        participants,
        key=lambda p: (
            p["finish_at"] if p["finish_at"] is not None else float("inf"),
            -p["progress"],
            p["id"],
        ),
    )
    for rank, participant in enumerate(ordered, start=1):
        participant["rank"] = rank
    return {
        "id": str(race.id),
        "difficulty": race.difficulty,
        "status": race.status,
        "revision": race.revision,
        "rules": data["rules"],
        "answered": count,
        "correct": data["correct"],
        "points": data["points"],
        "participants": participants,
        "question": question,
        "feedback": data.get("feedback"),
    }


def start_race(db: Session, owner: str, difficulty: str, key: UUID) -> dict:
    existing = db.scalar(
        select(TriviaRace).where(
            TriviaRace.owner == owner, TriviaRace.start_key == key
        )
    )
    if existing:
        if existing.difficulty != difficulty:
            raise HTTPException(409, "This start key belongs to another race.")
        return race_response(existing)
    available = list(
        db.scalars(
            select(TriviaQuestion).where(
                TriviaQuestion.difficulty == difficulty,
                TriviaQuestion.published.is_(True),
            )
        )
    )
    if len(available) < QUESTION_LIMIT:
        raise HTTPException(
            503, "This difficulty needs more published questions."
        )
    rng = random.SystemRandom()
    assigned = []
    for question in rng.sample(available, QUESTION_LIMIT):
        content = deepcopy(question.content)
        correct = content["correct_option_id"]
        for option in content["options"]:
            old_id = option["id"]
            option["id"] = str(uuid4())
            if old_id == correct:
                content["correct_option_id"] = option["id"]
        rng.shuffle(content["options"])
        assigned.append(
            {
                **content,
                "assignment_id": str(uuid4()),
                "question_id": str(question.id),
                "version": question.version,
            }
        )
    rules = RULES[difficulty]
    participants = [
        {
            "id": "player",
            "name": "You",
            "is_player": True,
            "progress": 0.0,
            "finish_at": None,
        },
        *[
            {
                "id": f"bot-{i}",
                "name": name,
                "is_player": False,
                "progress": 0.0,
                "finish_at": None,
            }
            for i, name in enumerate(["Apex", "Slipstream", "Chicane"])
        ],
    ]
    race = TriviaRace(
        owner=owner,
        start_key=key,
        difficulty=difficulty,
        snapshot={
            "rules": {
                "version": 1,
                "reward": rules["reward"],
                "track_distance": rules["reward"] * 8,
                "question_limit": QUESTION_LIMIT,
                "description": "Opponents move after every answer. First across the line wins; after 10 questions, the most distance wins.",
            },
            "questions": assigned,
            "participants": participants,
            "bot_moves": [
                [
                    round(rng.uniform(pace - 0.012, pace + 0.012), 6)
                    for _ in range(QUESTION_LIMIT)
                ]
                for pace in rules["bot_pace"]
            ],
            "answered": 0,
            "correct": 0,
            "points": 0,
        },
    )
    db.add(race)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if (
            db.scalar(
                select(TriviaRace.id).where(
                    TriviaRace.owner == owner, TriviaRace.start_key == key
                )
            )
            is None
        ):
            raise
        return start_race(db, owner, difficulty, key)
    return race_response(race)


def _previous_answer(db, race_id, assignment_id, option_id):
    previous = db.scalar(
        select(TriviaAnswer).where(
            TriviaAnswer.race_id == race_id,
            TriviaAnswer.assignment_id == assignment_id,
        )
    )
    if previous:
        if previous.option_id != option_id:
            raise HTTPException(
                409, "This question has already been answered."
            )
        return previous.response
    return None


def answer_question(
    db: Session, owner: str, race_id: UUID, assignment_id: UUID, option_id: str
) -> dict:
    race = race_for_owner(db, race_id, owner)
    previous = _previous_answer(db, race_id, assignment_id, option_id)
    if previous is not None:
        return previous
    if race.status != "racing":
        raise HTTPException(409, "This race is already complete.")
    data = deepcopy(race.snapshot)
    turn = data["answered"]
    question = data["questions"][turn]
    if question["assignment_id"] != str(assignment_id):
        raise HTTPException(
            409, "Refresh the race to get the current question."
        )
    if option_id not in {option["id"] for option in question["options"]}:
        raise HTTPException(422, "Choose one of this question's options.")
    correct = option_id == question["correct_option_id"]
    reward = data["rules"]["reward"] if correct else 0
    data["answered"] += 1
    data["correct"] += int(correct)
    data["points"] += reward
    moves = [
        reward / data["rules"]["track_distance"],
        *[schedule[turn] for schedule in data["bot_moves"]],
    ]
    for participant, move in zip(data["participants"], moves, strict=True):
        progress = participant["progress"]
        if move and progress + move >= 1 and participant["finish_at"] is None:
            participant["finish_at"] = turn + (1 - progress) / move
        participant["progress"] = min(1.0, round(progress + move, 6))
    data["feedback"] = {
        "assignment_id": str(assignment_id),
        "correct": correct,
        "selected_option_id": option_id,
        "correct_option_id": question["correct_option_id"],
        "correct_label": next(
            o["label"]
            for o in question["options"]
            if o["id"] == question["correct_option_id"]
        ),
        "reward": reward,
        "explanation": question["explanation"],
        "source_url": question["source_url"],
    }
    completed = (
        any(p["finish_at"] is not None for p in data["participants"])
        or data["answered"] == data["rules"]["question_limit"]
    )
    revision = race.revision
    next_status = "completed" if completed else "racing"
    # Compare-and-swap works on SQLite as well as PostgreSQL. Progress and the
    # answer ledger commit in one transaction, including the cached response.
    result = db.execute(
        update(TriviaRace)
        .where(TriviaRace.id == race_id, TriviaRace.revision == revision)
        .values(snapshot=data, revision=revision + 1, status=next_status),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        db.rollback()
        previous = _previous_answer(db, race_id, assignment_id, option_id)
        if previous is not None:
            return previous
        raise HTTPException(409, "The race changed. Refresh and try again.")
    db.refresh(race)
    response = race_response(race)
    db.add(
        TriviaAnswer(
            race_id=race_id,
            assignment_id=assignment_id,
            option_id=option_id,
            response=response,
        )
    )
    db.commit()
    return response

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rate_limit import enforce_rate_limit
from app.core.security import require_roles
from app.core.trivia_identity import create_trivia_guest, trivia_owner
from app.db.database import get_db
from app.models.trivia import TriviaQuestion, TriviaRace
from app.services.trivia_service import (
    QUESTION_LIMIT,
    RULES,
    answer_question,
    race_for_owner,
    race_response,
    start_race,
)

router = APIRouter(prefix="/trivia", tags=["Trivia Race"])
Difficulty = Literal["easy", "medium", "hard"]
editor = require_roles("admin", "editor")


async def trivia_guest_limit(request: Request):
    await enforce_rate_limit(
        request, bucket="trivia-guest", limit=20, window_seconds=3600
    )


class StartRequest(BaseModel):
    difficulty: Difficulty
    start_key: UUID


class AnswerRequest(BaseModel):
    assignment_id: UUID
    option_id: str = Field(min_length=1, max_length=36)


class Option(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    label: str = Field(min_length=1, max_length=250)


class QuestionRequest(BaseModel):
    difficulty: Difficulty
    prompt: str = Field(min_length=8, max_length=1000)
    options: list[Option] = Field(min_length=4, max_length=4)
    correct_option_id: str
    explanation: str = Field(min_length=8, max_length=2000)
    source_url: HttpUrl
    published: bool = False
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_options(self):
        ids = {o.id for o in self.options}
        labels = {o.label.strip().casefold() for o in self.options}
        if (
            len(ids) != 4
            or len(labels) != 4
            or self.correct_option_id not in ids
        ):
            raise ValueError(
                "Provide four distinct options and a valid answer."
            )
        return self


@router.get("/config")
def config(db: Session = Depends(get_db)):
    counts = dict(
        db.execute(
            select(TriviaQuestion.difficulty, func.count())
            .where(TriviaQuestion.published.is_(True))
            .group_by(TriviaQuestion.difficulty)
        ).all()
    )
    return {
        "question_limit": QUESTION_LIMIT,
        "difficulties": [
            {
                "id": difficulty,
                "reward": rules["reward"],
                "available": counts.get(difficulty, 0) >= QUESTION_LIMIT,
            }
            for difficulty, rules in RULES.items()
        ],
    }


@router.post(
    "/guest-session",
    status_code=201,
    dependencies=[Depends(trivia_guest_limit)],
)
def guest_session(db: Session = Depends(get_db)):
    return {"guest_key": create_trivia_guest(db)}


@router.get("/races/active")
def active_race(
    owner: str = Depends(trivia_owner), db: Session = Depends(get_db)
):
    race = db.scalar(
        select(TriviaRace)
        .where(TriviaRace.owner == owner, TriviaRace.status == "racing")
        .order_by(TriviaRace.created_at.desc())
        .limit(1)
    )
    return {"race": race_response(race) if race else None}


@router.post("/races", status_code=201)
def create_race(
    payload: StartRequest,
    owner: str = Depends(trivia_owner),
    db: Session = Depends(get_db),
):
    return start_race(db, owner, payload.difficulty, payload.start_key)


@router.get("/races/{race_id}")
def get_race(
    race_id: UUID,
    owner: str = Depends(trivia_owner),
    db: Session = Depends(get_db),
):
    return race_response(race_for_owner(db, race_id, owner))


@router.post("/races/{race_id}/answers")
def answer(
    race_id: UUID,
    payload: AnswerRequest,
    owner: str = Depends(trivia_owner),
    db: Session = Depends(get_db),
):
    return answer_question(
        db, owner, race_id, payload.assignment_id, payload.option_id
    )


def question_response(question):
    return {
        "id": str(question.id),
        "version": question.version,
        "difficulty": question.difficulty,
        "published": question.published,
        **question.content,
    }


@router.get("/questions", dependencies=[Depends(editor)])
def questions(db: Session = Depends(get_db)):
    return [
        question_response(q)
        for q in db.scalars(
            select(TriviaQuestion).order_by(TriviaQuestion.id).limit(500)
        )
    ]


@router.put("/questions/{question_id}", dependencies=[Depends(editor)])
def save_question(
    question_id: UUID, payload: QuestionRequest, db: Session = Depends(get_db)
):
    question = db.scalar(
        select(TriviaQuestion)
        .where(TriviaQuestion.id == question_id)
        .with_for_update()
    )
    if question:
        if question.version != payload.expected_version:
            raise HTTPException(409, "Question version changed; reload it.")
        question.version += 1
    else:
        if payload.expected_version is not None:
            raise HTTPException(404, "Question was not found.")
        question = TriviaQuestion(id=question_id, version=1)
        db.add(question)
    question.difficulty = payload.difficulty
    question.published = payload.published
    question.content = payload.model_dump(
        mode="json", exclude={"difficulty", "published", "expected_version"}
    )
    db.commit()
    return question_response(question)

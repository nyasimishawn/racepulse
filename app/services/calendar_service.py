from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.calendar import CalendarRevision, CalendarWeekend
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.schemas.calendar import CalendarWeekendInput


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def calendar_identifier(value: str) -> str:
    identifier = value.strip().upper()
    return "SQ" if identifier == "SS" else identifier


def fantasy_schedule(db: Session, meeting_id: UUID) -> dict:
    weekend = db.scalar(
        select(CalendarWeekend).where(CalendarWeekend.meeting_id == meeting_id)
    )
    if weekend is None:
        return {}
    sessions = db.scalars(
        select(RaceSession).where(RaceSession.meeting_id == meeting_id)
    ).all()
    deadlines = {
        calendar_identifier(session.session_identifier): utc(
            session.started_at
        )
        if session.started_at
        else None
        for session in sessions
    }
    locked = {}
    revisions = db.scalars(
        select(CalendarRevision)
        .where(CalendarRevision.weekend_id == weekend.id)
        .order_by(CalendarRevision.version)
    ).all()
    # Replay publication times, not read times. A deadline that elapsed before
    # a revision remains closed even if no Fantasy snapshot existed then.
    result = {}
    for revision in revisions:
        for identifier, deadline in deadlines.items():
            if deadline is not None and deadline <= utc(revision.changed_at):
                locked.setdefault(identifier, deadline)
        schedule = revision.schedule
        suspended = schedule["status"] in {"CANCELLED", "POSTPONED"}
        active = {
            item["identifier"]: (
                None
                if suspended or item["status"] in {"CANCELLED", "POSTPONED"}
                else datetime.fromisoformat(
                    item["starts_at"].replace("Z", "+00:00")
                )
            )
            for item in schedule["sessions"]
        }
        result = {
            identifier: None
            if active.get(identifier) is None
            else locked.get(identifier, active[identifier])
            for identifier in deadlines.keys() | active.keys()
        }
        deadlines = {
            identifier: locked.get(identifier, deadline)
            for identifier, deadline in result.items()
        }
    return result


class CalendarService:
    def __init__(self, db: Session):
        self.db = db

    def save(
        self,
        payload: CalendarWeekendInput,
        actor: str,
        weekend_id: UUID | None = None,
        version: int | None = None,
        automated: bool = False,
        sync_key: str | None = None,
    ):
        row = self.db.get(CalendarWeekend, weekend_id) if weekend_id else None
        if weekend_id and row is None:
            raise LookupError("Calendar weekend not found.")
        if row and row.version != version:
            raise ValueError("Schedule changed; reload before updating.")
        if row and row.meeting_id and row.meeting_id != payload.meeting_id:
            raise ValueError("An existing meeting link cannot be replaced.")
        if payload.meeting_id:
            meeting = self.db.get(Meeting, payload.meeting_id)
            if meeting is None or meeting.year != payload.year:
                raise ValueError("Link a meeting from the same season.")
            duplicate = self.db.scalar(
                select(CalendarWeekend).where(
                    CalendarWeekend.meeting_id == payload.meeting_id
                )
            )
            if duplicate and duplicate.id != weekend_id:
                raise ValueError("Meeting is already linked to a weekend.")
        now = datetime.now(UTC)
        if row is None:
            row = CalendarWeekend()
            self.db.add(row)
        row.year = payload.year
        row.sync_enabled = automated
        if sync_key:
            row.sync_key = sync_key
        row.event_name = payload.event_name
        row.meeting_id = payload.meeting_id
        row.schedule = payload.model_dump(mode="json")
        row.updated_at = now
        self.db.flush()
        self.db.add(
            CalendarRevision(
                weekend_id=row.id,
                version=row.version,
                schedule=row.schedule,
                changed_by=actor,
                changed_at=now,
            )
        )
        self.db.commit()
        return self.overview(row)

    def overview(self, row: CalendarWeekend):
        imported = (
            self.db.scalars(
                select(RaceSession).where(
                    RaceSession.meeting_id == row.meeting_id
                )
            ).all()
            if row.meeting_id
            else []
        )
        by_identifier = {
            calendar_identifier(session.session_identifier): session
            for session in imported
        }
        sessions = []
        for item in row.schedule["sessions"]:
            linked = by_identifier.get(item["identifier"])
            sessions.append(
                {
                    **item,
                    "race_session_id": linked.id if linked else None,
                    "imported": linked is not None,
                }
            )
        return {
            **row.schedule,
            "id": row.id,
            "version": row.version,
            "updated_at": utc(row.updated_at),
            "sessions": sessions,
        }

    def list(self, year: int):
        rows = self.db.scalars(
            select(CalendarWeekend).where(CalendarWeekend.year == year)
        ).all()
        return [
            self.overview(row)
            for row in sorted(
                rows,
                key=lambda row: (row.schedule["round_number"], row.event_name),
            )
        ]

    def next(self):
        now = datetime.now(UTC)
        candidates = []
        for row in self.db.scalars(select(CalendarWeekend)).all():
            if row.schedule["status"] not in {"SCHEDULED", "IN_PROGRESS"}:
                continue
            race = next(
                s for s in row.schedule["sessions"] if s["identifier"] == "R"
            )
            if (
                race["status"] not in {"SCHEDULED", "IN_PROGRESS"}
                or not race["starts_at"]
            ):
                continue
            start = datetime.fromisoformat(
                race["starts_at"].replace("Z", "+00:00")
            )
            if start >= now or "IN_PROGRESS" in {
                race["status"],
                row.schedule["status"],
            }:
                candidates.append((start, row))
        return (
            self.overview(min(candidates, key=lambda item: item[0])[1])
            if candidates
            else None
        )

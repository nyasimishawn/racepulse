from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.exc import IntegrityError

from app.core.security import AuthenticatedUser, require_roles
from app.db.database import get_db
from app.models.calendar import (
    CalendarRevision,
    CalendarSyncState,
    CalendarWeekend,
)
from app.schemas.calendar import CalendarWeekendInput
from app.services.calendar_service import CalendarService

router = APIRouter(tags=["Calendar"])


@router.get("/calendar/sync-status")
def sync_status(
    year: int = Query(ge=2018, le=2100), db: Session = Depends(get_db)
):
    state = db.get(CalendarSyncState, year)
    return {
        "year": year,
        "checked_at": state.checked_at if state else None,
        "result": state.result if state else {"status": "NEVER_RUN"},
    }


@router.get("/calendar")
def calendar(
    year: int = Query(ge=2018, le=2100), db: Session = Depends(get_db)
):
    return CalendarService(db).list(year)


@router.get("/weekends/next")
def next_weekend(db: Session = Depends(get_db)):
    return CalendarService(db).next()


@router.get("/weekends/{weekend_id}/overview")
def overview(weekend_id: UUID, db: Session = Depends(get_db)):
    row = db.get(CalendarWeekend, weekend_id)
    if row is None:
        raise HTTPException(404, "Calendar weekend not found.")
    return CalendarService(db).overview(row)


@router.get("/calendar/{weekend_id}/history")
def history(weekend_id: UUID, db: Session = Depends(get_db)):
    if db.get(CalendarWeekend, weekend_id) is None:
        raise HTTPException(404, "Calendar weekend not found.")
    return [
        {
            "version": row.version,
            "changed_at": row.changed_at,
            "schedule": row.schedule,
        }
        for row in db.scalars(
            select(CalendarRevision)
            .where(CalendarRevision.weekend_id == weekend_id)
            .order_by(CalendarRevision.version)
        ).all()
    ]


def save(payload, user, db, weekend_id=None, version=None):
    try:
        return CalendarService(db).save(
            payload, user.subject, weekend_id, version
        )
    except LookupError as error:
        db.rollback()
        raise HTTPException(404, str(error)) from error
    except (ValueError, StaleDataError, IntegrityError) as error:
        db.rollback()
        raise HTTPException(
            409, "Schedule conflict or invalid meeting link."
        ) from error


@router.post("/calendar", status_code=201)
def create_weekend(
    payload: CalendarWeekendInput,
    user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
):
    return save(payload, user, db)


@router.put("/calendar/{weekend_id}")
def update_weekend(
    weekend_id: UUID,
    payload: CalendarWeekendInput,
    version: int = Query(ge=1),
    user: AuthenticatedUser = Depends(require_roles("editor")),
    db: Session = Depends(get_db),
):
    return save(payload, user, db, weekend_id, version)

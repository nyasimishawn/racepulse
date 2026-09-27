from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, get_active_current_user
from app.db.database import get_db
from app.models.alert import (
    Alert,
    AlertStatus,
    AlertType,
    WeekendAlertPreference,
)
from app.models.calendar import CalendarWeekend
from app.models.user_profile import UserProfile
from app.services.alert_service import AlertService


router = APIRouter(prefix="/alerts", tags=["Alerts"])


class AlertPreferenceUpdate(BaseModel):
    session_soon: bool = False
    fantasy_deadline: bool = False
    schedule_change: bool = False


class AlertPreferenceResponse(AlertPreferenceUpdate):
    weekend_id: UUID


class AlertResponse(BaseModel):
    id: UUID
    weekend_id: UUID
    type: AlertType
    session_identifier: str | None
    title: str
    body: str
    event_at: datetime | None
    delivered_at: datetime
    read_at: datetime | None


def my_id(db: Session, user: AuthenticatedUser) -> UUID:
    return db.scalar(
        select(UserProfile.id).where(
            UserProfile.keycloak_subject == user.subject
        )
    )


def preference_response(preference: WeekendAlertPreference) -> dict:
    return {
        "weekend_id": preference.weekend_id,
        "session_soon": preference.session_soon,
        "fantasy_deadline": preference.fantasy_deadline,
        "schedule_change": preference.schedule_change,
    }


@router.get("/preferences", response_model=list[AlertPreferenceResponse])
def list_preferences(
    user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
):
    return [
        preference_response(item)
        for item in db.scalars(
            select(WeekendAlertPreference).where(
                WeekendAlertPreference.user_id == my_id(db, user)
            )
        ).all()
    ]


@router.put(
    "/preferences/{weekend_id}", response_model=AlertPreferenceResponse
)
def set_preference(
    weekend_id: UUID,
    payload: AlertPreferenceUpdate,
    user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
):
    weekend = db.get(CalendarWeekend, weekend_id)
    if weekend is None:
        raise HTTPException(404, "Calendar weekend not found.")
    preference = AlertService(db).set_preference(
        my_id(db, user), weekend, **payload.model_dump()
    )
    return preference_response(preference)


@router.delete("/preferences/{weekend_id}", status_code=204)
def remove_preference(
    weekend_id: UUID,
    user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
) -> Response:
    AlertService(db).remove_preference(my_id(db, user), weekend_id)
    return Response(status_code=204)


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    limit: int = Query(default=50, ge=1, le=100),
    user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
):
    alerts = db.scalars(
        select(Alert)
        .where(
            Alert.user_id == my_id(db, user),
            Alert.status == AlertStatus.DELIVERED,
        )
        .order_by(Alert.delivered_at.desc(), Alert.id.desc())
        .limit(limit)
    ).all()
    return [alert_response(alert) for alert in alerts]


@router.post("/{alert_id}/read", response_model=AlertResponse)
def mark_read(
    alert_id: UUID,
    user: AuthenticatedUser = Depends(get_active_current_user),
    db: Session = Depends(get_db),
):
    alert = db.scalar(
        select(Alert).where(
            Alert.id == alert_id,
            Alert.user_id == my_id(db, user),
            Alert.status == AlertStatus.DELIVERED,
        )
    )
    if alert is None:
        raise HTTPException(404, "Alert not found.")
    if alert.read_at is None:
        alert.read_at = datetime.now(UTC)
        db.commit()
    return alert_response(alert)


def alert_response(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "weekend_id": alert.weekend_id,
        "type": alert.alert_type.value,
        "session_identifier": alert.session_identifier,
        "title": alert.title,
        "body": alert.body,
        "event_at": alert.event_at,
        "delivered_at": alert.delivered_at,
        "read_at": alert.read_at,
    }

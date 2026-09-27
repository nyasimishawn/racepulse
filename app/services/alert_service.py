"""PostgreSQL backed alert scheduling and in-app delivery."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.alert import (
    Alert,
    AlertStatus,
    AlertType,
    WeekendAlertPreference,
)
from app.models.calendar import CalendarWeekend
from app.models.race_session import RaceSession
from app.services.calendar_service import (
    calendar_identifier,
    fantasy_schedule,
    utc,
)


logger = logging.getLogger(__name__)


class PushDelivery(Protocol):
    def send(self, alert: Alert) -> None:
        """Send with alert.id as the provider idempotency key; raise to retry."""


class NoopPushDelivery:
    def send(self, alert: Alert) -> None:
        del alert


def meaningful_schedule(schedule: dict) -> tuple:
    return (
        schedule["status"],
        tuple(
            sorted(
                (
                    session["identifier"],
                    session["status"],
                    session["starts_at"],
                )
                for session in schedule["sessions"]
            )
        ),
    )


class AlertService:
    def __init__(
        self,
        db: Session,
        *,
        now: datetime | None = None,
        push: PushDelivery | None = None,
    ) -> None:
        self.db = db
        self.now = utc(now or datetime.now(UTC))
        self.push = push or NoopPushDelivery()

    def set_preference(
        self,
        user_id: UUID,
        weekend: CalendarWeekend,
        *,
        session_soon: bool,
        fantasy_deadline: bool,
        schedule_change: bool,
    ) -> WeekendAlertPreference:
        preference = self.db.scalar(
            select(WeekendAlertPreference).where(
                WeekendAlertPreference.user_id == user_id,
                WeekendAlertPreference.weekend_id == weekend.id,
            )
        )
        if preference is None:
            preference = WeekendAlertPreference(
                user_id=user_id, weekend_id=weekend.id
            )
            self.db.add(preference)
            self.db.flush()
        preference.session_soon = session_soon
        preference.fantasy_deadline = fantasy_deadline
        preference.schedule_change = schedule_change
        self._replace_reminders(weekend, preference)
        self.db.commit()
        return preference

    def remove_preference(self, user_id: UUID, weekend_id: UUID) -> None:
        preference = self.db.scalar(
            select(WeekendAlertPreference).where(
                WeekendAlertPreference.user_id == user_id,
                WeekendAlertPreference.weekend_id == weekend_id,
            )
        )
        if preference is None:
            return
        for alert in self.db.scalars(
            select(Alert).where(
                Alert.user_id == user_id,
                Alert.weekend_id == weekend_id,
                Alert.status == AlertStatus.PENDING,
            )
        ):
            alert.status = AlertStatus.CANCELLED
        self.db.delete(preference)
        self.db.commit()

    def calendar_changed(
        self, weekend: CalendarWeekend, previous: dict | None
    ) -> None:
        changed = previous is not None and meaningful_schedule(
            previous
        ) != meaningful_schedule(weekend.schedule)
        preferences = self.db.scalars(
            select(WeekendAlertPreference).where(
                WeekendAlertPreference.weekend_id == weekend.id
            )
        ).all()
        for preference in preferences:
            self._replace_reminders(weekend, preference)
            if changed and preference.schedule_change:
                self._create(
                    preference,
                    weekend,
                    AlertType.SCHEDULE_CHANGE,
                    f"change:{weekend.version}",
                    self.now,
                    None,
                    None,
                    f"{weekend.event_name} schedule changed",
                    weekend.schedule["change_reason"],
                )

    def refresh_missing_fantasy_reminders(self) -> None:
        """Pick up Fantasy sessions imported after a weekend was followed."""
        followers = self.db.execute(
            select(WeekendAlertPreference, CalendarWeekend)
            .join(
                CalendarWeekend,
                WeekendAlertPreference.weekend_id == CalendarWeekend.id,
            )
            .where(
                WeekendAlertPreference.fantasy_deadline.is_(True),
                CalendarWeekend.meeting_id.is_not(None),
            )
        ).all()
        for preference, weekend in followers:
            self._fantasy_reminders(weekend, preference)
        self.db.commit()

    def _replace_reminders(
        self, weekend: CalendarWeekend, preference: WeekendAlertPreference
    ) -> None:
        pending = self.db.scalars(
            select(Alert).where(
                Alert.user_id == preference.user_id,
                Alert.weekend_id == weekend.id,
                Alert.status == AlertStatus.PENDING,
                Alert.alert_type.in_(
                    [AlertType.SESSION_SOON, AlertType.FANTASY_DEADLINE]
                ),
            )
        ).all()
        for alert in pending:
            alert.status = AlertStatus.CANCELLED

        schedule = weekend.schedule
        if schedule["status"] in {"CANCELLED", "POSTPONED"}:
            return
        for session in schedule["sessions"]:
            identifier = session["identifier"]
            if session["status"] in {
                "CANCELLED",
                "POSTPONED",
                "COMPLETED",
                "IN_PROGRESS",
            }:
                continue
            start = session["starts_at"]
            if not start:
                continue
            starts_at = utc(
                datetime.fromisoformat(start.replace("Z", "+00:00"))
            )
            if preference.session_soon:
                self._reminder(
                    preference,
                    weekend,
                    AlertType.SESSION_SOON,
                    identifier,
                    starts_at,
                    30,
                    f"{session['name']} starts soon",
                    f"{weekend.event_name}: {session['name']} starts in 30 minutes.",
                )
        self._fantasy_reminders(weekend, preference)

    def _fantasy_reminders(
        self, weekend: CalendarWeekend, preference: WeekendAlertPreference
    ) -> None:
        if not preference.fantasy_deadline or not weekend.meeting_id:
            return
        schedule = weekend.schedule
        if schedule["status"] in {"CANCELLED", "POSTPONED"}:
            return
        deadlines = fantasy_schedule(self.db, weekend.meeting_id)
        imported = {
            calendar_identifier(identifier)
            for identifier in self.db.scalars(
                select(RaceSession.session_identifier).where(
                    RaceSession.meeting_id == weekend.meeting_id
                )
            )
        }
        if "R" not in imported:
            return
        for session in schedule["sessions"]:
            if session["status"] in {
                "CANCELLED",
                "POSTPONED",
                "COMPLETED",
                "IN_PROGRESS",
            }:
                continue
            if session["identifier"] not in imported or session[
                "identifier"
            ] not in {"FP1", "FP2", "FP3", "Q", "R"}:
                continue
            deadline = deadlines.get(session["identifier"])
            if deadline is not None:
                self._reminder(
                    preference,
                    weekend,
                    AlertType.FANTASY_DEADLINE,
                    session["identifier"],
                    utc(deadline),
                    60,
                    "Fantasy predictions close soon",
                    f"{weekend.event_name}: {session['name']} predictions close in one hour.",
                )

    def _reminder(
        self,
        preference: WeekendAlertPreference,
        weekend: CalendarWeekend,
        kind: AlertType,
        identifier: str,
        event_at: datetime,
        lead_minutes: int,
        title: str,
        body: str,
    ) -> None:
        if event_at <= self.now:
            return
        scheduled_for = max(
            self.now, event_at - timedelta(minutes=lead_minutes)
        )
        self._create(
            preference,
            weekend,
            kind,
            f"{identifier}:{event_at.isoformat()}",
            scheduled_for,
            event_at,
            identifier,
            title,
            body,
        )

    def _create(
        self,
        preference: WeekendAlertPreference,
        weekend: CalendarWeekend,
        kind: AlertType,
        source: str,
        scheduled_for: datetime,
        event_at: datetime | None,
        identifier: str | None,
        title: str,
        body: str,
    ) -> None:
        key = f"{preference.user_id}:{weekend.id}:{kind.value}:{source}"
        alert = self.db.scalar(
            select(Alert).where(Alert.idempotency_key == key)
        )
        if alert is None:
            try:
                with self.db.begin_nested():
                    self.db.add(
                        Alert(
                            user_id=preference.user_id,
                            weekend_id=weekend.id,
                            alert_type=kind,
                            status=AlertStatus.PENDING,
                            idempotency_key=key,
                            session_identifier=identifier,
                            title=title,
                            body=body,
                            scheduled_for=scheduled_for,
                            event_at=event_at,
                        )
                    )
                    self.db.flush()
            except IntegrityError:
                alert = self.db.scalar(
                    select(Alert).where(Alert.idempotency_key == key)
                )
        if alert is not None and alert.status == AlertStatus.CANCELLED:
            alert.status = AlertStatus.PENDING
            alert.scheduled_for = scheduled_for

    def deliver_due(self, *, limit: int = 100) -> int:
        identifiers = self.db.scalars(
            select(Alert.id)
            .where(
                Alert.status == AlertStatus.PENDING,
                Alert.scheduled_for <= self.now,
            )
            .order_by(Alert.scheduled_for)
            .limit(limit)
        ).all()
        delivered = 0
        for alert_id in identifiers:
            alert = self.db.scalar(
                select(Alert)
                .where(Alert.id == alert_id)
                .with_for_update(skip_locked=True)
            )
            if alert is None or alert.status != AlertStatus.PENDING:
                continue
            preference = self.db.scalar(
                select(WeekendAlertPreference).where(
                    WeekendAlertPreference.user_id == alert.user_id,
                    WeekendAlertPreference.weekend_id == alert.weekend_id,
                )
            )
            enabled = (
                preference is not None
                and {
                    AlertType.SESSION_SOON: preference.session_soon,
                    AlertType.FANTASY_DEADLINE: preference.fantasy_deadline,
                    AlertType.SCHEDULE_CHANGE: preference.schedule_change,
                }[alert.alert_type]
            )
            try:
                sent = False
                if not enabled or (
                    alert.event_at is not None
                    and utc(alert.event_at) <= self.now
                ):
                    alert.status = AlertStatus.CANCELLED
                else:
                    self.push.send(alert)
                    alert.status = AlertStatus.DELIVERED
                    alert.delivered_at = self.now
                    sent = True
                self.db.commit()
                delivered += int(sent)
            except Exception:
                self.db.rollback()
                logger.exception(
                    "Alert delivery failed alert_id=%s; worker will retry.",
                    alert_id,
                )
        return delivered

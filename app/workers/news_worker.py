"""Collect official publisher feeds once for every app user.

Historical metadata is imported using the companion CLI. Public RSS feeds only
contain a rolling window, so successful RSS checks never claim a full backfill.
"""

import argparse
from datetime import UTC, datetime, timedelta
import logging
import time
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.models.calendar import CalendarWeekend
from app.models.news import NewsWorkerLease
from app.providers.news_provider import (
    NewsFeedProvider,
    PublisherError,
    SOURCES,
)
from app.services.news_service import NewsService, utc

logger = logging.getLogger(__name__)


def acquire_lease(db: Session, owner: str, now: datetime) -> bool:
    result = db.execute(
        update(NewsWorkerLease)
        .where(
            NewsWorkerLease.name == "paddock-news",
            or_(
                NewsWorkerLease.expires_at < now,
                NewsWorkerLease.owner == owner,
            ),
        )
        .values(owner=owner, expires_at=now + timedelta(minutes=5))
    )
    if result.rowcount:
        db.commit()
        return True
    if db.get(NewsWorkerLease, "paddock-news") is not None:
        db.rollback()
        return False
    db.add(
        NewsWorkerLease(
            name="paddock-news",
            owner=owner,
            expires_at=now + timedelta(minutes=5),
        )
    )
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def release_lease(db: Session, owner: str):
    db.execute(
        update(NewsWorkerLease)
        .where(
            NewsWorkerLease.name == "paddock-news",
            NewsWorkerLease.owner == owner,
        )
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db.commit()


def sync_once(db: Session, provider: NewsFeedProvider) -> int:
    inserted = 0
    for source_id in SOURCES:
        checked = datetime.now(UTC)
        service = NewsService(db)
        state = service.source_state(source_id)
        retry_after = state.archive.get("feed_retry_after")
        if retry_after:
            try:
                if utc(datetime.fromisoformat(retry_after)) > checked:
                    continue
            except (ValueError, TypeError):
                pass
        try:
            items = provider.fetch(source_id)
            inserted += service.ingest(items, now=datetime.now(UTC))
            state.last_success_at = datetime.now(UTC)
            state.status = "CURRENT"
            state.message = "Publisher RSS feed checked successfully."
            state.archive = {
                key: value
                for key, value in state.archive.items()
                if key != "feed_retry_after"
            }
        except PublisherError as error:
            # Preserve previously imported stories and successful-check time.
            state.status = "ERROR"
            state.message = str(error)[:500]
            if error.retry_after:
                state.archive = {
                    **state.archive,
                    "feed_retry_after": error.retry_after.isoformat(),
                }
            logger.warning("News source %s: %s", source_id, error)
        state.last_checked_at = checked
        db.commit()
    return inserted


def interval_seconds(db: Session, now: datetime) -> int:
    """Two minutes during session weekends; ten minutes on other days."""
    for weekend in db.scalars(
        select(CalendarWeekend).where(
            CalendarWeekend.year == now.year,
        )
    ).all():
        if weekend.schedule.get("status") in {"CANCELLED", "POSTPONED"}:
            continue
        sessions = weekend.schedule.get("sessions", [])
        dates = []
        for session in sessions:
            if session.get("status") in {"CANCELLED", "POSTPONED"}:
                continue
            try:
                start = session.get("starts_at")
                if start:
                    dates.append(
                        utc(
                            datetime.fromisoformat(
                                start.replace("Z", "+00:00")
                            )
                        )
                    )
            except (TypeError, ValueError):
                continue
        if dates and min(dates) - timedelta(hours=12) <= now <= max(
            dates
        ) + timedelta(hours=6):
            return 120
    return 600


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    owner = uuid4().hex
    provider = NewsFeedProvider()
    try:
        while True:
            interval = 600
            if settings.news_sync_enabled:
                try:
                    with SessionLocal() as db:
                        if acquire_lease(db, owner, datetime.now(UTC)):
                            try:
                                count = sync_once(db, provider)
                                logger.info(
                                    "Collected %s new publisher headlines",
                                    count,
                                )
                                interval = interval_seconds(
                                    db, datetime.now(UTC)
                                )
                            finally:
                                release_lease(db, owner)
                except SQLAlchemyError:
                    # Never log the database DSN or abandon the collector on an outage.
                    logger.warning(
                        "News database unavailable; collector will retry."
                    )
                    interval = 120
            if args.once:
                return
            time.sleep(interval)
    finally:
        provider.close()


if __name__ == "__main__":
    main()

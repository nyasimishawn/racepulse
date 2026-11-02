"""Searchable publisher headline archive, independent from curated examples."""

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.curated_content import EditorialUpdate
from app.models.news import ImportedNews, NewsSourceState
from app.providers.news_provider import NewsItem, SOURCES, canonical_url
from app.schemas.news import NewsCoverage, NewsPage, NewsSync
from app.services.profile_content_service import EditorialContentService

SEASON_START = datetime(2026, 1, 1, tzinfo=UTC)


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


class NewsService:
    def __init__(self, db: Session):
        self.db = db

    def source_state(self, source_id: str) -> NewsSourceState:
        state = self.db.get(NewsSourceState, source_id)
        if state is None:
            state = NewsSourceState(
                source_id=source_id,
                publisher=SOURCES[source_id].publisher,
                status="PENDING",
                message="Waiting for the first source check.",
                archive={},
            )
            self.db.add(state)
            self.db.flush()
        return state

    def ingest(
        self, items: list[NewsItem], *, now: datetime | None = None
    ) -> int:
        """One transaction per batch. The worker lease prevents concurrent imports."""
        now = utc(now or datetime.now(UTC))
        inserted = 0
        for item in items:
            published = utc(item.published_at)
            if not SEASON_START <= published <= now:
                continue
            key = url_hash(item.url)
            imported = self.db.get(ImportedNews, key)
            if imported is not None:
                imported.last_seen_at = now
                update = self.db.get(EditorialUpdate, imported.editorial_id)
                if update is not None:
                    # Respect an editor's subsequent correction rather than overwrite it.
                    if update.updated_by_profile_id is None:
                        update.title = item.title[:240]
                        update.published_at = published
                        update.context = {
                            **(update.context or {}),
                            "category": category_for(item.title),
                        }
                        flags = [
                            flag
                            for flag in update.data_quality_flags
                            if flag != "RUMOUR"
                        ]
                        if any(
                            word in item.title.casefold()
                            for word in ("rumour", "rumor", "speculation")
                        ):
                            flags.append("RUMOUR")
                        update.data_quality_flags = flags
                continue
            self.source_state(item.source_id)
            flags = ["PUBLISHER_HEADLINE_ONLY"]
            lower = item.title.casefold()
            if any(
                word in lower for word in ("rumour", "rumor", "speculation")
            ):
                flags.append("RUMOUR")
            update = EditorialUpdate(
                id=uuid4(),
                update_type="TEAM_UPDATE",
                publication_status="PUBLISHED",
                title=item.title[:240],
                body=f"Published by {item.publisher}. Open the original report for details.",
                source_url=canonical_url(item.url),
                publisher=item.publisher,
                published_at=published,
                confidence="MEDIUM",
                data_quality_flags=flags,
                context={
                    "category": category_for(item.title),
                    "evidence": "REPORTED",
                    "summary": "",
                    "why_it_matters": "",
                },
            )
            self.db.add(update)
            self.db.flush()
            self.db.add(
                ImportedNews(
                    url_hash=key,
                    editorial_id=update.id,
                    source_id=item.source_id,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            # autoflush is disabled in this application's SessionLocal.
            self.db.flush()
            inserted += 1
        self.db.commit()
        return inserted

    def page(
        self,
        *,
        since: datetime,
        until: datetime | None,
        query: str,
        order: str,
        cursor: str | None,
        limit: int,
        now: datetime | None = None,
    ) -> NewsPage:
        now = utc(now or datetime.now(UTC))
        since = utc(since)
        until = utc(until) if until else None
        if since < SEASON_START:
            raise ValueError("The Paddock archive begins on 1 January 2026.")
        if until is not None and until <= since:
            raise ValueError(
                "until must be later than since (exclusive UTC end)."
            )
        if order not in {"oldest", "newest"}:
            raise ValueError("Unknown news sort order.")
        normalized_query = " ".join(query.strip().split()).casefold()
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    since.isoformat(),
                    until.isoformat() if until else None,
                    normalized_query,
                    order,
                ]
            ).encode()
        ).hexdigest()
        snapshot = now
        cursor_date = cursor_id = None
        if cursor:
            try:
                decoded = json.loads(
                    base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                )
                if decoded["filter"] != fingerprint:
                    raise ValueError
                snapshot = utc(datetime.fromisoformat(decoded["snapshot"]))
                cursor_date = utc(datetime.fromisoformat(decoded["published"]))
                cursor_id = UUID(decoded["id"])
                if snapshot > now + timedelta(seconds=1):
                    raise ValueError
            except (
                ValueError,
                TypeError,
                KeyError,
                json.JSONDecodeError,
            ) as error:
                raise ValueError(
                    "Invalid news cursor; restart this search."
                ) from error

        filters = [
            EditorialUpdate.publication_status == "PUBLISHED",
            EditorialUpdate.published_at >= since,
            EditorialUpdate.published_at <= snapshot,
            ImportedNews.first_seen_at <= snapshot,
        ]
        if until is not None:
            filters.append(EditorialUpdate.published_at < until)
        # Each word must occur in the headline, publisher, or canonical URL.
        # Treat %, _ and backslashes as literal search text, not SQL wildcards.
        for token in normalized_query.split()[:12]:
            escaped = (
                token.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            filters.append(
                or_(
                    EditorialUpdate.title.ilike(pattern, escape="\\"),
                    EditorialUpdate.publisher.ilike(pattern, escape="\\"),
                    EditorialUpdate.source_url.ilike(pattern, escape="\\"),
                )
            )
        base = (
            select(EditorialUpdate)
            .join(
                ImportedNews,
                ImportedNews.editorial_id == EditorialUpdate.id,
            )
            .where(*filters)
        )
        total = (
            self.db.scalar(select(func.count()).select_from(base.subquery()))
            or 0
        )
        if cursor_date is not None:
            if order == "newest":
                base = base.where(
                    or_(
                        EditorialUpdate.published_at < cursor_date,
                        and_(
                            EditorialUpdate.published_at == cursor_date,
                            EditorialUpdate.id < cursor_id,
                        ),
                    )
                )
            else:
                base = base.where(
                    or_(
                        EditorialUpdate.published_at > cursor_date,
                        and_(
                            EditorialUpdate.published_at == cursor_date,
                            EditorialUpdate.id > cursor_id,
                        ),
                    )
                )
        sort = [EditorialUpdate.published_at, EditorialUpdate.id]
        if order == "newest":
            sort = [column.desc() for column in sort]
        rows = self.db.scalars(base.order_by(*sort).limit(limit + 1)).all()
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "filter": fingerprint,
                            "snapshot": snapshot.isoformat(),
                            "published": utc(
                                rows[-1].published_at
                            ).isoformat(),
                            "id": str(rows[-1].id),
                        }
                    ).encode()
                )
                .decode()
                .rstrip("=")
            )
        coverage, sync = self.health(now)
        return NewsPage(
            items=[EditorialContentService._response(row) for row in rows],
            next_cursor=next_cursor,
            total=total,
            coverage=coverage,
            sync=sync,
        )

    def health(self, now: datetime) -> tuple[NewsCoverage, NewsSync]:
        states = list(self.db.scalars(select(NewsSourceState)).all())
        earliest, latest, count = self.db.execute(
            select(
                func.min(EditorialUpdate.published_at),
                func.max(EditorialUpdate.published_at),
                func.count(func.distinct(ImportedNews.source_id)),
            )
            .join(
                ImportedNews, ImportedNews.editorial_id == EditorialUpdate.id
            )
            .where(
                EditorialUpdate.publication_status == "PUBLISHED",
                EditorialUpdate.published_at <= now,
            )
        ).one()
        archives = [
            state.archive
            for state in states
            if "metadata_count" in state.archive
        ]
        complete = any(archive.get("complete") is True for archive in archives)
        backfill = (
            "COMPLETE"
            if complete
            else (
                "PARTIAL"
                if any(
                    archive.get("status") == "PARTIAL" for archive in archives
                )
                else "IN_PROGRESS"
                if archives
                else "PENDING"
            )
        )
        checked = [utc(s.last_checked_at) for s in states if s.last_checked_at]
        success = [utc(s.last_success_at) for s in states if s.last_success_at]
        last_checked = max(checked, default=None)
        last_success = max(success, default=None)
        failed = any(s.status == "ERROR" for s in states)
        if not settings.news_sync_enabled:
            status = "DISABLED"
            message = "Automatic source checks are disabled. Saved stories remain available."
        elif failed:
            status = "DEGRADED"
            message = "A publisher could not be checked. Previously collected stories remain available."
        elif last_success is None:
            status = "PENDING"
            message = "Waiting for the news worker to check the publishers."
        elif now - last_success > timedelta(minutes=25):
            status = "STALE"
            message = "Source checks are overdue. Showing the saved publisher archive."
        else:
            status = "CURRENT"
            message = "Publisher feeds checked automatically; publication times come from the sources."
        return NewsCoverage(
            start=SEASON_START,
            earliest=utc(earliest) if earliest else None,
            latest=utc(latest) if latest else None,
            backfill_status=backfill,
            complete=complete,
            source_count=count,
            scope="Motorsport.com's available 2026 Formula 1 archive, plus latest Autosport headlines.",
        ), NewsSync(
            last_success_at=last_success,
            last_checked_at=last_checked,
            status=status,
            message=message,
        )


def category_for(title: str) -> str:
    lower = title.casefold()
    if re.search(r"\b(?:tyres?|tires?|pirelli|compounds?)\b", lower):
        return "TYRES"
    if any(
        term in lower
        for term in (
            "contract",
            "driver market",
            "seat",
            "signs",
            "line-up",
            "lineup",
            "extends",
        )
    ):
        return "DRIVER_MARKET"
    if any(
        term in lower
        for term in (
            "upgrade",
            "aerodynamic",
            "front wing",
            "floor",
            "power unit",
            "engine",
            "regulation",
        )
    ):
        return "TECHNICAL"
    if any(
        term in lower
        for term in (
            "grand prix",
            " gp",
            "qualifying",
            "practice",
            "sprint",
            "race",
            "penalty",
        )
    ):
        return "WEEKEND"
    return "GENERAL"

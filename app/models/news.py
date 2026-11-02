"""Publisher metadata archive; original article content stays with publishers."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ImportedNews(Base):
    __tablename__ = "imported_news"

    # A canonical URL hash is the ingestion idempotency key across RSS/archive.
    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    editorial_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("editorial_updates.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    source_id: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class NewsSourceState(Base):
    __tablename__ = "news_source_state"

    source_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    publisher: Mapped[str] = mapped_column(String(255), nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="PENDING",
    )
    message: Mapped[str] = mapped_column(
        String(500), default="", nullable=False
    )
    # Completed sitemap URLs and monthly listing progress survive restarts.
    archive: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class NewsWorkerLease(Base):
    __tablename__ = "news_worker_lease"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

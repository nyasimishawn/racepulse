from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.editorial import EditorialUpdateResponse


class NewsCoverage(BaseModel):
    start: datetime
    earliest: datetime | None
    latest: datetime | None
    backfill_status: Literal["PENDING", "IN_PROGRESS", "COMPLETE", "PARTIAL"]
    complete: bool
    source_count: int
    scope: str = "Available Formula 1 headlines from the connected publishers."


class NewsSync(BaseModel):
    last_success_at: datetime | None
    last_checked_at: datetime | None
    status: Literal["PENDING", "CURRENT", "STALE", "DEGRADED", "DISABLED"]
    message: str


class NewsPage(BaseModel):
    items: list[EditorialUpdateResponse]
    next_cursor: str | None
    total: int
    coverage: NewsCoverage
    sync: NewsSync

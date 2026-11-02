from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.news import NewsPage
from app.services.news_service import NewsService

router = APIRouter(prefix="/editorial/news", tags=["Paddock news"])


@router.get("", response_model=NewsPage)
def list_news(
    since: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    until: datetime | None = None,
    q: str = Query(default="", max_length=160),
    order: Literal["newest", "oldest"] = "newest",
    cursor: str | None = Query(default=None, max_length=1800),
    limit: int = Query(default=24, ge=1, le=100),
    db: Session = Depends(get_db),
) -> NewsPage:
    try:
        return NewsService(db).page(
            since=since,
            until=until,
            query=q,
            order=order,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

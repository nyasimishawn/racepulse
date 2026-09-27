from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.dashboard import DashboardTrack, TrackDashboardResponse
from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/tracks", response_model=list[DashboardTrack])
def list_dashboard_tracks(db: Session = Depends(get_db)):
    return DashboardService(db).tracks()


@router.get("/tracks/{meeting_id}", response_model=TrackDashboardResponse)
def get_track_dashboard(
    meeting_id: UUID,
    season_year: int = Query(ge=1950, le=2100),
    as_of: datetime | None = None,
    db: Session = Depends(get_db),
):
    if as_of is not None and as_of.tzinfo is None:
        raise HTTPException(422, "as_of must include a timezone.")
    try:
        return DashboardService(db).dashboard(meeting_id, season_year, as_of)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error

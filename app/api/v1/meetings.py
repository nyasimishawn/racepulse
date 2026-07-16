from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.session import SessionSummaryResponse
from app.services.session_query_service import SessionQueryService

router = APIRouter(prefix="/meetings", tags=["Meetings"])


@router.get(
    "/{meeting_id}/sessions",
    response_model=list[SessionSummaryResponse],
    summary="List all imported sessions for one Grand Prix weekend",
)
def list_meeting_sessions(
    meeting_id: UUID,
    db: Session = Depends(get_db),
) -> list[SessionSummaryResponse]:
    service = SessionQueryService(db)

    if not service.meeting_exists(meeting_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found.",
        )

    return service.list_meeting_sessions(meeting_id)
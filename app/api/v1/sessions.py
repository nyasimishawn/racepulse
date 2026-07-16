from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.session import (
    SessionDetailResponse,
    SessionSummaryResponse,
    TimingTowerRowResponse,
)
from app.services.session_query_service import SessionQueryService

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.get(
    "",
    response_model=list[SessionSummaryResponse],
    summary="List imported sessions",
)
def list_sessions(
    year: int | None = Query(default=None, ge=2018, le=2100),
    session_type: str | None = Query(
        default=None,
        min_length=1,
        max_length=50,
    ),
    db: Session = Depends(get_db),
) -> list[SessionSummaryResponse]:
    return SessionQueryService(db).list_sessions(
        year=year,
        session_type=session_type,
    )


@router.get(
    "/{session_id}",
    response_model=SessionDetailResponse,
    summary="Get an imported session and its results",
)
def get_session(
    session_id: UUID,
    db: Session = Depends(get_db),
) -> SessionDetailResponse:
    session = SessionQueryService(db).get_session_detail(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Race session not found.",
        )

    return session


@router.get(
    "/{session_id}/timing-tower",
    response_model=list[TimingTowerRowResponse],
    summary="Get the session timing tower",
)
def get_timing_tower(
    session_id: UUID,
    db: Session = Depends(get_db),
) -> list[TimingTowerRowResponse]:
    service = SessionQueryService(db)

    if service.get_session_detail(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Race session not found.",
        )

    return service.get_timing_tower(session_id)
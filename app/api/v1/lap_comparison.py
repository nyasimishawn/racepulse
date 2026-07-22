from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.lap_comparison import LapComparisonResponse
from app.schemas.telemetry_overlay import TelemetryOverlayResponse
from app.services.lap_comparison_service import (
    ComparisonLapNotFoundError,
    ComparisonSessionNotFoundError,
    CrossMeetingComparisonError,
    LapComparisonService,
    MeaninglessLapComparisonError,
)
from app.services.telemetry_overlay_service import (
    TelemetryOverlayService,
)

router = APIRouter(
    prefix="/lap-comparison",
    tags=["Lap Comparison"],
)


@router.get(
    "/telemetry-overlay",
    response_model=TelemetryOverlayResponse,
    summary="Compare telemetry traces across two stored laps",
)
def get_telemetry_overlay(
    reference_session_id: UUID = Query(...),
    reference_driver_number: str = Query(..., min_length=1),
    reference_lap_number: int = Query(..., ge=1),
    target_session_id: UUID = Query(...),
    target_driver_number: str = Query(..., min_length=1),
    target_lap_number: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> TelemetryOverlayResponse:
    try:
        return TelemetryOverlayService(db).get_overlay(
            reference_session_id=reference_session_id,
            reference_driver_number=reference_driver_number,
            reference_lap_number=reference_lap_number,
            target_session_id=target_session_id,
            target_driver_number=target_driver_number,
            target_lap_number=target_lap_number,
        )

    except (
        ComparisonSessionNotFoundError,
        ComparisonLapNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except (
        CrossMeetingComparisonError,
        MeaninglessLapComparisonError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error


@router.get(
    "",
    response_model=LapComparisonResponse,
    summary="Compare two stored driver laps",
)
def compare_laps(
    reference_session_id: UUID = Query(...),
    reference_driver_number: str = Query(..., min_length=1),
    reference_lap_number: int = Query(..., ge=1),
    target_session_id: UUID = Query(...),
    target_driver_number: str = Query(..., min_length=1),
    target_lap_number: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> LapComparisonResponse:
    try:
        return LapComparisonService(db).compare(
            reference_session_id=reference_session_id,
            reference_driver_number=reference_driver_number,
            reference_lap_number=reference_lap_number,
            target_session_id=target_session_id,
            target_driver_number=target_driver_number,
            target_lap_number=target_lap_number,
        )

    except (
        ComparisonSessionNotFoundError,
        ComparisonLapNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except (
        CrossMeetingComparisonError,
        MeaninglessLapComparisonError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
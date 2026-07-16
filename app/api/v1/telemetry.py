from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.telemetry import (
    TelemetryImportResponse,
    TelemetryPointResponse,
)
from app.services.telemetry_service import (
    StoredLapNotFoundError,
    TelemetryImportError,
    TelemetryImportService,
    TelemetryQueryService,
    TelemetryUnavailableError,
)

router = APIRouter(
    prefix=(
        "/sessions/{race_session_id}/drivers/{driver_number}"
        "/laps/{lap_number}/telemetry"
    ),
    tags=["Telemetry"],
)


@router.post(
    "/import",
    response_model=TelemetryImportResponse,
    summary="Import telemetry for one driver lap",
)
def import_telemetry(
    race_session_id: UUID,
    driver_number: str,
    lap_number: int,
    db: Session = Depends(get_db),
) -> TelemetryImportResponse:
    try:
        telemetry_points_upserted = TelemetryImportService(
            db
        ).import_lap_telemetry(
            race_session_id=race_session_id,
            driver_number=driver_number,
            lap_number=lap_number,
        )

        return TelemetryImportResponse(
            race_session_id=race_session_id,
            driver_number=driver_number,
            lap_number=lap_number,
            telemetry_points_upserted=telemetry_points_upserted,
        )

    except StoredLapNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error

    except TelemetryUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    except TelemetryImportError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error


@router.get(
    "",
    response_model=list[TelemetryPointResponse],
    summary="Get imported telemetry for one driver lap",
)
def get_telemetry(
    race_session_id: UUID,
    driver_number: str,
    lap_number: int,
    limit: int = Query(default=2000, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> list[TelemetryPointResponse]:
    return TelemetryQueryService(db).list_points(
        race_session_id=race_session_id,
        driver_number=driver_number,
        lap_number=lap_number,
        limit=limit,
    )
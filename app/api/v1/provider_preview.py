from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query, status

from app.providers.base_provider import ProviderError
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.provider import FastF1SessionPreviewResponse

router = APIRouter(
    prefix="/providers/fastf1",
    tags=["Data Providers"],
)


@lru_cache
def get_fastf1_provider() -> FastF1Provider:
    return FastF1Provider()


@router.get(
    "/session-preview",
    response_model=FastF1SessionPreviewResponse,
    summary="Preview a FastF1 session",
)
def preview_fastf1_session(
    year: int = Query(ge=2018, le=2100, examples=[2024]),
    event_name: str = Query(min_length=2, examples=["Bahrain"]),
    session_identifier: str = Query(default="R", examples=["R"]),
) -> FastF1SessionPreviewResponse:
    try:
        preview = get_fastf1_provider().get_session_preview(
            year=year,
            event_name=event_name,
            session_identifier=session_identifier,
        )
    except ProviderError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error

    return FastF1SessionPreviewResponse(
        source=preview.source,
        year=preview.year,
        meeting_name=preview.meeting_name,
        official_meeting_name=preview.official_meeting_name,
        session_name=preview.session_name,
        session_identifier=preview.session_identifier,
        event_date=preview.event_date,
        country_name=preview.country_name,
        location=preview.location,
        drivers=[
            {
                "driver_number": driver.driver_number,
                "abbreviation": driver.abbreviation,
                "full_name": driver.full_name,
                "team_name": driver.team_name,
                "team_colour": driver.team_colour,
                "country_code": driver.country_code,
                "classified_position": driver.classified_position,
            }
            for driver in preview.drivers
        ],
    )
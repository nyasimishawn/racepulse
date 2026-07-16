from fastapi import APIRouter

from app.api.v1 import (
    health,
    import_jobs,
    laps,
    meetings,
    provider_preview,
    sessions,
    qualifying,
    attack_index,
    telemetry,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(import_jobs.router)
api_router.include_router(provider_preview.router)
api_router.include_router(sessions.router)
api_router.include_router(meetings.router)
api_router.include_router(qualifying.router)
api_router.include_router(laps.router)
api_router.include_router(telemetry.router)
api_router.include_router(attack_index.router)
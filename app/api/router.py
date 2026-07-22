from fastapi import APIRouter

from app.api.v1 import (
    attack_index,
    health,
    import_jobs,
    insights,
    lap_comparison,
    laps,
    meetings,
    provider_preview,
    push_manage_timeline,
    race_engineering,
    qualifying,
    race_context,
    replay,
    replay_rooms,
    session_map_imports,
    sessions,
    strategy,
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
api_router.include_router(strategy.router)
api_router.include_router(insights.router)
api_router.include_router(push_manage_timeline.router)
api_router.include_router(lap_comparison.router)
api_router.include_router(race_context.router)
api_router.include_router(race_engineering.router)
api_router.include_router(replay.router)
api_router.include_router(replay_rooms.router)

api_router.include_router(session_map_imports.session_router)
api_router.include_router(session_map_imports.job_router)

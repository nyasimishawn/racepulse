from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    auth,
    attack_index,
    editorial,
    fantasy,
    head_to_head,
    health,
    import_jobs,
    insights,
    jobs,
    lap_comparison,
    laps,
    meetings,
    provider_preview,
    profiles,
    push_manage_timeline,
    qualifying,
    race_context,
    race_engineering,
    replay,
    replay_rooms,
    session_map_imports,
    session_telemetry_imports,
    sessions,
    strategy,
    telemetry,
    users,
    weekends,
)


api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(weekends.router)
api_router.include_router(jobs.router)
api_router.include_router(import_jobs.router)
api_router.include_router(provider_preview.router)
api_router.include_router(sessions.router)
api_router.include_router(meetings.router)
api_router.include_router(profiles.router)
api_router.include_router(qualifying.router)
api_router.include_router(laps.router)
api_router.include_router(telemetry.router)
api_router.include_router(accounts.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(attack_index.router)
api_router.include_router(strategy.router)
api_router.include_router(insights.router)
api_router.include_router(push_manage_timeline.router)
api_router.include_router(lap_comparison.router)
api_router.include_router(head_to_head.router)
api_router.include_router(race_context.router)
api_router.include_router(race_engineering.router)
api_router.include_router(replay.router)
api_router.include_router(replay_rooms.router)
api_router.include_router(editorial.router)

api_router.include_router(session_map_imports.session_router)
api_router.include_router(session_map_imports.job_router)

api_router.include_router(fantasy.router)

api_router.include_router(session_telemetry_imports.session_router)
api_router.include_router(session_telemetry_imports.job_router)

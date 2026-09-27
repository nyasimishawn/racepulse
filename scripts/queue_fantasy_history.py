"""Queue every 2025 weekend and 2026 weekends through Monza.

Run from the backend root with ``.venv/Scripts/python scripts/queue_fantasy_history.py``.
Selections are idempotent; the durable worker performs the full session import.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fastf1

from app.core.redis import create_sync_redis_client
from app.db.database import SessionLocal
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.weekend import WeekendSelectionRequest
from app.services.weekend_download_service import WeekendDownloadService
from app.workers.job_queue import RedisStreamJobPublisher


def main() -> None:
    provider = FastF1Provider()
    redis = create_sync_redis_client()
    redis.ping()
    publisher = RedisStreamJobPublisher(redis)
    publisher.ensure_consumer_group()
    db = SessionLocal()
    try:
        service = WeekendDownloadService(db, provider=provider, publisher=publisher)
        for year, last_round in ((2025, None), (2026, 13)):
            schedule = fastf1.get_event_schedule(year, include_testing=False)
            events = schedule[schedule["RoundNumber"] > 0]
            if last_round is not None:
                events = events[events["RoundNumber"] <= last_round]
            for _, event in events.sort_values("RoundNumber").iterrows():
                response = service.select_weekend(
                    WeekendSelectionRequest(
                        year=year, event_name=str(event["EventName"])
                    )
                )
                print(
                    f"{year} round {response.round_number:02d} "
                    f"{response.event_name}: {response.status} "
                    f"({len(response.sessions)} sessions)",
                    flush=True,
                )
    finally:
        db.close()
        redis.close()


if __name__ == "__main__":
    main()

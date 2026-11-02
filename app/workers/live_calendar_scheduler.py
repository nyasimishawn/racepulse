"""Supervise live refreshes without blocking requests or backend shutdown."""

import asyncio
import logging
import os
from pathlib import Path
import subprocess
import sys

from app.core.config import settings


logger = logging.getLogger(__name__)


async def _stop_process(process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def refresh_live_season(stop: asyncio.Event) -> int | None:
    # Isolate the network collector so an API restart can stop it promptly.
    # PostgreSQL owns synchronization and all durable data.
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "app.workers.calendar_sync_worker", "--once",
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **options,
    )
    completed = asyncio.create_task(process.wait())
    stopped = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({completed, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if completed.done():
            return completed.result()
        return None
    finally:
        await _stop_process(process)
        for task in (completed, stopped):
            if not task.done():
                task.cancel()
        await asyncio.gather(completed, stopped, return_exceptions=True)


async def run_live_calendar_scheduler(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            result = await refresh_live_season(stop)
            if result is not None:
                logger.info("Live calendar refresh exit_status=%s", result)
        except Exception as error:
            logger.warning(
                "Live calendar refresh failed error_type=%s", type(error).__name__
            )
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.calendar_sync_interval_seconds
            )
        except TimeoutError:
            pass

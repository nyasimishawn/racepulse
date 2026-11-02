import asyncio

import pytest

from app.workers import live_calendar_scheduler as scheduler


@pytest.mark.parametrize("in_flight", [False, True])
def test_refresh_stops_child_on_api_shutdown(monkeypatch, in_flight):
    launched = []

    async def scenario():
        stop = asyncio.Event()

        finished = asyncio.Event()

        class Process:
            returncode = None if in_flight else 0
            terminated = False

            async def wait(self):
                if self.returncode is None:
                    await finished.wait()
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = -15
                finished.set()

        process = Process()

        async def launch(*args, **kwargs):
            launched.append(args)
            asyncio.get_running_loop().call_soon(stop.set)
            return process

        monkeypatch.setattr(scheduler.asyncio, "create_subprocess_exec", launch)
        result = await scheduler.refresh_live_season(stop)
        assert process.terminated is in_flight
        if in_flight:
            assert result is None
        else:
            assert result == 0

    asyncio.run(scenario())
    assert launched[0][1:] == (
        "-m", "app.workers.calendar_sync_worker", "--once",
    )


def test_scheduler_keeps_provider_exception_details_out_of_logs(monkeypatch, caplog):
    async def scenario():
        stop = asyncio.Event()

        async def refresh(event):
            event.set()
            raise RuntimeError("private provider response")

        monkeypatch.setattr(scheduler, "refresh_live_season", refresh)
        await scheduler.run_live_calendar_scheduler(stop)

    asyncio.run(scenario())
    assert "error_type=RuntimeError" in caplog.text
    assert "private provider response" not in caplog.text

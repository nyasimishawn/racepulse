"""A bounded live timing snapshot, published immediately on every update."""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import json
from uuid import uuid4

from redis.exceptions import RedisError

from app.providers.openf1_provider import utc_date

SNAPSHOT_KEY = "racepulse:live:v1:snapshot"
CHANNEL = "racepulse:live:v1:updates"
LEASE_KEY = "racepulse:live:v1:collector"


def utc_now():
    return datetime.now(timezone.utc)


def empty_snapshot(status="waiting", message="No session is live right now."):
    return {
        "schema_version": 1,
        "epoch": "",
        "sequence": 0,
        "configured": True,
        "status": status,
        "message": message,
        "session": None,
        "drivers": [],
        "race_control": [],
        "weather": {},
        "last_received_at": None,
        "source_timestamp": None,
        "collector_updated_at": None,
    }


class LiveTimingState:
    def __init__(self, session: dict | None = None):
        self.state = empty_snapshot()
        self.state["epoch"] = uuid4().hex
        self.state["session"] = deepcopy(session)
        self.drivers: dict[int, dict] = {}
        self._versions: OrderedDict[tuple, tuple] = OrderedDict()

    def status(self, status: str, message: str):
        self.state.update(status=status, message=message)

    def apply(
        self, topic: str, row: dict, *, received_at=None, bootstrap=False
    ) -> bool:
        session = self.state["session"]
        if not session or row.get("session_key") != session.get("session_key"):
            return False
        number = row.get("driver_number")
        stamp = row.get("date") or row.get("date_start") or ""
        document = row.get("_key") or (
            f"{number}:{row.get('lap_number')}"
            if topic == "laps"
            else f"{number}:{row.get('stint_number')}"
            if topic == "stints"
            else str(number)
            if topic in ("drivers", "position", "intervals")
            else f"{stamp}:{number}:{row.get('message', '')}"
        )
        key = (topic, str(document))
        version = (int(row.get("_id") or 0), stamp)
        previous = self._versions.get(key)
        if previous is not None and version <= previous:
            return False
        self._versions[key] = version
        self._versions.move_to_end(key)
        if len(self._versions) > 5000:
            self._versions.popitem(last=False)

        # A newer document from an older lap/stint must not move the board back.
        if number is not None:
            number = int(number)
            driver = self.drivers.setdefault(number, {"driver_number": number})
            clocks = driver.setdefault("_clocks", {})
            clock = (
                int(row.get("lap_number") or 0)
                if topic in ("laps", "pit")
                else int(row.get("stint_number") or 0)
                if topic == "stints"
                else stamp
            )
            if topic in clocks and clock < clocks[topic]:
                return False
            clocks[topic] = clock
            fields = {
                "drivers": (
                    "full_name",
                    "name_acronym",
                    "team_name",
                    "team_colour",
                ),
                "position": ("position",),
                "intervals": ("interval", "gap_to_leader"),
                "laps": (
                    "lap_number",
                    "lap_duration",
                    "duration_sector_1",
                    "duration_sector_2",
                    "duration_sector_3",
                ),
                "stints": (
                    "compound",
                    "tyre_age_at_start",
                    "lap_start",
                    "stint_number",
                ),
                "pit": ("stop_duration", "lane_duration"),
            }.get(topic, ())
            if topic == "laps" and clock != driver.get("lap_number"):
                for field in (
                    "lap_duration",
                    "duration_sector_1",
                    "duration_sector_2",
                    "duration_sector_3",
                ):
                    driver[field] = None
            for field in fields:
                if field in row:
                    if (
                        topic == "laps"
                        and row[field] is None
                        and driver.get(field) is not None
                    ):
                        # An older queued partial lap must not erase a sector
                        # already filled by the REST reconnect snapshot.
                        continue
                    driver[field] = row[field]
            if topic == "laps" and row.get("lap_duration") is not None:
                driver["last_lap_duration"] = row["lap_duration"]
            if (
                driver.get("lap_number") is not None
                and driver.get("lap_start") is not None
            ):
                driver["tyre_age"] = max(
                    0, driver["lap_number"] - driver["lap_start"]
                ) + (driver.get("tyre_age_at_start") or 0)
            if topic == "pit":
                driver["last_pit_lap"] = row.get("lap_number")
        if topic == "race_control":
            # Updated versions replace the same document rather than duplicate it.
            controls = self.state["race_control"]
            controls[:] = [
                item
                for item in controls
                if item.get("_document") != str(document)
            ]
            controls.append(
                {
                    **{
                        field: row.get(field)
                        for field in (
                            "date",
                            "message",
                            "flag",
                            "category",
                            "lap_number",
                            "driver_number",
                        )
                    },
                    "_document": str(document),
                }
            )
            controls.sort(key=lambda item: item.get("date") or "")
            self.state["race_control"] = controls[-8:]
        elif topic == "weather":
            if stamp >= self.state["weather"].get("date", ""):
                self.state["weather"] = {
                    field: row.get(field)
                    for field in (
                        "date",
                        "air_temperature",
                        "track_temperature",
                        "rainfall",
                        "wind_speed",
                    )
                }
        elif topic == "sessions":
            session.update(row)
        if not bootstrap:
            self.state["last_received_at"] = (
                received_at or utc_now()
            ).isoformat()
            if topic not in ("drivers", "sessions"):
                self.status("live", "Receiving live timing from OpenF1.")
        if stamp > (self.state["source_timestamp"] or ""):
            self.state["source_timestamp"] = stamp
        return True

    def snapshot(self, *, now=None) -> dict:
        self.state["sequence"] += 1
        self.state["collector_updated_at"] = (now or utc_now()).isoformat()
        self.state["drivers"] = sorted(
            [
                {k: v for k, v in driver.items() if not k.startswith("_")}
                for driver in self.drivers.values()
            ],
            key=lambda driver: (
                driver.get("position") or 999,
                driver["driver_number"],
            ),
        )
        return deepcopy(self.state)


# Lease fencing and the snapshot/broadcast happen in one Redis operation. An old
# worker cannot publish after another worker takes over its expired lease.
PUBLISH = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('set', KEYS[2], ARGV[2], 'EX', 86400)
redis.call('publish', KEYS[3], ARGV[2])
if ARGV[3] ~= '' then
  redis.call('xadd', KEYS[4], 'MAXLEN', '~', 100000, '*', 'event', ARGV[3])
  redis.call('expire', KEYS[4], 604800)
end
return 1
"""
RENEW = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('expire', KEYS[1], 30)
"""
RELEASE = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('del', KEYS[1])
"""


async def publish(redis, state: LiveTimingState, owner: str, event=None):
    snapshot = state.snapshot()
    session_key = (snapshot.get("session") or {}).get("session_key", "none")
    result = await redis.eval(
        PUBLISH,
        4,
        LEASE_KEY,
        SNAPSHOT_KEY,
        CHANNEL,
        f"racepulse:live:v1:events:{session_key}",
        owner,
        json.dumps(snapshot),
        json.dumps(event) if event else "",
    )
    if not result:
        raise RuntimeError("Live collector lease lost")


async def read_snapshot(redis, *, configured: bool, now=None) -> dict:
    if not configured:
        result = empty_snapshot(
            "unavailable", "Live timing is not configured yet."
        )
        result["configured"] = False
        return result
    try:
        raw = await redis.get(SNAPSHOT_KEY) if redis else None
        result = (
            json.loads(raw)
            if raw
            else empty_snapshot(
                "connecting", "Waiting for the live timing collector."
            )
        )
    except (RedisError, ValueError, TypeError):
        return empty_snapshot(
            "unavailable", "Live timing is temporarily unavailable."
        )
    checked_at = now or utc_now()
    heartbeat = utc_date(result.get("collector_updated_at"))
    if heartbeat and (checked_at - heartbeat).total_seconds() > 35:
        result.update(
            status="reconnecting",
            message="The live timing collector is reconnecting.",
        )
    session_end = utc_date((result.get("session") or {}).get("date_end"))
    if session_end and (checked_at - session_end).total_seconds() > 1800:
        result.update(status="finished", message="This session has ended.")
    received = utc_date(result.get("last_received_at"))
    if result["status"] == "live" and (
        not received or (checked_at - received).total_seconds() > 30
    ):
        result.update(
            status="waiting",
            message="Connected; waiting for the next timing update.",
        )
    return result

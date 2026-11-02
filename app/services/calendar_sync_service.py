"""Synchronize published F1 structured schedules; never invent missing events."""

from datetime import UTC, datetime
import json
import re
import ssl

import httpx
from sqlalchemy import select, text, update

from app.models.calendar import CalendarSyncState, CalendarWeekend
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.schemas.calendar import CalendarWeekendInput
from app.services.calendar_service import CalendarService, calendar_identifier, utc
from app.services.official_f1_data import flight_records, normalized_name, objects

BASE = "https://www.formula1.com"
IDENTIFIERS = {
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
    "Qualifying": "Q",
    "Race": "R",
    "Sprint": "S",
    "Sprint Qualifying": "SQ",
    "Sprint Shootout": "SQ",
}


def parse_event(html: str, url: str, year: int, now: datetime):
    documents = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.S,
    )
    event = None
    for raw in documents:
        item = json.loads(raw)
        if (
            isinstance(item, dict)
            and item.get("@id") == url
            and item.get("subEvent")
        ):
            event = item
            break
    if event is None:
        raise ValueError("Official session structure unavailable.")
    # Next.js serializes the round as an escaped string; only extract this
    # scalar. Session times and statuses come from public JSON-LD above.
    unescaped = re.sub(r'\\+"', '"', html)
    round_match = re.search(r'"meetingNumber"\s*:\s*"?(\d+)', unescaped)
    if not round_match:
        raise ValueError("Official round number unavailable.")
    state_match = re.search(
        r'"meetingSessions"\s*:\s*(\[.*?\])', unescaped, re.S
    )
    states = {}
    if state_match:
        states = {
            s["description"]: s.get("state")
            for s in json.loads(state_match[1])
        }
    for item in objects(flight_records(html)):
        if isinstance(item.get("meetingSessions"), list):
            states.update({
                session["description"]: session.get("state")
                for session in item["meetingSessions"]
                if isinstance(session, dict) and "description" in session
            })
    sessions = []
    name = None
    for item in event["subEvent"]:
        label, _, event_name = item["name"].partition(" - ")
        if label not in IDENTIFIERS:
            raise ValueError("Unknown session type; editor review required.")
        status = item.get("eventStatus", "").rsplit("/", 1)[-1]
        statuses = {
            "EventScheduled": "SCHEDULED",
            "EventRescheduled": "SCHEDULED",
            "EventCancelled": "CANCELLED",
            "EventPostponed": "POSTPONED",
        }
        if status not in statuses:
            raise ValueError("Unknown official event status.")
        session_status = statuses[status]
        if session_status == "SCHEDULED":
            session_status = {
                "completed": "COMPLETED",
                "live": "IN_PROGRESS",
                "inProgress": "IN_PROGRESS",
            }.get(states.get(label), session_status)
        sessions.append(
            {
                "identifier": IDENTIFIERS[label],
                "name": label,
                "starts_at": item.get("startDate"),
                "status": session_status,
            }
        )
        name = name or event_name
    race = next((s for s in sessions if s["identifier"] == "R"), None)
    if race is None:
        raise ValueError("Race session missing.")
    return CalendarWeekendInput(
        year=year,
        round_number=int(round_match[1]),
        event_name=name,
        status=race["status"],
        source_url=url,
        source_checked_at=now,
        change_reason="Session schedule published by Formula 1.",
        sessions=sessions,
    )


def fetch_page(client, url):
    with client.stream("GET", url) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > 5_000_000:
                raise ValueError("Official page exceeds supported size.")
    return data.decode("utf-8")


def ensure_calendar_sessions(db, payload, html, row=None):
    """Create schedule metadata without inventing classifications/telemetry."""
    meeting = db.get(Meeting, row.meeting_id) if row and row.meeting_id else None
    metadata = next((
        item for item in objects(flight_records(html))
        if item.get("meetingName") == payload.event_name
        and item.get("meetingCountryName")
    ), {})
    race = next(s for s in payload.sessions if s.identifier == "R")
    if meeting is None:
        meeting = db.scalar(select(Meeting).where(
            Meeting.source == "FASTF1", Meeting.year == payload.year,
            Meeting.name == payload.event_name,
        ))
    if meeting is None:
        # Reuse an unambiguous provider alias/venue before making a new stub.
        # Same-circuit double headers need distinct dates to qualify.
        aliases = {normalized_name(payload.event_name)}
        if metadata.get("meetingOfficialName"):
            aliases.add(normalized_name(metadata["meetingOfficialName"]))
        candidates = []
        for candidate in db.scalars(select(Meeting).where(
            Meeting.source == "FASTF1", Meeting.year == payload.year,
        )).all():
            name_match = bool(aliases & {
                normalized_name(candidate.name),
                normalized_name(candidate.official_name or ""),
            })
            venue_match = (
                metadata.get("meetingLocation") and candidate.location
                and normalized_name(candidate.location)
                == normalized_name(metadata["meetingLocation"])
                and normalized_name(candidate.country_name or "")
                == normalized_name(metadata.get("meetingCountryName", ""))
                and (not candidate.event_date or not race.starts_at
                     or utc(candidate.event_date).date()
                     == utc(race.starts_at).date())
            )
            if name_match or venue_match:
                candidates.append(candidate)
        if len(candidates) == 1:
            meeting = candidates[0]
    if meeting is None:
        meeting = Meeting(
            source="FASTF1", year=payload.year, name=payload.event_name,
        )
        db.add(meeting)
        db.flush()
    payload.meeting_id = meeting.id
    for field, key in (
        ("official_name", "meetingOfficialName"),
        ("country_name", "meetingCountryName"),
        ("location", "meetingLocation"),
    ):
        if metadata.get(key):
            setattr(meeting, field, metadata[key])
    meeting.event_date = race.starts_at
    imported = db.scalars(select(RaceSession).where(
        RaceSession.meeting_id == meeting.id
    )).all()
    by_identifier = {
        calendar_identifier(s.session_identifier): s for s in imported
    }
    suffixes = {
        "FP1": "practice/1", "FP2": "practice/2", "FP3": "practice/3",
        "SQ": "sprint-qualifying", "S": "sprint-results",
        "Q": "qualifying", "R": "race-result",
    }
    # Derive the results prefix from the official page's published links.
    slug = str(payload.source_url).rstrip("/").rsplit("/", 1)[-1]
    key_pattern = re.escape(str(metadata["meetingKey"])) \
        if metadata.get("meetingKey") else r"\d+"
    paths = re.findall(
        rf'/en/results/{payload.year}/races/{key_pattern}/{re.escape(slug)}/', html
    )
    prefix = BASE + paths[0] if paths else None
    for item in payload.sessions:
        session = by_identifier.get(item.identifier)
        if session is None:
            session = RaceSession(
                meeting_id=meeting.id, name=item.name,
                session_identifier=item.identifier, session_type=item.name,
                started_at=item.starts_at,
                source_metadata={"calendar_only": True},
            )
            db.add(session)
        elif (session.source_metadata or {}).get("calendar_only"):
            session.started_at = item.starts_at
        session.source_metadata = {
            **(session.source_metadata or {}),
            "calendar": {
                "source_url": str(payload.source_url),
                "round_number": payload.round_number,
                "status": item.status,
                "results_url": prefix + suffixes[item.identifier]
                if prefix else None,
            },
        }
    db.flush()


def sync_calendar(db, year: int, client=None):
    if not 2018 <= year <= 2100:
        raise ValueError("Unsupported season.")
    # Keep a dedicated PostgreSQL session lock across commits in save().
    # A second scheduler exits without touching the active run.
    lock = None
    if db.bind.dialect.name == "postgresql":
        lock = db.bind.connect()
        if not lock.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": 820000 + year}
        ).scalar():
            lock.close()
            return {"status": "BUSY"}
    owned_client = client is None
    try:
        client = client or httpx.Client(
            timeout=25,
            verify=ssl.create_default_context(),
            headers={"User-Agent": "RacePulseCalendar/1.0"},
        )
        now = datetime.now(UTC)
        result = {
            "status": "COMPLETED",
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "editor_owned": 0,
            "errors": [],
            "missing_from_source": [],
        }
        try:
            html = fetch_page(client, f"{BASE}/en/racing/{year}")
            paths = sorted(
                set(
                    re.findall(rf'href="(/en/racing/{year}/[a-z0-9-]+)"', html)
                )
            )
            paths = [path for path in paths if "testing" not in path]
            if not paths:
                raise ValueError("No official race links found.")
            source_keys = {BASE + path for path in paths}
            known = db.scalars(
                select(CalendarWeekend).where(CalendarWeekend.year == year)
            ).all()
            result["missing_from_source"] = [
                str(w.id)
                for w in known
                if w.sync_key and w.sync_key not in source_keys
            ]
            for path in paths:
                url = BASE + path
                try:
                    row = db.scalar(
                        select(CalendarWeekend).where(
                            CalendarWeekend.sync_key == url
                        )
                    )
                    if row is None:
                        row = db.scalar(
                            select(CalendarWeekend).where(
                                CalendarWeekend.schedule[
                                    "source_url"
                                ].as_string()
                                == url
                            )
                        )
                    if row and not row.sync_enabled:
                        result["editor_owned"] += 1
                        continue
                    event_html = fetch_page(client, url)
                    payload = parse_event(event_html, url, year, now)
                    ensure_calendar_sessions(db, payload, event_html, row)
                    if row:
                        current = {
                            k: v
                            for k, v in row.schedule.items()
                            if k != "source_checked_at"
                        }
                        incoming = payload.model_dump(
                            mode="json", exclude={"source_checked_at"}
                        )
                        if current == incoming:
                            result["unchanged"] += 1
                            # A successful check refreshes provenance without
                            # creating a spurious schedule revision/version.
                            db.execute(update(CalendarWeekend).where(
                                CalendarWeekend.id == row.id,
                                CalendarWeekend.version == row.version,
                                CalendarWeekend.sync_enabled.is_(True),
                            ).values(schedule=payload.model_dump(mode="json")),
                                execution_options={
                                    "synchronize_session": False,
                                })
                            db.commit()
                            db.expire(row)
                            continue
                    CalendarService(db).save(
                        payload,
                        "calendar-sync",
                        row.id if row else None,
                        row.version if row else None,
                        automated=True,
                        sync_key=url,
                    )
                    result["updated" if row else "created"] += 1
                except Exception as error:
                    db.rollback()
                    result["errors"].append(
                        {"source_url": url, "error_type": type(error).__name__}
                    )
            if result["errors"] or result["missing_from_source"]:
                result["status"] = "PARTIAL"
        except Exception as error:
            db.rollback()
            result["status"] = "FAILED"
            result["errors"].append({"error_type": type(error).__name__})
        state = db.get(CalendarSyncState, year)
        if state is None:
            state = CalendarSyncState(year=year)
            db.add(state)
        state.checked_at, state.result = now, result
        db.commit()
        return result
    finally:
        try:
            if owned_client and client is not None:
                client.close()
        finally:
            if lock is not None:
                try:
                    lock.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": 820000 + year},
                    )
                finally:
                    lock.close()

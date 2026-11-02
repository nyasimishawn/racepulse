"""Refresh Fantasy entries from published F1 classifications, not fake data."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import re
import ssl

import httpx
from sqlalchemy import select

from app.models.calendar import CalendarWeekend
from app.models.driver import Driver
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.services.calendar_service import CalendarService, utc
from app.services.calendar_sync_service import fetch_page
from app.services.official_f1_data import (
    flight_records, normalized_name, objects, resolve,
)


def milliseconds(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:\d+:)?\d{1,2}\.\d{3}", value
    ):
        return None
    minutes, separator, seconds = value.partition(":")
    return int((Decimal(seconds if separator else minutes)
                + (int(minutes) * 60 if separator else 0)) * 1000)


def content_text(value):
    if isinstance(value, str):
        return "" if value.startswith("$") else value
    if isinstance(value, dict):
        return content_text(value.get("children", value.get("content", "")))
    if isinstance(value, list):
        if len(value) == 4 and value[0] == "$" and isinstance(value[3], dict):
            return content_text(value[3])
        return "".join(content_text(child) for child in value)
    return ""


def parse_results(html):
    """Require actual structured classifications; never score an entry list."""
    rows = {}
    records = flight_records(html)
    for item in objects(records):
        if not all(item.get(key) for key in (
            "racingNumber", "driverFirstName", "driverLastName", "teamName",
        )):
            continue
        if "positionValue" not in item and "positionNumber" not in item:
            continue
        rows[str(item["racingNumber"])] = item
    # The results archive publishes the same classifications as structured
    # table cells; drivers retain named responsive spans and explicit columns.
    if not rows:
        table = next((item for item in objects(records)
                      if item.get("tableId") == "results-table"), None)
        if table:
            table = resolve(table, records)
            labels = [column["label"] for column in table["cols"]]
            if not {"Pos.", "No.", "Driver", "Team"}.issubset(labels):
                raise ValueError("Unsupported official result columns.")
            for cells in table["rows"]:
                by_label = dict(zip(labels, cells, strict=True))
                spans = {item.get("className"): item.get("children")
                         for item in objects(by_label["Driver"])
                         if isinstance(item.get("children"), str)}
                images = [item for item in objects(by_label["Driver"])
                          if item.get("src")]
                image = images[0] if images else {}
                reference = re.search(r"/([a-z]{6}\d+)/", image.get("src", ""))
                number = content_text(by_label["No."])
                row = {
                    "racingNumber": number,
                    "driverFirstName": spans.get("max-lg:hidden"),
                    "driverLastName": spans.get("max-md:hidden"),
                    "driverTLA": spans.get("md:hidden"),
                    "driverReference": reference[1] if reference else number,
                    "teamName": content_text(by_label["Team"]),
                    "teamColourCode": image.get("style", {}).get(
                        "backgroundColor", ""
                    ).lstrip("#") or None,
                    "positionValue": content_text(by_label["Pos."]),
                    "displayPosition": content_text(by_label["Pos."]),
                    "version": "Provisional" if "provisional" in str(
                        table.get("footnote", "")
                    ).casefold() else "Published",
                }
                for segment in ("Q1", "Q2", "Q3"):
                    if segment in by_label:
                        row[segment.casefold()] = content_text(by_label[segment])
                points = next((label for label in ("Pts", "PTS", "Points")
                               if label in by_label), None)
                if points:
                    row["points"] = content_text(by_label[points])
                if row["driverFirstName"] and row["driverLastName"]:
                    rows[number] = row
    if len(rows) < 5 or not any(
        str(row.get("positionValue", row.get("positionNumber"))) == "1"
        for row in rows.values()
    ):
        raise ValueError("Published session classification unavailable.")
    return list(rows.values())


def upsert_participant(db, row):
    first, last = row["driverFirstName"], row["driverLastName"]
    full_name = f"{first} {last}"
    # Reuse existing FastF1 identities so submitted picks survive imports.
    drivers = db.scalars(select(Driver).where(Driver.source == "FASTF1")).all()
    driver = next((driver for driver in drivers if normalized_name(
        driver.full_name or f"{driver.first_name or ''} {driver.last_name or ''}"
    ) == normalized_name(full_name)), None)
    source_identifier = "f1:" + str(row.get(
        "driverReference", row.get("driverKey", row["racingNumber"])
    )).casefold()
    if driver is None:
        driver = db.scalar(select(Driver).where(
            Driver.source == "FASTF1",
            Driver.source_identifier == source_identifier,
        ))
    if driver is None:
        driver = Driver(source="FASTF1", source_identifier=source_identifier)
        db.add(driver)
    driver.first_name, driver.last_name = first, last
    driver.full_name = full_name
    driver.driver_number = str(row["racingNumber"])
    driver.abbreviation = row.get("driverTLA")
    teams = db.scalars(select(Team).where(Team.source == "FASTF1")).all()
    team = next((team for team in teams if normalized_name(team.name)
                 == normalized_name(row["teamName"])), None)
    team_identifier = "f1:" + str(row.get("teamKey", row["teamName"])).casefold()
    if team is None:
        team = db.scalar(select(Team).where(
            Team.source == "FASTF1", Team.source_identifier == team_identifier,
        ))
    if team is None:
        team = Team(source="FASTF1", source_identifier=team_identifier)
        db.add(team)
    team.name, team.colour = row["teamName"], row.get("teamColourCode")
    db.flush()
    return driver, team


def save_results(db, session, rows, url, now):
    roster = []
    for row in rows:
        driver, team = upsert_participant(db, row)
        result = db.scalar(select(SessionResult).where(
            SessionResult.race_session_id == session.id,
            SessionResult.driver_id == driver.id,
        ))
        if result is None:
            result = SessionResult(race_session_id=session.id, driver_id=driver.id)
            db.add(result)
        position = str(row.get("positionValue", row.get("positionNumber", "")))
        result.position = int(position) if position.isdigit() and int(position) < 100 else None
        result.classified_position = row.get("displayPosition", position)
        result.team_id = team.id
        if row.get("completionStatusCode"):
            result.status = row["completionStatusCode"]
        if row.get("points") is not None:
            result.points = Decimal(str(row["points"]))
        for segment in ("q1", "q2", "q3"):
            value = next((row[key] for key in (
                segment, f"{segment}Time", f"{segment.upper()}Time",
                f"qualifying{segment.upper()}Time",
            ) if key in row), None)
            timing = milliseconds(value)
            if timing is not None:
                setattr(result, f"{segment}_time_ms", timing)
        roster.append({"driver_id": str(driver.id), "team_id": str(team.id)})
    session.source_metadata = {
        **(session.source_metadata or {}), "calendar_only": False,
        "official_results": {
            "source_url": url, "checked_at": now.isoformat(),
            "versions": sorted({str(row.get("version", "Published"))
                                for row in rows}),
        },
        "fantasy_roster": roster,
    }
    db.commit()
    return roster


def sync_live_fantasy(db, year, client=None, now=None):
    now = utc(now) if now else datetime.now(UTC)
    result = {"status": "COMPLETED", "results_updated": 0,
              "roster_size": 0, "errors": [], "pending_sessions": []}
    weekends = db.scalars(select(CalendarWeekend).where(
        CalendarWeekend.year == year,
        CalendarWeekend.meeting_id.is_not(None),
    )).all()
    current = CalendarService(db, now=now).next()
    active_id = current["id"] if current and current["year"] == year else None
    dated = []
    for weekend in weekends:
        race = next(s for s in weekend.schedule["sessions"]
                    if s["identifier"] == "R")
        if race["starts_at"]:
            start = datetime.fromisoformat(race["starts_at"].replace("Z", "+00:00"))
            dated.append((start, weekend))
    recent = {weekend.id for start, weekend in dated
              if now - timedelta(days=10) <= start <= now + timedelta(days=4)}
    previous = [pair for pair in dated if pair[0] <= now]
    if previous:
        recent.add(max(previous, key=lambda pair: pair[0])[1].id)
    if active_id:
        recent.add(active_id)
    own_client = client is None
    client = client or httpx.Client(
        timeout=25, verify=ssl.create_default_context(),
        headers={"User-Agent": "RacePulseFantasy/1.0"},
    )
    latest_roster = None
    try:
        sessions = db.scalars(select(RaceSession).where(
            RaceSession.meeting_id.in_(
                [weekend.meeting_id for weekend in weekends
                 if weekend.id in recent]
            )
        ).order_by(RaceSession.started_at)).all()
        for session in sessions:
            metadata = session.source_metadata or {}
            calendar = metadata.get("calendar", {})
            url = calendar.get("results_url")
            if calendar.get("status") in {"CANCELLED", "POSTPONED"}:
                continue
            if not session.started_at or utc(session.started_at) > now:
                continue
            if metadata.get("fantasy_roster"):
                latest_roster = (utc(session.started_at), metadata["fantasy_roster"])
            if not url or calendar.get("status") != "COMPLETED":
                continue
            try:
                rows = parse_results(fetch_page(client, url))
                roster = save_results(db, session, rows, url, now)
                latest_roster = (utc(session.started_at), roster)
                result["results_updated"] += 1
            except ValueError:
                db.rollback()
                result["pending_sessions"].append(str(session.id))
            except httpx.HTTPStatusError as error:
                db.rollback()
                if error.response.status_code == 404:
                    result["pending_sessions"].append(str(session.id))
                else:
                    result["errors"].append({
                        "source_url": url, "error_type": type(error).__name__,
                    })
            except Exception as error:
                db.rollback()
                result["errors"].append({
                    "source_url": url, "error_type": type(error).__name__,
                })
        if latest_roster:
            checked, roster = latest_roster
            result["roster_size"] = len(roster)
            # Entry lists for current/future events follow the latest observed
            # season roster. Historical predictions keep their original list.
            for session in db.scalars(select(RaceSession).where(
                RaceSession.meeting_id.in_(
                    [weekend.meeting_id for start, weekend in dated
                     if start >= now or weekend.id == active_id]
                )
            )).all():
                session.source_metadata = {
                    **(session.source_metadata or {}), "fantasy_roster": roster,
                    "fantasy_roster_checked_at": checked.isoformat(),
                }
            db.commit()
        # Results remain pending until the publisher supplies classification.
        from app.services.fantasy_service import FantasyService

        for session in sessions:
            if session.session_identifier == "R":
                FantasyService(db).score_available_questions(session.id)
        if result["errors"]:
            result["status"] = "PARTIAL"
        return result
    finally:
        if own_client:
            client.close()

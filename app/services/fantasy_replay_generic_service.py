"""Replay any fully imported weekend while retaining older Barcelona replays."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models.fantasy import FantasyQuestionResolution
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.schemas.fantasy import FantasyQuestionResolutionRequest
from app.services.fantasy_replay_service import (
    FantasyReplayService, ReplayError, SOURCE, SPEEDS, utc,
)
from app.services.fantasy_service import FantasyService


SESSION_HOURS = {"FP1": 1, "FP2": 1, "FP3": 1, "SQ": 1,
                 "S": 2, "Q": 1, "R": 3}
RESULT_FIELDS = (
    "team_id", "position", "classified_position", "grid_position",
    "q1_time_ms", "q2_time_ms", "q3_time_ms", "status", "points",
)


class GenericFantasyReplayService(FantasyReplayService):
    def create(self, owner_id: UUID, source_race_id: UUID | None = None) -> dict:
        self._require_development()
        if source_race_id is None:
            current = self.current(owner_id)
            if current is not None:
                race = self.db.get(RaceSession, UUID(current["race"]["race_session_id"]))
                source_race_id = UUID(self._metadata(race)["source_race_id"])
            else:
                return super().create(owner_id)
        source = self.db.get(RaceSession, source_race_id)
        meeting = self.db.get(Meeting, source.meeting_id) if source else None
        if (source is None or meeting is None or meeting.source == SOURCE
                or source.session_identifier != "R"):
            raise ReplayError("Select an imported race weekend to replay.")
        sessions = self.db.scalars(
            select(RaceSession).where(RaceSession.meeting_id == meeting.id)
            .order_by(RaceSession.started_at, RaceSession.name)
        ).all()
        sessions = [s for s in sessions if s.session_identifier in SESSION_HOURS
                    and s.started_at is not None]
        if not {"FP1", "Q", "R"}.issubset(
            {s.session_identifier for s in sessions}
        ):
            raise ReplayError("Wait for FP1, qualifying and race sessions to import.")
        results = {
            session.id: self.db.scalars(select(SessionResult).where(
                SessionResult.race_session_id == session.id
            )).all()
            for session in sessions
        }
        if any(not rows for rows in results.values()):
            raise ReplayError("Wait for all weekend session results to import.")
        if not {1, 2, 3}.issubset({row.position for row in results[source.id]}):
            raise ReplayError("Race classification is incomplete.")

        previous = self.db.scalars(
            select(RaceSession)
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(Meeting.source == SOURCE, RaceSession.session_identifier == "R")
        ).all()
        for old in previous:
            old_metadata = self._metadata(old)
            if old_metadata.get("owner_profile_id") == str(owner_id):
                old.source_metadata = {
                    "fantasy_replay": {**old_metadata, "is_current": False}
                }
                flag_modified(old, "source_metadata")

        first_start = min(utc(s.started_at) for s in sessions)
        replay_meeting = Meeting(
            source=SOURCE, year=meeting.year,
            name=f"{meeting.name} Replay {uuid4().hex[:12]}",
            event_date=source.started_at,
        )
        self.db.add(replay_meeting)
        self.db.flush()
        cloned = {}
        for session in sessions:
            clone = RaceSession(
                meeting_id=replay_meeting.id, name=session.name,
                session_identifier=session.session_identifier,
                session_type=session.session_type,
                started_at=session.started_at, source_metadata={},
            )
            self.db.add(clone)
            self.db.flush()
            cloned[session.id] = clone
        events = [{
            "key": session.session_identifier, "label": session.name,
            "starts_at": utc(session.started_at).isoformat(),
            "results_at": (utc(session.started_at) + timedelta(
                hours=SESSION_HOURS[session.session_identifier]
            )).isoformat(),
            "source_session_id": str(session.id),
            "replay_session_id": str(cloned[session.id].id),
        } for session in sessions]
        replay_race = cloned[source.id]
        replay_race.source_metadata = {"fantasy_replay": {
            "owner_profile_id": str(owner_id), "is_current": True,
            "source_race_id": str(source.id),
            "source_meeting_name": meeting.name,
            "base_virtual_at": (first_start - timedelta(days=1)).isoformat(),
            "anchor_real_at": self.now.isoformat(), "speed": 3600,
            "playing": False, "events": events, "released": [],
        }}
        self.db.add_all(SessionResult(
            race_session_id=replay_race.id, driver_id=row.driver_id,
            team_id=row.team_id,
        ) for row in results[source.id])
        self.db.commit()
        return self.state(replay_race.id, owner_id)

    def state(self, race_id: UUID, owner_id: UUID) -> dict:
        race = self.db.get(RaceSession, race_id)
        metadata = self._metadata(race)
        if not metadata.get("events"):
            return super().state(race_id, owner_id)
        self._require_development()
        self._require_owner(race, owner_id)
        virtual = self._virtual_time(race)
        self._release_due(race, virtual, owner_id)
        meeting = self.db.get(Meeting, race.meeting_id)
        return {
            "race": {
                "race_session_id": str(race.id),
                "meeting_id": str(meeting.id),
                "meeting_name": f"{metadata['source_meeting_name']} · Replay",
                "year": meeting.year, "session_name": race.name,
                "started_at": utc(race.started_at).isoformat(),
            },
            "source_race_session_id": metadata["source_race_id"],
            "virtual_time": virtual.isoformat(),
            "playing": metadata["playing"] and virtual < self._end(metadata),
            "speed": metadata["speed"],
            "events": [{
                "key": event["key"], "label": event["label"],
                "starts_at": event["starts_at"],
                "results_at": event["results_at"],
                "state": (
                    "RESULT" if virtual >= datetime.fromisoformat(event["results_at"])
                    else "LIVE" if virtual >= datetime.fromisoformat(event["starts_at"])
                    else "UPCOMING"
                ),
            } for event in metadata["events"]],
        }

    def control(self, race_id: UUID, owner_id: UUID, *, playing: bool,
                speed: int) -> dict:
        race = self.db.get(RaceSession, race_id)
        metadata = self._metadata(race)
        if not metadata.get("events"):
            return super().control(race_id, owner_id, playing=playing,
                                   speed=speed)
        self._require_development()
        self._require_owner(race, owner_id)
        if speed not in SPEEDS:
            raise ReplayError("Choose 60×, 600×, 3600×, or 14400×.")
        virtual = self._virtual_time(race)
        metadata = dict(metadata)
        metadata.update(
            base_virtual_at=virtual.isoformat(),
            anchor_real_at=self.now.isoformat(),
            playing=playing and virtual < self._end(metadata), speed=speed,
        )
        race.source_metadata = {"fantasy_replay": metadata}
        flag_modified(race, "source_metadata")
        self.db.commit()
        return self.state(race_id, owner_id)

    @staticmethod
    def _end(metadata: dict) -> datetime:
        return max(datetime.fromisoformat(e["results_at"])
                   for e in metadata["events"])

    def _virtual_time(self, race: RaceSession) -> datetime:
        metadata = self._metadata(race)
        if not metadata.get("events"):
            return super()._virtual_time(race)
        virtual = datetime.fromisoformat(metadata["base_virtual_at"])
        if metadata["playing"]:
            anchor = datetime.fromisoformat(metadata["anchor_real_at"])
            elapsed = max(0, (self.now - anchor).total_seconds())
            virtual += timedelta(seconds=elapsed * metadata["speed"])
        return min(utc(virtual), self._end(metadata))

    def _release_due(self, race: RaceSession, virtual: datetime,
                     owner_id: UUID) -> None:
        metadata = dict(self._metadata(race))
        if not metadata.get("events"):
            return super()._release_due(race, virtual, owner_id)
        released = set(metadata.get("released", []))
        changed = False
        for event in metadata["events"]:
            if (event["key"] in released or
                    virtual < datetime.fromisoformat(event["results_at"])):
                continue
            source_id = UUID(event["source_session_id"])
            replay_id = UUID(event["replay_session_id"])
            source_rows = self.db.scalars(select(SessionResult).where(
                SessionResult.race_session_id == source_id
            )).all()
            replay_rows = {row.driver_id: row for row in self.db.scalars(
                select(SessionResult).where(SessionResult.race_session_id == replay_id)
            )}
            for source_row in source_rows:
                row = replay_rows.get(source_row.driver_id)
                if row is None:
                    row = SessionResult(race_session_id=replay_id,
                                        driver_id=source_row.driver_id)
                    self.db.add(row)
                for field in RESULT_FIELDS:
                    setattr(row, field, getattr(source_row, field))
            if event["key"] == "R":
                self._copy_scoring_laps(source_id, replay_id)
            released.add(event["key"])
            changed = True
        if not changed:
            return
        metadata["released"] = sorted(released)
        race.source_metadata = {"fantasy_replay": metadata}
        flag_modified(race, "source_metadata")
        self.db.flush()
        fantasy = FantasyService(self.db, now=virtual)
        fantasy.score_available_questions(race.id)
        if "R" in released:
            existing = {item.question_key for item in self.db.scalars(
                select(FantasyQuestionResolution).where(
                    FantasyQuestionResolution.race_session_id == race.id
                )
            )}
            source_race_id = UUID(metadata["source_race_id"])
            if "RACE_DNF_DRIVERS" not in existing:
                retired = [row.driver_id for row in self.db.scalars(
                    select(SessionResult).where(
                        SessionResult.race_session_id == source_race_id,
                        SessionResult.classified_position == "R",
                    )
                )]
                fantasy.set_question_resolution(
                    race_session_id=race.id, question_key="RACE_DNF_DRIVERS",
                    payload=FantasyQuestionResolutionRequest(
                        status="RESOLVED", actual_driver_ids=retired,
                        source_reference=f"session_results:{source_race_id}",
                    ), resolved_by_profile_id=owner_id,
                )
            for key, column in (("RACE_FASTEST_LAP", Lap.lap_time_ms),
                                ("RACE_TOP_SPEED", Lap.speed_st)):
                if key in existing:
                    continue
                if self.db.scalar(select(Lap).where(
                    Lap.race_session_id == race.id, column.is_not(None)
                )) is None:
                    fantasy.set_question_resolution(
                        race_session_id=race.id, question_key=key,
                        payload=FantasyQuestionResolutionRequest(
                            status="NOT_SCORED",
                            source_reference="Imported lap data unavailable",
                        ), resolved_by_profile_id=owner_id,
                    )
        self.db.commit()

    def _copy_scoring_laps(self, source_id: UUID, replay_id: UUID) -> None:
        laps = self.db.scalars(select(Lap).where(
            Lap.race_session_id == source_id, Lap.lap_time_ms.is_not(None),
            Lap.is_accurate.is_(True), Lap.deleted.is_not(True),
            Lap.fastf1_generated.is_not(True),
        )).all()
        fastest = min(laps, key=lambda lap: lap.lap_time_ms, default=None)
        speeds = [lap for lap in laps if lap.speed_st is not None]
        top_speed = max(speeds, key=lambda lap: lap.speed_st, default=None)
        for source_lap in {lap.id: lap for lap in (fastest, top_speed)
                           if lap is not None}.values():
            self.db.add(Lap(
                race_session_id=replay_id, driver_id=source_lap.driver_id,
                lap_number=source_lap.lap_number,
                lap_time_ms=source_lap.lap_time_ms,
                speed_st=source_lap.speed_st,
                is_accurate=True, deleted=False, fastf1_generated=False,
            ))

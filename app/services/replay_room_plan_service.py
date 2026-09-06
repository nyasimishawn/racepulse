from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.session_map_import import (
    SessionMapDriverStatus,
    SessionMapImport,
    SessionMapImportStatus,
)
from app.models.session_map_import_driver import (
    SessionMapImportDriver,
)
from app.schemas.race_context import TimelineEventType
from app.services.race_context_service import RaceContextService


MAX_CONTEXT_TAIL_MS = 300_000


@dataclass(frozen=True)
class ReplayRoomPlanEnrichment:
    map_metadata: dict[str, Any]
    context_events: list[dict[str, Any]]
    context_available: bool
    context_alignment: str
    warnings: list[str]


class ReplayRoomPlanService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_enrichment(
        self,
        *,
        race_session_id: UUID,
        source_time_origin_ms: int,
        timing_duration_ms: int,
        eligible_driver_count: int,
    ) -> ReplayRoomPlanEnrichment:
        map_metadata, map_warnings = self._build_map_metadata(
            race_session_id=race_session_id,
            eligible_driver_count=eligible_driver_count,
        )

        (
            context_events,
            context_available,
            context_alignment,
            context_warnings,
        ) = self._build_context_events(
            race_session_id=race_session_id,
            source_time_origin_ms=source_time_origin_ms,
            timing_duration_ms=timing_duration_ms,
        )

        return ReplayRoomPlanEnrichment(
            map_metadata=map_metadata,
            context_events=context_events,
            context_available=context_available,
            context_alignment=context_alignment,
            warnings=map_warnings + context_warnings,
        )

    def _build_map_metadata(
        self,
        *,
        race_session_id: UUID,
        eligible_driver_count: int,
    ) -> tuple[dict[str, Any], list[str]]:
        metadata: dict[str, Any] = {
            "track_map_available": False,
            "full_session_track_map_available": False,
            "map_import_id": None,
            "map_driver_count": 0,
            "map_sample_interval_ms": None,
            "map_time_alignment": None,
        }

        candidates = self.db.scalars(
            select(SessionMapImport)
            .where(
                SessionMapImport.race_session_id == race_session_id,
                SessionMapImport.status.in_(
                    [
                        SessionMapImportStatus.COMPLETED,
                        SessionMapImportStatus.PARTIAL,
                    ]
                ),
            )
            .order_by(
                SessionMapImport.completed_at.desc(),
                SessionMapImport.created_at.desc(),
            )
        ).all()

        for candidate in candidates:
            usable_drivers = self.db.scalars(
                select(SessionMapImportDriver).where(
                    SessionMapImportDriver.map_import_id == candidate.id,
                    SessionMapImportDriver.status.in_(
                        [
                            SessionMapDriverStatus.READY,
                            SessionMapDriverStatus.PARTIAL,
                        ]
                    ),
                )
            ).all()

            if not usable_drivers:
                continue

            ready_driver_count = len(usable_drivers)

            full_session_ready = (
                candidate.requested_driver_numbers is None
                and candidate.status
                == SessionMapImportStatus.COMPLETED
                and ready_driver_count >= eligible_driver_count
            )

            metadata = {
                "track_map_available": True,
                "full_session_track_map_available": full_session_ready,
                "map_import_id": str(candidate.id),
                "map_driver_count": ready_driver_count,
                # Raw downloads keep all points; replay still has a bounded
                # display cadence independent of storage sampling.
                "map_sample_interval_ms": candidate.sample_interval_ms or 250,
                "map_time_alignment": (
                    "FASTF1_SESSION_TIME_MINUS_PLAN_ORIGIN"
                ),
            }

            warnings = []

            if not full_session_ready:
                warnings.append(
                    f"PARTIAL_TRACK_MAP:{ready_driver_count}/"
                    f"{eligible_driver_count}"
                )

            return metadata, warnings

        return metadata, []

    def _build_context_events(
        self,
        *,
        race_session_id: UUID,
        source_time_origin_ms: int,
        timing_duration_ms: int,
    ) -> tuple[list[dict[str, Any]], bool, str, list[str]]:
        try:
            timeline = RaceContextService(self.db).get_timeline(
                race_session_id=race_session_id,
                event_types=set(TimelineEventType),
                include_weather=True,
                limit=10_000,
            )
        except Exception as error:
            return (
                [],
                False,
                "NOT_IMPORTED",
                [f"RACE_CONTEXT_UNAVAILABLE:{type(error).__name__}"],
            )

        events: list[dict[str, Any]] = []
        warnings: list[str] = []

        seen_ids: set[str] = set()
        unaligned_count = 0
        before_origin_count = 0
        after_finish_count = 0
        aligned_seen = False

        maximum_context_cursor_ms = (
            timing_duration_ms + MAX_CONTEXT_TAIL_MS
        )

        for event in timeline.events:
            serialized = event.model_dump(mode="json")
            source_session_time_ms = serialized.get(
                "session_time_ms"
            )

            if source_session_time_ms is None:
                unaligned_count += 1
                continue

            aligned_seen = True

            source_cursor_ms = (
                int(source_session_time_ms)
                - source_time_origin_ms
            )

            if source_cursor_ms < 0:
                before_origin_count += 1
                continue

            if source_cursor_ms > maximum_context_cursor_ms:
                after_finish_count += 1
                continue

            event_id = str(serialized["event_id"])

            if event_id in seen_ids:
                continue

            seen_ids.add(event_id)

            events.append(
                {
                    "event_id": event_id,
                    "event_type": serialized["event_type"],
                    "at_ms": source_cursor_ms,
                    "source_session_time_ms": int(
                        source_session_time_ms
                    ),
                    "occurred_at": serialized.get("occurred_at"),
                    "priority": int(serialized["priority"]),
                    "title": serialized["title"],
                    "message": serialized["message"],
                    "severity": serialized["severity"],
                    "driver_number": serialized.get(
                        "driver_number"
                    ),
                    "lap_number": serialized.get("lap_number"),
                    "data_quality_flags": serialized.get(
                        "data_quality_flags",
                        [],
                    ),
                    "payload": serialized.get("payload", {}),
                }
            )

        events.sort(
            key=lambda event: (
                event["at_ms"],
                event["priority"],
                event["event_id"],
            )
        )

        if not timeline.events:
            warnings.append("NO_RACE_CONTEXT_EVENTS")

        if unaligned_count:
            warnings.append(
                f"UNALIGNED_CONTEXT_EVENTS_SKIPPED:{unaligned_count}"
            )

        if before_origin_count:
            warnings.append(
                f"PRE_REPLAY_CONTEXT_EVENTS_SKIPPED:"
                f"{before_origin_count}"
            )

        if after_finish_count:
            warnings.append(
                f"DISTANT_POST_RACE_CONTEXT_EVENTS_SKIPPED:"
                f"{after_finish_count}"
            )

        context_available = bool(events)

        context_alignment = (
            "LAP_ANCHORED"
            if aligned_seen
            else (
                "UNALIGNED"
                if timeline.total > 0
                else "NOT_IMPORTED"
            )
        )

        return (
            events,
            context_available,
            context_alignment,
            warnings,
        )

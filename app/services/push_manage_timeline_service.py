from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.push_manage import (
    classify_observed_effort,
)
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.race_session import RaceSession
from app.models.telemetry_point import TelemetryPoint
from app.schemas.push_manage_timeline import (
    PushManageContextEventResponse,
    PushManageCoverageResponse,
    PushManageQualifyingReferenceResponse,
    PushManageTimelineEntryResponse,
    PushManageTimelineResponse,
)
from app.schemas.race_context import TimelineEventType
from app.services.attack_index_v2_service import (
    AttackIndexV2Service,
)
from app.services.qualifying_service import (
    QualifyingQueryService,
)
from app.services.race_context_service import (
    RaceContextService,
)


DISCLAIMER = (
    "Push / Manage Timeline uses Attack Index V2 as a telemetry-derived "
    "driving-effort proxy. PUSHING_LIKE, MANAGING_LIKE, and COASTING_LIKE "
    "describe observed throttle, brake, and coasting patterns relative "
    "to a qualifying reference; they do not prove driver intent, team "
    "instructions, traffic, fuel load, ERS mode, tyre temperature, or "
    "private team telemetry. Missing telemetry is always NOT_SCORED."
)


class PushManageSessionNotFoundError(LookupError):
    pass


class PushManageDriverNotFoundError(LookupError):
    pass


class NonRacePushManageSessionError(ValueError):
    pass


class PushManageValidationError(ValueError):
    pass


class PushManageTimelineService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_timeline(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
        start_lap: int | None,
        end_lap: int | None,
        include_context: bool,
    ) -> PushManageTimelineResponse:
        if (
            start_lap is not None
            and end_lap is not None
            and start_lap > end_lap
        ):
            raise PushManageValidationError(
                "start_lap cannot be greater than end_lap."
            )

        race_session = self.db.get(
            RaceSession,
            race_session_id,
        )

        if race_session is None:
            raise PushManageSessionNotFoundError(
                "Race session not found."
            )

        if not self._is_race_session(race_session):
            raise NonRacePushManageSessionError(
                "Push / Manage Timeline currently supports Race sessions only."
            )

        normalized_driver_number = driver_number.strip()

        driver = self.db.scalar(
            select(Driver)
            .join(Lap, Lap.driver_id == Driver.id)
            .where(
                Lap.race_session_id == race_session_id,
                Driver.driver_number == normalized_driver_number,
            )
            .limit(1)
        )

        if driver is None:
            raise PushManageDriverNotFoundError(
                "Driver was not found in this race session."
            )

        all_laps = self.db.scalars(
            select(Lap)
            .where(
                Lap.race_session_id == race_session_id,
                Lap.driver_id == driver.id,
            )
            .order_by(Lap.lap_number)
        ).all()

        selected_laps = [
            lap
            for lap in all_laps
            if (
                start_lap is None
                or lap.lap_number >= start_lap
            )
            and (
                end_lap is None
                or lap.lap_number <= end_lap
            )
        ]

        if not selected_laps:
            raise PushManageValidationError(
                "No laps were found in the requested lap range."
            )

        qualifying_reference = QualifyingQueryService(
            self.db
        ).get_race_qualifying_reference(
            race_session_id=race_session_id,
            driver_number=normalized_driver_number,
        )

        qualifying_lap = (
            self.db.get(
                Lap,
                qualifying_reference.reference_lap.id,
            )
            if qualifying_reference.reference_lap is not None
            else None
        )

        telemetry_lap_ids = {
            lap.id
            for lap in selected_laps
        }

        if qualifying_lap is not None:
            telemetry_lap_ids.add(qualifying_lap.id)

        stored_telemetry_lap_ids = (
            self._stored_telemetry_lap_ids(telemetry_lap_ids)
        )

        qualifying_has_stored_telemetry = (
            qualifying_lap is not None
            and qualifying_lap.id in stored_telemetry_lap_ids
        )

        warnings: list[str] = []

        reference_problem = None

        if qualifying_reference.reference_lap is None:
            reference_problem = (
                qualifying_reference.unavailable_reason
                or "No qualifying reference lap is available."
            )
            warnings.append("QUALIFYING_REFERENCE_UNAVAILABLE")

        elif qualifying_lap is None:
            reference_problem = (
                "The stored qualifying reference lap could not be loaded."
            )
            warnings.append("QUALIFYING_REFERENCE_LAP_MISSING")

        elif not qualifying_has_stored_telemetry:
            reference_problem = (
                "Qualifying reference telemetry has not been imported."
            )
            warnings.append(
                "QUALIFYING_REFERENCE_TELEMETRY_NOT_IMPORTED"
            )

        context_events: list[
            PushManageContextEventResponse
        ] = []

        if include_context:
            context_events, context_warnings = (
                self._load_context_events(
                    race_session_id=race_session_id,
                    laps=selected_laps,
                )
            )
            warnings.extend(context_warnings)

        mode_counts: dict[str, int] = defaultdict(int)
        entries: list[PushManageTimelineEntryResponse] = []

        attack_service = AttackIndexV2Service(self.db)

        for lap in selected_laps:
            context_event_ids = self._context_ids_for_lap(
                lap,
                context_events,
            )

            has_race_telemetry = (
                lap.id in stored_telemetry_lap_ids
            )

            is_candidate = (
                reference_problem is None
                and has_race_telemetry
            )

            if not is_candidate:
                entry = self._not_scored_entry(
                    lap=lap,
                    context_event_ids=context_event_ids,
                    reason=(
                        reference_problem
                        if reference_problem is not None
                        else "Race-lap telemetry has not been imported."
                    ),
                )
            else:
                entry = self._score_entry(
                    attack_service=attack_service,
                    race_session_id=race_session_id,
                    driver_number=normalized_driver_number,
                    lap=lap,
                    context_event_ids=context_event_ids,
                )

            mode_counts[entry.observed_mode] += 1
            entries.append(entry)

        scored_lap_count = sum(
            entry.telemetry_eligible
            for entry in entries
        )

        race_laps_with_stored_telemetry = sum(
            lap.id in stored_telemetry_lap_ids
            for lap in selected_laps
        )

        telemetry_candidate_lap_count = (
            race_laps_with_stored_telemetry
            if reference_problem is None
            else 0
        )

        return PushManageTimelineResponse(
            timeline_version="push-manage-timeline-v1",
            race_session_id=race_session_id,
            session_name=race_session.name,
            driver_number=driver.driver_number,
            abbreviation=driver.abbreviation,
            driver_name=self._driver_name(driver),
            requested_start_lap=start_lap,
            requested_end_lap=end_lap,
            qualifying_reference=(
                PushManageQualifyingReferenceResponse(
                    qualifying_session_id=(
                        qualifying_reference.qualifying_session_id
                    ),
                    qualifying_lap_id=(
                        qualifying_lap.id
                        if qualifying_lap is not None
                        else None
                    ),
                    qualifying_lap_number=(
                        qualifying_lap.lap_number
                        if qualifying_lap is not None
                        else None
                    ),
                    qualifying_reference_quality=(
                        qualifying_reference.reference_lap.reference_quality
                        if qualifying_reference.reference_lap is not None
                        else None
                    ),
                    qualifying_reference_available=(
                        qualifying_lap is not None
                    ),
                    qualifying_reference_has_stored_telemetry=(
                        qualifying_has_stored_telemetry
                    ),
                    unavailable_reason=reference_problem,
                )
            ),
            coverage=PushManageCoverageResponse(
                total_requested_laps=len(selected_laps),
                race_laps_with_stored_telemetry=(
                    race_laps_with_stored_telemetry
                ),
                telemetry_candidate_lap_count=(
                    telemetry_candidate_lap_count
                ),
                scored_lap_count=scored_lap_count,
                unscored_lap_count=(
                    len(entries) - scored_lap_count
                ),
                race_laps_without_stored_telemetry=(
                    len(selected_laps)
                    - race_laps_with_stored_telemetry
                ),
            ),
            mode_counts=dict(mode_counts),
            context_events=context_events,
            entries=entries,
            warnings=sorted(set(warnings)),
            disclaimer=DISCLAIMER,
        )

    def _score_entry(
        self,
        *,
        attack_service: AttackIndexV2Service,
        race_session_id: UUID,
        driver_number: str,
        lap: Lap,
        context_event_ids: list[str],
    ) -> PushManageTimelineEntryResponse:
        try:
            attack = attack_service.assess(
                race_session_id=race_session_id,
                driver_number=driver_number,
                race_lap_number=lap.lap_number,
            )
        except Exception as error:
            return self._not_scored_entry(
                lap=lap,
                context_event_ids=context_event_ids,
                reason=(
                    "Attack Index scoring failed for this lap: "
                    f"{type(error).__name__}."
                ),
            )

        if (
            not attack.eligible
            or attack.telemetry_effort is None
        ):
            return PushManageTimelineEntryResponse(
                lap_id=lap.id,
                lap_number=lap.lap_number,
                lap_start_session_time_ms=lap.lap_start_time_ms,
                lap_end_session_time_ms=(
                    self._lap_end_time_ms(lap)
                ),
                lap_time_ms=lap.lap_time_ms,
                sector_1_time_ms=lap.sector_1_time_ms,
                sector_2_time_ms=lap.sector_2_time_ms,
                sector_3_time_ms=lap.sector_3_time_ms,
                stint_number=lap.stint,
                compound=lap.compound,
                tyre_life=(
                    float(lap.tyre_life)
                    if lap.tyre_life is not None
                    else None
                ),
                fresh_tyre=lap.fresh_tyre,
                position=lap.position,
                track_status=lap.track_status,
                telemetry_eligible=False,
                attack_index_metric_version=attack.metric_version,
                telemetry_effort=None,
                pace_context=attack.pace_context,
                observed_mode="NOT_SCORED",
                evidence_tags=["ATTACK_INDEX_INELIGIBLE"],
                ineligibility_reasons=(
                    attack.ineligibility_reasons
                ),
                lap_quality_flags=self._lap_quality_flags(lap),
                context_event_ids=context_event_ids,
            )

        components = attack.telemetry_effort.components

        observed = classify_observed_effort(
            effort_score_10=attack.telemetry_effort.score_10,
            same_stint_pace_band=(
                attack.pace_context.same_stint_pace_band
            ),
            extra_coast_candidate_distance_m=(
                components.extra_coast_candidate_distance_m
            ),
        )

        return PushManageTimelineEntryResponse(
            lap_id=lap.id,
            lap_number=lap.lap_number,
            lap_start_session_time_ms=lap.lap_start_time_ms,
            lap_end_session_time_ms=self._lap_end_time_ms(lap),
            lap_time_ms=lap.lap_time_ms,
            sector_1_time_ms=lap.sector_1_time_ms,
            sector_2_time_ms=lap.sector_2_time_ms,
            sector_3_time_ms=lap.sector_3_time_ms,
            stint_number=lap.stint,
            compound=lap.compound,
            tyre_life=(
                float(lap.tyre_life)
                if lap.tyre_life is not None
                else None
            ),
            fresh_tyre=lap.fresh_tyre,
            position=lap.position,
            track_status=lap.track_status,
            telemetry_eligible=True,
            attack_index_metric_version=attack.metric_version,
            telemetry_effort=attack.telemetry_effort,
            pace_context=attack.pace_context,
            observed_mode=observed.mode,
            evidence_tags=observed.evidence_tags,
            ineligibility_reasons=[],
            lap_quality_flags=self._lap_quality_flags(lap),
            context_event_ids=context_event_ids,
        )

    def _not_scored_entry(
        self,
        *,
        lap: Lap,
        context_event_ids: list[str],
        reason: str,
    ) -> PushManageTimelineEntryResponse:
        return PushManageTimelineEntryResponse(
            lap_id=lap.id,
            lap_number=lap.lap_number,
            lap_start_session_time_ms=lap.lap_start_time_ms,
            lap_end_session_time_ms=self._lap_end_time_ms(lap),
            lap_time_ms=lap.lap_time_ms,
            sector_1_time_ms=lap.sector_1_time_ms,
            sector_2_time_ms=lap.sector_2_time_ms,
            sector_3_time_ms=lap.sector_3_time_ms,
            stint_number=lap.stint,
            compound=lap.compound,
            tyre_life=(
                float(lap.tyre_life)
                if lap.tyre_life is not None
                else None
            ),
            fresh_tyre=lap.fresh_tyre,
            position=lap.position,
            track_status=lap.track_status,
            telemetry_eligible=False,
            attack_index_metric_version=None,
            telemetry_effort=None,
            pace_context=None,
            observed_mode="NOT_SCORED",
            evidence_tags=["TELEMETRY_NOT_IMPORTED"],
            ineligibility_reasons=[reason],
            lap_quality_flags=self._lap_quality_flags(lap),
            context_event_ids=context_event_ids,
        )

    def _stored_telemetry_lap_ids(
        self,
        lap_ids: set[UUID],
    ) -> set[UUID]:
        if not lap_ids:
            return set()

        return set(
            self.db.scalars(
                select(TelemetryPoint.lap_id)
                .where(TelemetryPoint.lap_id.in_(lap_ids))
                .distinct()
            ).all()
        )

    def _load_context_events(
        self,
        *,
        race_session_id: UUID,
        laps: list[Lap],
    ) -> tuple[
        list[PushManageContextEventResponse],
        list[str],
    ]:
        starts = [
            lap.lap_start_time_ms
            for lap in laps
            if lap.lap_start_time_ms is not None
        ]

        ends = [
            self._lap_end_time_ms(lap)
            for lap in laps
            if self._lap_end_time_ms(lap) is not None
        ]

        if not starts or not ends:
            return [], ["CONTEXT_ALIGNMENT_UNAVAILABLE"]

        try:
            timeline = RaceContextService(
                self.db
            ).get_timeline(
                race_session_id=race_session_id,
                event_types=set(TimelineEventType),
                start_ms=min(starts),
                end_ms=max(ends),
                include_weather=True,
                limit=1000,
            )
        except Exception as error:
            return (
                [],
                [
                    "RACE_CONTEXT_UNAVAILABLE:"
                    f"{type(error).__name__}"
                ],
            )

        events = [
            PushManageContextEventResponse(
                event_id=event.event_id,
                event_type=event.event_type.value,
                session_time_ms=event.session_time_ms,
                occurred_at=(
                    event.occurred_at.isoformat()
                    if event.occurred_at is not None
                    else None
                ),
                title=event.title,
                message=event.message,
                severity=event.severity,
                driver_number=event.driver_number,
                lap_number=event.lap_number,
                data_quality_flags=event.data_quality_flags,
                payload=event.payload,
            )
            for event in timeline.events
            if event.session_time_ms is not None
        ]

        return events, timeline.warnings

    @staticmethod
    def _context_ids_for_lap(
        lap: Lap,
        context_events: list[PushManageContextEventResponse],
    ) -> list[str]:
        lap_end_time_ms = (
            PushManageTimelineService._lap_end_time_ms(lap)
        )

        if (
            lap.lap_start_time_ms is None
            or lap_end_time_ms is None
        ):
            return []

        return [
            event.event_id
            for event in context_events
            if event.session_time_ms is not None
            and lap.lap_start_time_ms
            <= event.session_time_ms
            <= lap_end_time_ms
        ]

    @staticmethod
    def _lap_end_time_ms(lap: Lap) -> int | None:
        if (
            lap.lap_start_time_ms is None
            or lap.lap_time_ms is None
        ):
            return None

        return lap.lap_start_time_ms + lap.lap_time_ms

    @staticmethod
    def _lap_quality_flags(lap: Lap) -> list[str]:
        flags: list[str] = []

        if lap.lap_time_ms is None:
            flags.append("MISSING_LAP_TIME")

        if lap.track_status != "1":
            flags.append("NON_GREEN_TRACK_STATUS")

        if lap.is_accurate is not True:
            flags.append("INACCURATE_LAP")

        if lap.deleted is True or lap.deleted_reason is not None:
            flags.append("DELETED_OR_INVALIDATED_LAP")

        if lap.fastf1_generated is True:
            flags.append("FASTF1_GENERATED_LAP")

        if lap.pit_in_time_ms is not None:
            flags.append("PIT_ENTRY_LAP")

        if lap.pit_out_time_ms is not None:
            flags.append("PIT_EXIT_LAP")

        return flags

    @staticmethod
    def _is_race_session(
        race_session: RaceSession,
    ) -> bool:
        return (
            (race_session.session_identifier or "").strip().upper()
            == "R"
            or (race_session.session_type or "").strip().casefold()
            == "race"
        )

    @staticmethod
    def _driver_name(driver: Driver) -> str:
        return (
            driver.full_name
            or " ".join(
                part
                for part in [
                    driver.first_name,
                    driver.last_name,
                ]
                if part
            )
            or driver.abbreviation
            or driver.driver_number
        )
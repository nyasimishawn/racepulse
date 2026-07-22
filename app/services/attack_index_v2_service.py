from decimal import Decimal
from statistics import median
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.lap import Lap
from app.schemas.attack_index_v2 import (
    AttackIndexV2Response,
    PaceContextV2Response,
    StintContextV2Response,
    TelemetryEffortComponentsV2Response,
    TelemetryEffortV2Response,
)
from app.services.attack_index_service import (
    AttackIndexService,
    RaceLapNotFoundError,
)


V2_DISCLAIMER = (
    "Telemetry effort is derived from public throttle, brake, speed, "
    "and coast proxies. Pace and stint context are descriptive and do "
    "not affect the effort score. This metric cannot prove driver intent "
    "or reveal fuel load, ERS mode, tyre temperature, traffic, track "
    "evolution, or team instructions."
)


class AttackIndexV2Service:
    def __init__(self, db: Session) -> None:
        self.db = db

    def assess(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
        race_lap_number: int,
    ) -> AttackIndexV2Response:
        v1_response = AttackIndexService(self.db).assess(
            race_session_id=race_session_id,
            driver_number=driver_number,
            race_lap_number=race_lap_number,
        )

        race_lap = self.db.get(Lap, v1_response.race_lap_id)

        if race_lap is None:
            raise RaceLapNotFoundError(
                "The requested race lap could not be loaded."
            )

        qualifying_lap = (
            self.db.get(Lap, v1_response.qualifying_lap_id)
            if v1_response.qualifying_lap_id is not None
            else None
        )

        pace_context = self._build_pace_context(
            race_lap=race_lap,
            qualifying_lap=qualifying_lap,
        )

        stint_context = StintContextV2Response(
            stint_number=race_lap.stint,
            compound=race_lap.compound,
            tyre_life=(
                float(race_lap.tyre_life)
                if race_lap.tyre_life is not None
                else None
            ),
            fresh_tyre=race_lap.fresh_tyre,
            position=race_lap.position,
            track_status=race_lap.track_status,
        )

        if not v1_response.eligible or v1_response.components is None:
            return AttackIndexV2Response(
                metric_version="attack-index-v2",
                eligible=False,
                race_session_id=v1_response.race_session_id,
                qualifying_session_id=v1_response.qualifying_session_id,
                driver_number=v1_response.driver_number,
                race_lap_id=v1_response.race_lap_id,
                race_lap_number=v1_response.race_lap_number,
                qualifying_lap_id=v1_response.qualifying_lap_id,
                qualifying_lap_number=(
                    v1_response.qualifying_lap_number
                ),
                qualifying_reference_quality=(
                    v1_response.qualifying_reference_quality
                ),
                telemetry_effort=None,
                pace_context=pace_context,
                stint_context=stint_context,
                ineligibility_reasons=(
                    v1_response.ineligibility_reasons
                ),
                disclaimer=V2_DISCLAIMER,
            )

        components = v1_response.components

        telemetry_effort = TelemetryEffortV2Response(
            score_10=v1_response.attack_score_10,
            effort_band=self._effort_band(
                v1_response.attack_score_10
            ),
            telemetry_data_confidence=(
                v1_response.confidence or "LOW"
            ),
            components=TelemetryEffortComponentsV2Response(
                reference_full_throttle_share=(
                    components.qualifying_full_throttle_share
                ),
                race_full_throttle_share=(
                    components.race_full_throttle_share
                ),
                full_throttle_share_delta=(
                    components.full_throttle_share_delta
                ),
                reference_brake_zone_share=(
                    components.qualifying_brake_zone_share
                ),
                race_brake_zone_share=(
                    components.race_brake_zone_share
                ),
                brake_zone_share_delta=(
                    components.brake_zone_share_delta
                ),
                reference_coast_candidate_distance_m=(
                    components.qualifying_coast_candidate_distance_m
                ),
                race_coast_candidate_distance_m=(
                    components.race_coast_candidate_distance_m
                ),
                extra_coast_candidate_distance_m=(
                    components.extra_coast_candidate_distance_m
                ),
                reference_sample_count=(
                    components.qualifying_sample_count
                ),
                race_sample_count=components.race_sample_count,
                common_bin_count=components.common_bin_count,
            ),
        )

        return AttackIndexV2Response(
            metric_version="attack-index-v2",
            eligible=True,
            race_session_id=v1_response.race_session_id,
            qualifying_session_id=v1_response.qualifying_session_id,
            driver_number=v1_response.driver_number,
            race_lap_id=v1_response.race_lap_id,
            race_lap_number=v1_response.race_lap_number,
            qualifying_lap_id=v1_response.qualifying_lap_id,
            qualifying_lap_number=(
                v1_response.qualifying_lap_number
            ),
            qualifying_reference_quality=(
                v1_response.qualifying_reference_quality
            ),
            telemetry_effort=telemetry_effort,
            pace_context=pace_context,
            stint_context=stint_context,
            ineligibility_reasons=[],
            disclaimer=V2_DISCLAIMER,
        )

    def _build_pace_context(
        self,
        *,
        race_lap: Lap,
        qualifying_lap: Lap | None,
    ) -> PaceContextV2Response:
        qualifying_delta_ms = self._lap_delta(
            target_time_ms=race_lap.lap_time_ms,
            reference_time_ms=(
                qualifying_lap.lap_time_ms
                if qualifying_lap is not None
                else None
            ),
        )

        qualifying_delta_percent = self._delta_percent(
            delta_ms=qualifying_delta_ms,
            reference_time_ms=(
                qualifying_lap.lap_time_ms
                if qualifying_lap is not None
                else None
            ),
        )

        baseline_time_ms, selection, sample_count = (
            self._same_stint_baseline(race_lap)
        )

        stint_delta_ms = self._lap_delta(
            target_time_ms=race_lap.lap_time_ms,
            reference_time_ms=baseline_time_ms,
        )

        stint_delta_percent = self._delta_percent(
            delta_ms=stint_delta_ms,
            reference_time_ms=baseline_time_ms,
        )

        return PaceContextV2Response(
            race_lap_time_ms=race_lap.lap_time_ms,
            qualifying_lap_time_ms=(
                qualifying_lap.lap_time_ms
                if qualifying_lap is not None
                else None
            ),
            delta_to_qualifying_ms=qualifying_delta_ms,
            delta_to_qualifying_percent=qualifying_delta_percent,
            same_stint_baseline_lap_time_ms=baseline_time_ms,
            delta_to_same_stint_baseline_ms=stint_delta_ms,
            delta_to_same_stint_baseline_percent=(
                stint_delta_percent
            ),
            same_stint_pace_band=self._pace_band(
                stint_delta_percent
            ),
            baseline_selection=selection,
            baseline_sample_count=sample_count,
        )

    def _same_stint_baseline(
        self,
        race_lap: Lap,
    ) -> tuple[float | None, str, int]:
        if (
            race_lap.stint is None
            or race_lap.compound is None
            or race_lap.lap_time_ms is None
        ):
            return None, "UNAVAILABLE", 0

        statement = self._clean_same_stint_statement(race_lap)

        if race_lap.tyre_life is not None:
            tyre_life_window = self.db.scalars(
                statement.where(
                    Lap.tyre_life >= (
                        race_lap.tyre_life - Decimal("2")
                    ),
                    Lap.tyre_life <= (
                        race_lap.tyre_life + Decimal("2")
                    ),
                )
            ).all()

            if len(tyre_life_window) >= 3:
                return (
                    self._median_lap_time_ms(tyre_life_window),
                    "SAME_STINT_TYRE_LIFE_WINDOW",
                    len(tyre_life_window),
                )

        same_stint_laps = self.db.scalars(statement).all()

        if len(same_stint_laps) >= 3:
            return (
                self._median_lap_time_ms(same_stint_laps),
                "SAME_STINT_CLEAN_LAPS",
                len(same_stint_laps),
            )

        return None, "UNAVAILABLE", len(same_stint_laps)

    @staticmethod
    def _clean_same_stint_statement(race_lap: Lap):
        return (
            select(Lap)
            .where(
                Lap.race_session_id == race_lap.race_session_id,
                Lap.driver_id == race_lap.driver_id,
                Lap.id != race_lap.id,
                Lap.stint == race_lap.stint,
                Lap.compound == race_lap.compound,
                Lap.lap_time_ms.is_not(None),
                Lap.track_status == "1",
                Lap.is_accurate.is_(True),
                Lap.deleted_reason.is_(None),
                Lap.pit_in_time_ms.is_(None),
                Lap.pit_out_time_ms.is_(None),
                or_(
                    Lap.deleted.is_(False),
                    Lap.deleted.is_(None),
                ),
                or_(
                    Lap.fastf1_generated.is_(False),
                    Lap.fastf1_generated.is_(None),
                ),
            )
            .order_by(Lap.lap_number)
        )

    @staticmethod
    def _median_lap_time_ms(laps: list[Lap]) -> float:
        return float(
            median(
                lap.lap_time_ms
                for lap in laps
                if lap.lap_time_ms is not None
            )
        )

    @staticmethod
    def _lap_delta(
        *,
        target_time_ms: int | None,
        reference_time_ms: float | int | None,
    ) -> float | int | None:
        if target_time_ms is None or reference_time_ms is None:
            return None

        return target_time_ms - reference_time_ms

    @staticmethod
    def _delta_percent(
        *,
        delta_ms: float | int | None,
        reference_time_ms: float | int | None,
    ) -> float | None:
        if (
            delta_ms is None
            or reference_time_ms is None
            or reference_time_ms <= 0
        ):
            return None

        return round((delta_ms / reference_time_ms) * 100, 3)

    @staticmethod
    def _pace_band(delta_percent: float | None) -> str | None:
        if delta_percent is None:
            return None

        if delta_percent <= -0.25:
            return "FASTER_THAN_BASELINE"

        if delta_percent <= 0.25:
            return "AT_BASELINE"

        return "SLOWER_THAN_BASELINE"

    @staticmethod
    def _effort_band(score_10: float | None) -> str:
        if score_10 is None:
            return "UNAVAILABLE"

        if score_10 >= 8.5:
            return "VERY_HIGH_ATTACK_INPUT"

        if score_10 >= 7.0:
            return "HIGH_ATTACK_INPUT"

        if score_10 >= 5.0:
            return "NORMAL_RACE_INPUT"

        if score_10 >= 3.0:
            return "MANAGING_INPUT"

        return "LOW_ATTACK_INPUT"
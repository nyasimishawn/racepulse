from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.telemetry_alignment import (
    CleanTelemetryTrace,
    RawTelemetrySample,
    build_normalized_distance_bins,
    clean_telemetry_trace,
)
from app.models.lap import Lap
from app.models.telemetry_point import TelemetryPoint
from app.schemas.telemetry_overlay import (
    TelemetryOverlayDeltaResponse,
    TelemetryOverlayPointResponse,
    TelemetryOverlayResponse,
    TelemetryTraceBinResponse,
    TelemetryTraceQualityResponse,
)
from app.services.lap_comparison_service import LapComparisonService


BIN_COUNT = 100
MIN_CLEAN_SAMPLES = 50
MIN_COMMON_BINS = 70
MIN_DISTANCE_SPAN_M = 1_000.0
MIN_TIME_COVERAGE_RATIO = 0.85

DISCLAIMER = (
    "This overlay aligns public car telemetry by normalized lap distance. "
    "It is a driving-analysis aid and cannot isolate fuel load, ERS mode, "
    "tyre temperature, traffic, track evolution, or team instructions."
)


class TelemetryOverlayService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_overlay(
        self,
        *,
        reference_session_id: UUID,
        reference_driver_number: str,
        reference_lap_number: int,
        target_session_id: UUID,
        target_driver_number: str,
        target_lap_number: int,
    ) -> TelemetryOverlayResponse:
        comparison = LapComparisonService(self.db).compare(
            reference_session_id=reference_session_id,
            reference_driver_number=reference_driver_number,
            reference_lap_number=reference_lap_number,
            target_session_id=target_session_id,
            target_driver_number=target_driver_number,
            target_lap_number=target_lap_number,
        )

        reference_lap = self.db.get(
            Lap,
            comparison.reference.lap_id,
        )
        target_lap = self.db.get(
            Lap,
            comparison.target.lap_id,
        )

        if reference_lap is None or target_lap is None:
            raise RuntimeError(
                "The selected stored lap could not be loaded."
            )

        reference_trace = self._load_trace(reference_lap)
        target_trace = self._load_trace(target_lap)

        reference_bins = build_normalized_distance_bins(
            reference_trace,
            BIN_COUNT,
        )
        target_bins = build_normalized_distance_bins(
            target_trace,
            BIN_COUNT,
        )

        reference_quality = self._quality_response(
            trace=reference_trace,
            bins=reference_bins,
        )
        target_quality = self._quality_response(
            trace=target_trace,
            bins=target_bins,
        )

        reasons = [
            *self._lap_quality_reasons(
                "REFERENCE",
                comparison.reference.quality_flags,
            ),
            *self._lap_quality_reasons(
                "TARGET",
                comparison.target.quality_flags,
            ),
            *self._trace_reasons("REFERENCE", reference_trace),
            *self._trace_reasons("TARGET", target_trace),
        ]

        warnings = list(comparison.comparison_warnings)

        if comparison.comparison_scope == "SAME_MEETING_CROSS_SESSION":
            self._append_unique(
                warnings,
                "CROSS_SESSION_FUEL_TYRE_TRAFFIC_CONTEXT",
            )

        distance_span_difference_percent = (
            self._distance_span_difference_percent(
                reference_trace.distance_span_m,
                target_trace.distance_span_m,
            )
        )

        if (
            distance_span_difference_percent is not None
            and distance_span_difference_percent > 3
        ):
            self._append_unique(
                warnings,
                "DISTANCE_SPAN_MISMATCH",
            )

        if reasons:
            return self._response(
                eligible=False,
                comparison=comparison,
                reference_session_id=reference_session_id,
                target_session_id=target_session_id,
                reference_quality=reference_quality,
                target_quality=target_quality,
                common_bin_count=0,
                distance_span_difference_percent=(
                    distance_span_difference_percent
                ),
                warnings=warnings,
                ineligibility_reasons=reasons,
                points=[],
            )

        points = self._build_overlay_points(
            reference_bins=reference_bins,
            target_bins=target_bins,
        )

        common_bin_count = len(points)

        if common_bin_count < MIN_COMMON_BINS:
            reasons.append("INSUFFICIENT_COMMON_DISTANCE_BINS")

        return self._response(
            eligible=not reasons,
            comparison=comparison,
            reference_session_id=reference_session_id,
            target_session_id=target_session_id,
            reference_quality=reference_quality,
            target_quality=target_quality,
            common_bin_count=common_bin_count,
            distance_span_difference_percent=(
                distance_span_difference_percent
            ),
            warnings=warnings,
            ineligibility_reasons=reasons,
            points=points if not reasons else [],
        )

    def _load_trace(self, lap: Lap) -> CleanTelemetryTrace:
        points = self.db.scalars(
            select(TelemetryPoint)
            .where(
                TelemetryPoint.lap_id == lap.id,
                TelemetryPoint.is_interpolated.is_(False),
                func.lower(TelemetryPoint.sample_source) == "car",
                TelemetryPoint.distance_m.is_not(None),
                TelemetryPoint.relative_time_ms.is_not(None),
                TelemetryPoint.speed_kph.is_not(None),
                TelemetryPoint.throttle_percentage.is_not(None),
                TelemetryPoint.brake_applied.is_not(None),
            )
            .order_by(TelemetryPoint.sample_index)
        ).all()

        samples = [
            RawTelemetrySample(
                sample_index=point.sample_index,
                relative_time_ms=point.relative_time_ms,
                distance_m=float(point.distance_m),
                speed_kph=float(point.speed_kph),
                throttle_percentage=float(
                    point.throttle_percentage
                ),
                brake_applied=bool(point.brake_applied),
                rpm=point.rpm,
                gear=point.gear,
                drs=point.drs,
            )
            for point in points
        ]

        return clean_telemetry_trace(
            samples=samples,
            stored_lap_time_ms=lap.lap_time_ms,
        )

    @staticmethod
    def _quality_response(
        *,
        trace: CleanTelemetryTrace,
        bins: tuple[object | None, ...],
    ) -> TelemetryTraceQualityResponse:
        return TelemetryTraceQualityResponse(
            raw_sample_count=trace.raw_sample_count,
            clean_sample_count=len(trace.samples),
            usable_bin_count=sum(
                item is not None for item in bins
            ),
            distance_span_m=trace.distance_span_m,
            time_coverage_ratio=trace.time_coverage_ratio,
        )

    @staticmethod
    def _lap_quality_reasons(
        label: str,
        quality_flags: list[str],
    ) -> list[str]:
        return [
            f"{label}_LAP_{flag}"
            for flag in quality_flags
        ]

    @staticmethod
    def _trace_reasons(
        label: str,
        trace: CleanTelemetryTrace,
    ) -> list[str]:
        reasons: list[str] = []

        if len(trace.samples) < MIN_CLEAN_SAMPLES:
            reasons.append(f"{label}_INSUFFICIENT_CLEAN_TELEMETRY")

        if (
            trace.distance_span_m is None
            or trace.distance_span_m < MIN_DISTANCE_SPAN_M
        ):
            reasons.append(f"{label}_INSUFFICIENT_DISTANCE_COVERAGE")

        if (
            trace.time_coverage_ratio is not None
            and trace.time_coverage_ratio < MIN_TIME_COVERAGE_RATIO
        ):
            reasons.append(f"{label}_INSUFFICIENT_TIME_COVERAGE")

        return reasons

    @staticmethod
    def _build_overlay_points(
        *,
        reference_bins: tuple[object | None, ...],
        target_bins: tuple[object | None, ...],
    ) -> list[TelemetryOverlayPointResponse]:
        points: list[TelemetryOverlayPointResponse] = []

        for reference_bin, target_bin in zip(
            reference_bins,
            target_bins,
            strict=True,
        ):
            if reference_bin is None or target_bin is None:
                continue

            reference = TelemetryTraceBinResponse(
                speed_kph=reference_bin.speed_kph,
                throttle_percentage=(
                    reference_bin.throttle_percentage
                ),
                brake_share=reference_bin.brake_share,
                rpm=reference_bin.rpm,
                gear=reference_bin.gear,
                drs=reference_bin.drs,
                source_sample_count=(
                    reference_bin.source_sample_count
                ),
            )

            target = TelemetryTraceBinResponse(
                speed_kph=target_bin.speed_kph,
                throttle_percentage=target_bin.throttle_percentage,
                brake_share=target_bin.brake_share,
                rpm=target_bin.rpm,
                gear=target_bin.gear,
                drs=target_bin.drs,
                source_sample_count=target_bin.source_sample_count,
            )

            points.append(
                TelemetryOverlayPointResponse(
                    normalized_progress=(
                        reference_bin.normalized_progress
                    ),
                    reference=reference,
                    target=target,
                    delta=TelemetryOverlayDeltaResponse(
                        speed_kph=TelemetryOverlayService._delta(
                            target.speed_kph,
                            reference.speed_kph,
                        ),
                        throttle_percentage=(
                            TelemetryOverlayService._delta(
                                target.throttle_percentage,
                                reference.throttle_percentage,
                            )
                        ),
                        brake_share=TelemetryOverlayService._delta(
                            target.brake_share,
                            reference.brake_share,
                        ),
                    ),
                )
            )

        return points

    @staticmethod
    def _response(
        *,
        eligible: bool,
        comparison: object,
        reference_session_id: UUID,
        target_session_id: UUID,
        reference_quality: TelemetryTraceQualityResponse,
        target_quality: TelemetryTraceQualityResponse,
        common_bin_count: int,
        distance_span_difference_percent: float | None,
        warnings: list[str],
        ineligibility_reasons: list[str],
        points: list[TelemetryOverlayPointResponse],
    ) -> TelemetryOverlayResponse:
        return TelemetryOverlayResponse(
            overlay_version="telemetry-overlay-v1",
            eligible=eligible,
            alignment="NORMALIZED_DISTANCE_BIN_MEDIAN",
            bin_count=BIN_COUNT,
            common_bin_count=common_bin_count,
            coverage_percent=round(
                common_bin_count / BIN_COUNT * 100,
                2,
            ),
            comparison_scope=comparison.comparison_scope,
            reference_session_id=reference_session_id,
            target_session_id=target_session_id,
            reference_lap_id=comparison.reference.lap_id,
            target_lap_id=comparison.target.lap_id,
            reference_quality=reference_quality,
            target_quality=target_quality,
            distance_span_difference_percent=(
                distance_span_difference_percent
            ),
            warnings=warnings,
            ineligibility_reasons=ineligibility_reasons,
            points=points,
            disclaimer=DISCLAIMER,
        )

    @staticmethod
    def _distance_span_difference_percent(
        reference_span_m: float | None,
        target_span_m: float | None,
    ) -> float | None:
        if (
            reference_span_m is None
            or target_span_m is None
            or reference_span_m <= 0
            or target_span_m <= 0
        ):
            return None

        return round(
            abs(reference_span_m - target_span_m)
            / max(reference_span_m, target_span_m)
            * 100,
            3,
        )

    @staticmethod
    def _delta(
        target_value: float | None,
        reference_value: float | None,
    ) -> float | None:
        if target_value is None or reference_value is None:
            return None

        return round(target_value - reference_value, 3)

    @staticmethod
    def _append_unique(items: list[str], value: str) -> None:
        if value not in items:
            items.append(value)
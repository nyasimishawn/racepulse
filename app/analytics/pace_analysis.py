from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence


MIN_REGRESSION_POINTS = 3
TREND_THRESHOLD_MS_PER_AXIS_UNIT = 50.0


@dataclass(frozen=True)
class PacePoint:
    lap_number: int
    axis_value: float
    lap_time_ms: int


@dataclass(frozen=True)
class PaceTrend:
    axis: str
    sample_count: int
    opening_pace_ms: float | None
    closing_pace_ms: float | None
    slope_ms_per_axis_unit: float | None
    net_pace_change_ms: int | None
    r_squared: float | None
    residual_mad_ms: float | None
    direction: str
    data_quality_flags: tuple[str, ...]


def calculate_pace_trend(
    points: Sequence[PacePoint],
    *,
    axis: str,
) -> PaceTrend:
    ordered = sorted(
        points,
        key=lambda point: (point.axis_value, point.lap_number),
    )
    lap_times = [point.lap_time_ms for point in ordered]
    sample_count = len(ordered)

    opening_pace_ms = (
        float(median(lap_times[: min(3, sample_count)]))
        if lap_times
        else None
    )
    closing_pace_ms = (
        float(median(lap_times[-min(3, sample_count) :]))
        if lap_times
        else None
    )

    if sample_count < MIN_REGRESSION_POINTS:
        return PaceTrend(
            axis=axis,
            sample_count=sample_count,
            opening_pace_ms=opening_pace_ms,
            closing_pace_ms=closing_pace_ms,
            slope_ms_per_axis_unit=None,
            net_pace_change_ms=None,
            r_squared=None,
            residual_mad_ms=None,
            direction="UNAVAILABLE",
            data_quality_flags=("INSUFFICIENT_TREND_SAMPLE",),
        )

    x_values = [point.axis_value for point in ordered]
    y_values = [float(point.lap_time_ms) for point in ordered]

    x_mean = sum(x_values) / sample_count
    y_mean = sum(y_values) / sample_count

    denominator = sum((value - x_mean) ** 2 for value in x_values)

    if denominator == 0:
        return PaceTrend(
            axis=axis,
            sample_count=sample_count,
            opening_pace_ms=opening_pace_ms,
            closing_pace_ms=closing_pace_ms,
            slope_ms_per_axis_unit=None,
            net_pace_change_ms=None,
            r_squared=None,
            residual_mad_ms=None,
            direction="UNAVAILABLE",
            data_quality_flags=("CONSTANT_TREND_AXIS",),
        )

    slope = sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values, strict=True)
    ) / denominator

    intercept = y_mean - (slope * x_mean)

    predicted = [
        intercept + (slope * x_value)
        for x_value in x_values
    ]

    residuals = [
        actual - fitted
        for actual, fitted in zip(y_values, predicted, strict=True)
    ]

    total_variation = sum(
        (value - y_mean) ** 2
        for value in y_values
    )
    residual_variation = sum(value**2 for value in residuals)

    r_squared = (
        None
        if total_variation == 0
        else max(
            0.0,
            min(
                1.0,
                1 - (residual_variation / total_variation),
            ),
        )
    )

    residual_mad_ms = float(
        median(abs(value) for value in residuals)
    )

    net_pace_change_ms = int(
        round(slope * (max(x_values) - min(x_values)))
    )

    if slope >= TREND_THRESHOLD_MS_PER_AXIS_UNIT:
        direction = "PACE_FALLING"
    elif slope <= -TREND_THRESHOLD_MS_PER_AXIS_UNIT:
        direction = "PACE_IMPROVING"
    else:
        direction = "STABLE"

    return PaceTrend(
        axis=axis,
        sample_count=sample_count,
        opening_pace_ms=opening_pace_ms,
        closing_pace_ms=closing_pace_ms,
        slope_ms_per_axis_unit=round(slope, 2),
        net_pace_change_ms=net_pace_change_ms,
        r_squared=(
            round(r_squared, 4)
            if r_squared is not None
            else None
        ),
        residual_mad_ms=round(residual_mad_ms, 2),
        direction=direction,
        data_quality_flags=(),
    )
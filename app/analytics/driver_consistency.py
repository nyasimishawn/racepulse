from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence


@dataclass(frozen=True)
class DriverConsistency:
    sample_count: int
    median_absolute_deviation_ms: float | None
    p90_p10_spread_ms: float | None
    score_10: float | None
    data_quality_flags: tuple[str, ...]


def calculate_driver_consistency(
    lap_times_ms: Sequence[int],
) -> DriverConsistency:
    values = sorted(float(value) for value in lap_times_ms)
    sample_count = len(values)

    if not values:
        return DriverConsistency(
            sample_count=0,
            median_absolute_deviation_ms=None,
            p90_p10_spread_ms=None,
            score_10=None,
            data_quality_flags=("NO_CLEAN_LAPS_FOR_CONSISTENCY",),
        )

    centre = float(median(values))
    mad = float(
        median(abs(value - centre) for value in values)
    )

    if sample_count < 3:
        return DriverConsistency(
            sample_count=sample_count,
            median_absolute_deviation_ms=round(mad, 2),
            p90_p10_spread_ms=None,
            score_10=None,
            data_quality_flags=("LIMITED_CLEAN_LAP_SAMPLE",),
        )

    p10 = _percentile(values, 0.10)
    p90 = _percentile(values, 0.90)

    # Timing-consistency score, not a measure of driver intent.
    score = 10 / (1 + (mad / 250))

    return DriverConsistency(
        sample_count=sample_count,
        median_absolute_deviation_ms=round(mad, 2),
        p90_p10_spread_ms=round(p90 - p10, 2),
        score_10=round(max(0.0, min(10.0, score)), 1),
        data_quality_flags=(),
    )


def _percentile(
    values: Sequence[float],
    quantile: float,
) -> float:
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)

    if lower == upper:
        return values[lower]

    return values[lower] + (
        (values[upper] - values[lower])
        * (position - lower)
    )
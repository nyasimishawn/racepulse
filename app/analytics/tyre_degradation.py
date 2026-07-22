from __future__ import annotations

from dataclasses import dataclass

from app.analytics.pace_analysis import PaceTrend


@dataclass(frozen=True)
class TyrePaceProxy:
    classification: str
    estimated_pace_change_ms_per_axis_unit: float | None
    estimated_pace_change_over_stint_ms: int | None
    data_quality_flags: tuple[str, ...]


def estimate_tyre_pace_proxy(
    trend: PaceTrend,
) -> TyrePaceProxy:
    if trend.slope_ms_per_axis_unit is None:
        return TyrePaceProxy(
            classification="UNAVAILABLE",
            estimated_pace_change_ms_per_axis_unit=None,
            estimated_pace_change_over_stint_ms=None,
            data_quality_flags=trend.data_quality_flags,
        )

    classification_by_direction = {
        "PACE_FALLING": "PACE_LOSS_PROXY",
        "PACE_IMPROVING": "PACE_GAIN_PROXY",
        "STABLE": "STABLE_PACE_PROXY",
    }

    return TyrePaceProxy(
        classification=classification_by_direction[trend.direction],
        estimated_pace_change_ms_per_axis_unit=(
            trend.slope_ms_per_axis_unit
        ),
        estimated_pace_change_over_stint_ms=(
            trend.net_pace_change_ms
        ),
        data_quality_flags=trend.data_quality_flags,
    )
from app.analytics.pace_analysis import (
    PacePoint,
    calculate_pace_trend,
)
from app.analytics.tyre_degradation import estimate_tyre_pace_proxy


def make_point(
    lap_number: int,
    axis_value: float,
    lap_time_ms: int,
) -> PacePoint:
    return PacePoint(
        lap_number=lap_number,
        axis_value=axis_value,
        lap_time_ms=lap_time_ms,
    )


def test_identifies_falling_pace_from_consistent_lap_time_loss() -> None:
    trend = calculate_pace_trend(
        [
            make_point(1, 1, 100_000),
            make_point(2, 2, 100_100),
            make_point(3, 3, 100_200),
            make_point(4, 4, 100_300),
        ],
        axis="TYRE_LIFE",
    )

    assert trend.direction == "PACE_FALLING"
    assert trend.sample_count == 4
    assert trend.slope_ms_per_axis_unit == 100.0
    assert trend.net_pace_change_ms == 300
    assert trend.r_squared == 1.0
    assert trend.data_quality_flags == ()


def test_identifies_improving_pace() -> None:
    trend = calculate_pace_trend(
        [
            make_point(1, 1, 100_300),
            make_point(2, 2, 100_200),
            make_point(3, 3, 100_100),
            make_point(4, 4, 100_000),
        ],
        axis="TYRE_LIFE",
    )

    assert trend.direction == "PACE_IMPROVING"
    assert trend.slope_ms_per_axis_unit == -100.0
    assert trend.net_pace_change_ms == -300


def test_identifies_stable_pace_inside_threshold() -> None:
    trend = calculate_pace_trend(
        [
            make_point(1, 1, 100_000),
            make_point(2, 2, 100_025),
            make_point(3, 3, 100_050),
        ],
        axis="TYRE_LIFE",
    )

    assert trend.direction == "STABLE"
    assert trend.slope_ms_per_axis_unit == 25.0
    assert trend.net_pace_change_ms == 50


def test_returns_unavailable_for_insufficient_points() -> None:
    trend = calculate_pace_trend(
        [
            make_point(1, 1, 100_000),
            make_point(2, 2, 100_100),
        ],
        axis="TYRE_LIFE",
    )

    assert trend.direction == "UNAVAILABLE"
    assert trend.slope_ms_per_axis_unit is None
    assert trend.data_quality_flags == ("INSUFFICIENT_TREND_SAMPLE",)


def test_returns_unavailable_for_constant_axis() -> None:
    trend = calculate_pace_trend(
        [
            make_point(1, 5, 100_000),
            make_point(2, 5, 100_100),
            make_point(3, 5, 100_200),
        ],
        axis="TYRE_LIFE",
    )

    assert trend.direction == "UNAVAILABLE"
    assert trend.slope_ms_per_axis_unit is None
    assert trend.data_quality_flags == ("CONSTANT_TREND_AXIS",)


def test_maps_falling_pace_to_tyre_pace_loss_proxy() -> None:
    trend = calculate_pace_trend(
        [
            make_point(1, 1, 100_000),
            make_point(2, 2, 100_100),
            make_point(3, 3, 100_200),
        ],
        axis="TYRE_LIFE",
    )

    proxy = estimate_tyre_pace_proxy(trend)

    assert proxy.classification == "PACE_LOSS_PROXY"
    assert proxy.estimated_pace_change_ms_per_axis_unit == 100.0
    assert proxy.estimated_pace_change_over_stint_ms == 200
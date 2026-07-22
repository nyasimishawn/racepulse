from app.analytics.driver_consistency import (
    calculate_driver_consistency,
)


def test_returns_no_data_flag_when_no_laps_are_available() -> None:
    result = calculate_driver_consistency([])

    assert result.sample_count == 0
    assert result.score_10 is None
    assert result.data_quality_flags == (
        "NO_CLEAN_LAPS_FOR_CONSISTENCY",
    )


def test_returns_limited_sample_flag_for_two_laps() -> None:
    result = calculate_driver_consistency(
        [100_000, 100_200],
    )

    assert result.sample_count == 2
    assert result.median_absolute_deviation_ms == 100.0
    assert result.score_10 is None
    assert result.data_quality_flags == (
        "LIMITED_CLEAN_LAP_SAMPLE",
    )


def test_perfectly_consistent_laps_score_ten() -> None:
    result = calculate_driver_consistency(
        [100_000, 100_000, 100_000, 100_000, 100_000],
    )

    assert result.sample_count == 5
    assert result.median_absolute_deviation_ms == 0.0
    assert result.p90_p10_spread_ms == 0.0
    assert result.score_10 == 10.0
    assert result.data_quality_flags == ()


def test_variable_laps_receive_lower_consistency_score() -> None:
    result = calculate_driver_consistency(
        [100_000, 100_250, 100_500, 100_750, 101_000],
    )

    assert result.sample_count == 5
    assert result.median_absolute_deviation_ms == 250.0
    assert result.p90_p10_spread_ms == 800.0
    assert result.score_10 == 5.0
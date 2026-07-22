from types import SimpleNamespace

from app.analytics.compound_pace import (
    build_driver_compound_baselines,
    score_lap_against_compound_baseline,
)
from app.analytics.lift_and_coast import (
    TelemetrySample,
    detect_lift_and_coast,
)


def lap(lap_number, lap_time_ms, compound):
    return SimpleNamespace(
        lap_number=lap_number,
        lap_time_ms=lap_time_ms,
        compound=compound,
    )


def test_compound_baseline_is_driver_and_compound_relative() -> None:
    laps = [
        lap(1, 100_000, "SOFT"),
        lap(2, 100_300, "SOFT"),
        lap(3, 101_000, "SOFT"),
        lap(4, 102_000, "HARD"),
        lap(5, 102_200, "HARD"),
    ]

    baselines = build_driver_compound_baselines({"44": laps})

    soft = baselines[("44", "SOFT")]
    hard = baselines[("44", "HARD")]

    assert soft.robust_fastest_lap_time_ms == 100_300.0
    assert hard.robust_fastest_lap_time_ms == 102_100.0
    assert hard.data_quality_flags == ("LIMITED_BASELINE_SAMPLE",)

    score = score_lap_against_compound_baseline(
        lap(6, 102_250, "HARD"),
        hard,
    )

    assert score.delta_to_compound_baseline_ms == 150.0
    assert score.classification == "ON_BASELINE"


def test_lift_and_coast_detects_persistent_pre_brake_lifts() -> None:
    samples = []
    for lap_number in [10, 11]:
        samples.extend(
            [
                TelemetrySample(lap_number, 700.0, 1, 310.0, 100.0, False),
                TelemetrySample(lap_number, 760.0, 2, 306.0, 100.0, False),
                TelemetrySample(lap_number, 820.0, 3, 299.0, 20.0, False),
                TelemetrySample(lap_number, 880.0, 4, 292.0, 15.0, False),
                TelemetrySample(lap_number, 940.0, 5, 285.0, 10.0, False),
                TelemetrySample(lap_number, 1_000.0, 6, 278.0, 0.0, True),
                TelemetrySample(lap_number, 1_040.0, 7, 250.0, 0.0, True),
                TelemetrySample(lap_number, 1_120.0, 8, 220.0, 45.0, False),
            ]
        )

    analysis = detect_lift_and_coast(samples)

    assert analysis.data_quality_flags == ()
    assert analysis.persistence_lap_ranges == ("10-11",)
    assert [summary.classification for summary in analysis.lap_summaries] == [
        "INFERRED_LIFT_AND_COAST",
        "INFERRED_LIFT_AND_COAST",
    ]
    assert analysis.zone_observations[0].lift_distance_before_brake_m == 180.0
    assert "SPEED_DROP_BEFORE_BRAKE" in analysis.zone_observations[0].evidence_tags

import pytest

from app.analytics.push_manage import classify_observed_effort


@pytest.mark.parametrize(
    ("score", "expected_mode", "expected_tag"),
    [
        (8.5, "PUSHING_LIKE", "VERY_HIGH_TELEMETRY_EFFORT"),
        (7.0, "HIGH_RACE_EFFORT", "HIGH_TELEMETRY_EFFORT"),
        (5.0, "RACING_LIKE", "NORMAL_RACE_TELEMETRY_EFFORT"),
        (3.0, "MANAGING_LIKE", "MANAGEMENT_LIKE_TELEMETRY_EFFORT"),
        (2.9, "COASTING_LIKE", "LOW_TELEMETRY_EFFORT"),
    ],
)
def test_classifies_observed_effort_score_bands(
    score: float,
    expected_mode: str,
    expected_tag: str,
) -> None:
    result = classify_observed_effort(
        effort_score_10=score,
        same_stint_pace_band=None,
        extra_coast_candidate_distance_m=None,
    )

    assert result.mode == expected_mode
    assert result.evidence_tags == [expected_tag]


def test_marks_missing_telemetry_as_not_scored() -> None:
    result = classify_observed_effort(
        effort_score_10=None,
        same_stint_pace_band="FASTER_THAN_STINT_MEDIAN",
        extra_coast_candidate_distance_m=50.0,
    )

    assert result.mode == "NOT_SCORED"
    assert result.evidence_tags == ["TELEMETRY_NOT_AVAILABLE"]


def test_adds_pace_and_coasting_context_tags() -> None:
    result = classify_observed_effort(
        effort_score_10=4.0,
        same_stint_pace_band="SLOWER_THAN_STINT_MEDIAN",
        extra_coast_candidate_distance_m=25.0,
    )

    assert result.mode == "MANAGING_LIKE"
    assert result.evidence_tags == [
        "MANAGEMENT_LIKE_TELEMETRY_EFFORT",
        "PACE_SLOWER_THAN_STINT_MEDIAN",
        "EXTRA_COAST_CANDIDATES_DETECTED",
    ]
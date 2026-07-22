from dataclasses import dataclass


@dataclass(frozen=True)
class ObservedEffortMode:
    mode: str
    evidence_tags: list[str]


def classify_observed_effort(
    *,
    effort_score_10: float | None,
    same_stint_pace_band: str | None,
    extra_coast_candidate_distance_m: float | None,
) -> ObservedEffortMode:
    if effort_score_10 is None:
        return ObservedEffortMode(
            mode="NOT_SCORED",
            evidence_tags=["TELEMETRY_NOT_AVAILABLE"],
        )

    if effort_score_10 >= 8.5:
        mode = "PUSHING_LIKE"
        tags = ["VERY_HIGH_TELEMETRY_EFFORT"]
    elif effort_score_10 >= 7.0:
        mode = "HIGH_RACE_EFFORT"
        tags = ["HIGH_TELEMETRY_EFFORT"]
    elif effort_score_10 >= 5.0:
        mode = "RACING_LIKE"
        tags = ["NORMAL_RACE_TELEMETRY_EFFORT"]
    elif effort_score_10 >= 3.0:
        mode = "MANAGING_LIKE"
        tags = ["MANAGEMENT_LIKE_TELEMETRY_EFFORT"]
    else:
        mode = "COASTING_LIKE"
        tags = ["LOW_TELEMETRY_EFFORT"]

    if same_stint_pace_band is not None:
        tags.append(f"PACE_{same_stint_pace_band}")

    if (
        extra_coast_candidate_distance_m is not None
        and extra_coast_candidate_distance_m > 0
    ):
        tags.append("EXTRA_COAST_CANDIDATES_DETECTED")

    return ObservedEffortMode(
        mode=mode,
        evidence_tags=tags,
    )
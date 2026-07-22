from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


MIN_LIFT_COAST_SAMPLES = 8
DEFAULT_PRE_BRAKE_WINDOW_M = 260.0
DEFAULT_MIN_LIFT_DISTANCE_M = 70.0
DEFAULT_THROTTLE_LIFT_THRESHOLD = 35.0


@dataclass(frozen=True)
class TelemetrySample:
    lap_number: int
    distance_m: float | None
    relative_time_ms: int
    speed_kph: float | None
    throttle_percentage: float | None
    brake_applied: bool | None


@dataclass(frozen=True)
class LiftCoastZoneObservation:
    lap_number: int
    zone_index: int
    brake_distance_m: float
    lift_start_distance_m: float | None
    lift_distance_before_brake_m: float | None
    min_throttle_before_brake: float | None
    speed_at_lift_kph: float | None
    speed_at_brake_kph: float | None
    classification: str
    evidence_tags: tuple[str, ...]


@dataclass(frozen=True)
class LiftCoastLapSummary:
    lap_number: int
    inferred_zone_count: int
    strongest_lift_distance_before_brake_m: float | None
    classification: str
    evidence_tags: tuple[str, ...]


@dataclass(frozen=True)
class LiftCoastAnalysis:
    lap_summaries: tuple[LiftCoastLapSummary, ...]
    zone_observations: tuple[LiftCoastZoneObservation, ...]
    persistence_lap_ranges: tuple[str, ...]
    data_quality_flags: tuple[str, ...]


def detect_lift_and_coast(
    samples: Sequence[TelemetrySample],
    *,
    pre_brake_window_m: float = DEFAULT_PRE_BRAKE_WINDOW_M,
    min_lift_distance_m: float = DEFAULT_MIN_LIFT_DISTANCE_M,
    throttle_lift_threshold: float = DEFAULT_THROTTLE_LIFT_THRESHOLD,
) -> LiftCoastAnalysis:
    if len(samples) < MIN_LIFT_COAST_SAMPLES:
        return LiftCoastAnalysis(
            lap_summaries=(),
            zone_observations=(),
            persistence_lap_ranges=(),
            data_quality_flags=("INSUFFICIENT_TELEMETRY_SAMPLE",),
        )

    samples_by_lap: dict[int, list[TelemetrySample]] = {}
    for sample in samples:
        samples_by_lap.setdefault(sample.lap_number, []).append(sample)

    observations: list[LiftCoastZoneObservation] = []
    summaries: list[LiftCoastLapSummary] = []
    flags: list[str] = []

    if any(sample.distance_m is None for sample in samples):
        flags.append("MISSING_DISTANCE_DATA")

    if any(sample.throttle_percentage is None for sample in samples):
        flags.append("MISSING_THROTTLE_DATA")

    if any(sample.brake_applied is None for sample in samples):
        flags.append("MISSING_BRAKE_DATA")

    for lap_number, lap_samples in sorted(samples_by_lap.items()):
        ordered = sorted(
            lap_samples,
            key=lambda sample: (
                sample.distance_m
                if sample.distance_m is not None
                else float("inf"),
                sample.relative_time_ms,
            ),
        )
        lap_observations = _analyse_lap(
            lap_number=lap_number,
            samples=ordered,
            pre_brake_window_m=pre_brake_window_m,
            min_lift_distance_m=min_lift_distance_m,
            throttle_lift_threshold=throttle_lift_threshold,
        )
        observations.extend(lap_observations)

        inferred = [
            observation
            for observation in lap_observations
            if observation.classification == "INFERRED_LIFT_AND_COAST"
        ]
        strongest = None
        if inferred:
            strongest = max(
                observation.lift_distance_before_brake_m or 0.0
                for observation in inferred
            )

        evidence = sorted(
            {
                tag
                for observation in inferred
                for tag in observation.evidence_tags
            }
        )

        summaries.append(
            LiftCoastLapSummary(
                lap_number=lap_number,
                inferred_zone_count=len(inferred),
                strongest_lift_distance_before_brake_m=(
                    round(strongest, 2)
                    if strongest is not None
                    else None
                ),
                classification=(
                    "INFERRED_LIFT_AND_COAST"
                    if inferred
                    else "NO_PATTERN_DETECTED"
                ),
                evidence_tags=tuple(evidence),
            )
        )

    return LiftCoastAnalysis(
        lap_summaries=tuple(summaries),
        zone_observations=tuple(observations),
        persistence_lap_ranges=_persistence_ranges(summaries),
        data_quality_flags=tuple(sorted(set(flags))),
    )


def _analyse_lap(
    *,
    lap_number: int,
    samples: Sequence[TelemetrySample],
    pre_brake_window_m: float,
    min_lift_distance_m: float,
    throttle_lift_threshold: float,
) -> list[LiftCoastZoneObservation]:
    braking_onsets = _braking_onset_indexes(samples)
    observations: list[LiftCoastZoneObservation] = []

    for zone_index, brake_index in enumerate(braking_onsets, start=1):
        brake_sample = samples[brake_index]
        if brake_sample.distance_m is None:
            continue

        window = [
            sample
            for sample in samples[:brake_index]
            if sample.distance_m is not None
            and brake_sample.distance_m - pre_brake_window_m
            <= sample.distance_m
            < brake_sample.distance_m
        ]

        lifted = [
            sample
            for sample in window
            if sample.brake_applied is not True
            and sample.throttle_percentage is not None
            and sample.throttle_percentage <= throttle_lift_threshold
        ]

        if not lifted:
            observations.append(
                _zone_observation(
                    lap_number=lap_number,
                    zone_index=zone_index,
                    brake_sample=brake_sample,
                    lift_sample=None,
                    min_throttle=_min_throttle(window),
                    classification="NO_PATTERN_DETECTED",
                    evidence_tags=(),
                )
            )
            continue

        lift_sample = lifted[0]
        lift_distance = brake_sample.distance_m - lift_sample.distance_m
        has_speed_drop = _speed_drop_kph(
            lift_sample,
            brake_sample,
        )

        evidence = ["THROTTLE_LIFT_BEFORE_BRAKE"]
        if has_speed_drop is not None and has_speed_drop >= 5.0:
            evidence.append("SPEED_DROP_BEFORE_BRAKE")

        classification = (
            "INFERRED_LIFT_AND_COAST"
            if lift_distance >= min_lift_distance_m
            else "SHORT_THROTTLE_LIFT"
        )

        observations.append(
            _zone_observation(
                lap_number=lap_number,
                zone_index=zone_index,
                brake_sample=brake_sample,
                lift_sample=lift_sample,
                min_throttle=_min_throttle(window),
                classification=classification,
                evidence_tags=tuple(evidence),
            )
        )

    return observations


def _braking_onset_indexes(
    samples: Sequence[TelemetrySample],
) -> list[int]:
    onsets: list[int] = []
    was_braking = False

    for index, sample in enumerate(samples):
        is_braking = sample.brake_applied is True
        if is_braking and not was_braking:
            onsets.append(index)
        was_braking = is_braking

    return onsets


def _zone_observation(
    *,
    lap_number: int,
    zone_index: int,
    brake_sample: TelemetrySample,
    lift_sample: TelemetrySample | None,
    min_throttle: float | None,
    classification: str,
    evidence_tags: tuple[str, ...],
) -> LiftCoastZoneObservation:
    lift_distance = None
    if lift_sample is not None and lift_sample.distance_m is not None:
        lift_distance = brake_sample.distance_m - lift_sample.distance_m

    return LiftCoastZoneObservation(
        lap_number=lap_number,
        zone_index=zone_index,
        brake_distance_m=round(brake_sample.distance_m or 0.0, 2),
        lift_start_distance_m=(
            round(lift_sample.distance_m, 2)
            if lift_sample is not None
            and lift_sample.distance_m is not None
            else None
        ),
        lift_distance_before_brake_m=(
            round(lift_distance, 2)
            if lift_distance is not None
            else None
        ),
        min_throttle_before_brake=min_throttle,
        speed_at_lift_kph=(
            lift_sample.speed_kph
            if lift_sample is not None
            else None
        ),
        speed_at_brake_kph=brake_sample.speed_kph,
        classification=classification,
        evidence_tags=evidence_tags,
    )


def _min_throttle(
    samples: Sequence[TelemetrySample],
) -> float | None:
    values = [
        sample.throttle_percentage
        for sample in samples
        if sample.throttle_percentage is not None
    ]
    if not values:
        return None

    return round(float(min(values)), 2)


def _speed_drop_kph(
    lift_sample: TelemetrySample,
    brake_sample: TelemetrySample,
) -> float | None:
    if lift_sample.speed_kph is None or brake_sample.speed_kph is None:
        return None

    return lift_sample.speed_kph - brake_sample.speed_kph


def _persistence_ranges(
    summaries: Sequence[LiftCoastLapSummary],
) -> tuple[str, ...]:
    ranges: list[str] = []
    active_start: int | None = None
    previous_lap: int | None = None

    for summary in summaries:
        detected = summary.classification == "INFERRED_LIFT_AND_COAST"
        if detected and active_start is None:
            active_start = summary.lap_number
        elif (
            detected
            and previous_lap is not None
            and summary.lap_number != previous_lap + 1
        ):
            _append_range(ranges, active_start, previous_lap)
            active_start = summary.lap_number
        elif not detected and active_start is not None:
            _append_range(ranges, active_start, previous_lap)
            active_start = None

        previous_lap = summary.lap_number

    if active_start is not None:
        _append_range(ranges, active_start, previous_lap)

    return tuple(ranges)


def _append_range(
    ranges: list[str],
    start: int | None,
    end: int | None,
) -> None:
    if start is None or end is None or end <= start:
        return

    ranges.append(f"{start}-{end}")


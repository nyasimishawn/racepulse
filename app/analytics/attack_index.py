from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Sequence


MIN_SAMPLE_COUNT = 50
MIN_COMMON_BINS = 60

HIGH_SPEED_KPH = 120.0
FULL_THROTTLE_PERCENTAGE = 95.0
LIFT_THROTTLE_PERCENTAGE = 10.0

BRAKE_SHARE_THRESHOLD = 0.25
COAST_BRAKE_SHARE_LIMIT = 0.10


class InsufficientTelemetryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    distance_m: float
    speed_kph: float
    throttle_percentage: float
    brake_applied: bool


@dataclass(frozen=True, slots=True)
class AttackIndexResult:
    metric_version: str

    attack_score_10: float
    classification: str
    confidence: str

    qualifying_sample_count: int
    race_sample_count: int
    common_bin_count: int

    qualifying_full_throttle_share: float
    race_full_throttle_share: float
    full_throttle_share_delta: float

    qualifying_brake_zone_share: float
    race_brake_zone_share: float
    brake_zone_share_delta: float

    qualifying_coast_candidate_distance_m: float
    race_coast_candidate_distance_m: float
    extra_coast_candidate_distance_m: float

    disclaimer: str


@dataclass(frozen=True, slots=True)
class _TelemetryBin:
    throttle_percentage: float
    speed_kph: float
    brake_share: float
    sample_count: int


def calculate_attack_index(
    qualifying_samples: Sequence[TelemetrySample],
    race_samples: Sequence[TelemetrySample],
    *,
    bin_count: int = 100,
) -> AttackIndexResult:
    """
    Estimate driving effort from public telemetry.

    This is a driving-effort proxy, not proof of driver or team intent.
    It does not use lap-time delta in the score because fuel, tyre age,
    traffic, DRS, weather, and strategy make race-vs-qualifying pace
    directly incomparable.
    """
    if bin_count < 20:
        raise ValueError("bin_count must be at least 20.")

    clean_qualifying = _clean_samples(qualifying_samples)
    clean_race = _clean_samples(race_samples)

    if len(clean_qualifying) < MIN_SAMPLE_COUNT:
        raise InsufficientTelemetryError(
            "Qualifying telemetry does not contain enough clean samples."
        )

    if len(clean_race) < MIN_SAMPLE_COUNT:
        raise InsufficientTelemetryError(
            "Race telemetry does not contain enough clean samples."
        )

    qualifying_bins = _build_bins(clean_qualifying, bin_count)
    race_bins = _build_bins(clean_race, bin_count)

    common_bin_count = sum(
        qualifying_bin is not None and race_bin is not None
        for qualifying_bin, race_bin in zip(
            qualifying_bins,
            race_bins,
            strict=True,
        )
    )

    if common_bin_count < MIN_COMMON_BINS:
        raise InsufficientTelemetryError(
            "Telemetry coverage is too incomplete for a fair comparison."
        )

    qualifying_wot_share = _full_throttle_share(qualifying_bins)
    race_wot_share = _full_throttle_share(race_bins)

    qualifying_brake_share = _brake_zone_share(qualifying_bins)
    race_brake_share = _brake_zone_share(race_bins)

    approach_indexes = _reference_braking_approach_indexes(
        qualifying_bins
    )

    if not approach_indexes:
        raise InsufficientTelemetryError(
            "No reliable qualifying braking approaches were found."
        )

    qualifying_coast_count = _coast_candidate_count(
        reference_bins=qualifying_bins,
        target_bins=qualifying_bins,
        approach_indexes=approach_indexes,
    )

    race_coast_count = _coast_candidate_count(
        reference_bins=qualifying_bins,
        target_bins=race_bins,
        approach_indexes=approach_indexes,
    )

    track_distance_m = max(
        _lap_distance(clean_qualifying),
        _lap_distance(clean_race),
    )

    metres_per_bin = track_distance_m / bin_count

    qualifying_coast_distance = qualifying_coast_count * metres_per_bin
    race_coast_distance = race_coast_count * metres_per_bin
    extra_coast_distance = max(
        0.0,
        race_coast_distance - qualifying_coast_distance,
    )

    throttle_management = _clamp_01(
        (qualifying_wot_share - race_wot_share) / 0.12
    )

    brake_management = _clamp_01(
        (qualifying_brake_share - race_brake_share) / 0.20
    )

    coast_management = _clamp_01(
        (
            (race_coast_count - qualifying_coast_count)
            / max(1, len(approach_indexes))
        )
        / 0.15
    )

    management_score = 100.0 * (
        0.45 * throttle_management
        + 0.25 * brake_management
        + 0.30 * coast_management
    )

    attack_score = round(
        max(0.0, min(10.0, 10.0 * (1.0 - management_score / 100.0))),
        1,
    )

    return AttackIndexResult(
        metric_version="attack-index-v1",
        attack_score_10=attack_score,
        classification=_classification(attack_score),
        confidence=_confidence(
            qualifying_sample_count=len(clean_qualifying),
            race_sample_count=len(clean_race),
            common_bin_count=common_bin_count,
            braking_approach_count=len(approach_indexes),
        ),
        qualifying_sample_count=len(clean_qualifying),
        race_sample_count=len(clean_race),
        common_bin_count=common_bin_count,
        qualifying_full_throttle_share=round(qualifying_wot_share, 4),
        race_full_throttle_share=round(race_wot_share, 4),
        full_throttle_share_delta=round(
            race_wot_share - qualifying_wot_share,
            4,
        ),
        qualifying_brake_zone_share=round(qualifying_brake_share, 4),
        race_brake_zone_share=round(race_brake_share, 4),
        brake_zone_share_delta=round(
            race_brake_share - qualifying_brake_share,
            4,
        ),
        qualifying_coast_candidate_distance_m=round(
            qualifying_coast_distance,
            1,
        ),
        race_coast_candidate_distance_m=round(
            race_coast_distance,
            1,
        ),
        extra_coast_candidate_distance_m=round(
            extra_coast_distance,
            1,
        ),
        disclaimer=(
            "Attack Index is a telemetry-derived driving-effort proxy. "
            "It cannot prove driver intent or reveal fuel load, ERS mode, "
            "tyre temperature, traffic, or team instructions."
        ),
    )


def _clean_samples(
    samples: Sequence[TelemetrySample],
) -> list[TelemetrySample]:
    cleaned: list[TelemetrySample] = []

    for sample in samples:
        values = (
            sample.distance_m,
            sample.speed_kph,
            sample.throttle_percentage,
        )

        if not all(isfinite(value) for value in values):
            continue

        if sample.distance_m < 0:
            continue

        if sample.speed_kph < 0:
            continue

        if not 0 <= sample.throttle_percentage <= 100:
            continue

        cleaned.append(sample)

    cleaned.sort(key=lambda sample: sample.distance_m)

    monotonic: list[TelemetrySample] = []
    previous_distance: float | None = None

    for sample in cleaned:
        if (
            previous_distance is not None
            and sample.distance_m <= previous_distance
        ):
            continue

        monotonic.append(sample)
        previous_distance = sample.distance_m

    return monotonic


def _build_bins(
    samples: Sequence[TelemetrySample],
    bin_count: int,
) -> tuple[_TelemetryBin | None, ...]:
    start_distance = samples[0].distance_m
    lap_distance = _lap_distance(samples)

    if lap_distance <= 0:
        raise InsufficientTelemetryError(
            "Telemetry distance is not usable for comparison."
        )

    grouped_samples: list[list[TelemetrySample]] = [
        [] for _ in range(bin_count)
    ]

    for sample in samples:
        progress = (sample.distance_m - start_distance) / lap_distance
        index = min(bin_count - 1, max(0, int(progress * bin_count)))
        grouped_samples[index].append(sample)

    bins: list[_TelemetryBin | None] = []

    for group in grouped_samples:
        if not group:
            bins.append(None)
            continue

        bins.append(
            _TelemetryBin(
                throttle_percentage=median(
                    sample.throttle_percentage for sample in group
                ),
                speed_kph=median(
                    sample.speed_kph for sample in group
                ),
                brake_share=sum(
                    sample.brake_applied for sample in group
                )
                / len(group),
                sample_count=len(group),
            )
        )

    return tuple(bins)


def _lap_distance(samples: Sequence[TelemetrySample]) -> float:
    return samples[-1].distance_m - samples[0].distance_m


def _full_throttle_share(
    bins: Sequence[_TelemetryBin | None],
) -> float:
    high_speed_bins = [
        telemetry_bin
        for telemetry_bin in bins
        if telemetry_bin is not None
        and telemetry_bin.speed_kph >= HIGH_SPEED_KPH
    ]

    if not high_speed_bins:
        return 0.0

    return sum(
        telemetry_bin.throttle_percentage
        >= FULL_THROTTLE_PERCENTAGE
        for telemetry_bin in high_speed_bins
    ) / len(high_speed_bins)


def _brake_zone_share(
    bins: Sequence[_TelemetryBin | None],
) -> float:
    high_speed_bins = [
        telemetry_bin
        for telemetry_bin in bins
        if telemetry_bin is not None
        and telemetry_bin.speed_kph >= 80.0
    ]

    if not high_speed_bins:
        return 0.0

    return sum(
        telemetry_bin.brake_share >= BRAKE_SHARE_THRESHOLD
        for telemetry_bin in high_speed_bins
    ) / len(high_speed_bins)


def _reference_braking_approach_indexes(
    qualifying_bins: Sequence[_TelemetryBin | None],
) -> list[int]:
    indexes: list[int] = []

    for index, telemetry_bin in enumerate(qualifying_bins):
        if (
            telemetry_bin is None
            or telemetry_bin.speed_kph < HIGH_SPEED_KPH
        ):
            continue

        next_bins = qualifying_bins[index + 1 : index + 6]

        if any(
            next_bin is not None
            and next_bin.brake_share >= BRAKE_SHARE_THRESHOLD
            for next_bin in next_bins
        ):
            indexes.append(index)

    return indexes


def _coast_candidate_count(
    *,
    reference_bins: Sequence[_TelemetryBin | None],
    target_bins: Sequence[_TelemetryBin | None],
    approach_indexes: Sequence[int],
) -> int:
    count = 0

    for index in approach_indexes:
        target_bin = target_bins[index]

        if target_bin is None:
            continue

        if target_bin.speed_kph < HIGH_SPEED_KPH:
            continue

        if target_bin.throttle_percentage > LIFT_THROTTLE_PERCENTAGE:
            continue

        if target_bin.brake_share >= COAST_BRAKE_SHARE_LIMIT:
            continue

        next_target_bin = _next_available_bin(target_bins, index)

        if next_target_bin is None:
            continue

        if next_target_bin.speed_kph < target_bin.speed_kph - 3.0:
            count += 1

    return count


def _next_available_bin(
    bins: Sequence[_TelemetryBin | None],
    current_index: int,
) -> _TelemetryBin | None:
    for telemetry_bin in bins[current_index + 1 : current_index + 4]:
        if telemetry_bin is not None:
            return telemetry_bin

    return None


def _classification(attack_score: float) -> str:
    if attack_score >= 8.5:
        return "QUALIFYING_LIKE"

    if attack_score >= 7.0:
        return "PUSHING"

    if attack_score >= 5.0:
        return "NORMAL_RACE_PACE"

    if attack_score >= 3.0:
        return "MANAGING"

    return "CONSERVING"


def _confidence(
    *,
    qualifying_sample_count: int,
    race_sample_count: int,
    common_bin_count: int,
    braking_approach_count: int,
) -> str:
    if (
        min(qualifying_sample_count, race_sample_count) >= 200
        and common_bin_count >= 85
        and braking_approach_count >= 3
    ):
        return "HIGH"

    if (
        min(qualifying_sample_count, race_sample_count) >= 100
        and common_bin_count >= 70
        and braking_approach_count >= 2
    ):
        return "MEDIUM"

    return "LOW"


def _clamp_01(value: float) -> float:
    return max(0.0, min(1.0, value))
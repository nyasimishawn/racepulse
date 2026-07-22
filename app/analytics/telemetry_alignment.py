from collections import Counter
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class RawTelemetrySample:
    sample_index: int
    relative_time_ms: int
    distance_m: float
    speed_kph: float
    throttle_percentage: float
    brake_applied: bool
    rpm: int | None
    gear: int | None
    drs: int | None


@dataclass(frozen=True)
class CleanTelemetryTrace:
    samples: tuple[RawTelemetrySample, ...]
    raw_sample_count: int
    distance_span_m: float | None
    time_coverage_ratio: float | None


@dataclass(frozen=True)
class NormalizedTelemetryBin:
    normalized_progress: float
    source_sample_count: int
    speed_kph: float | None
    throttle_percentage: float | None
    brake_share: float | None
    rpm: float | None
    gear: int | None
    drs: int | None


def clean_telemetry_trace(
    samples: list[RawTelemetrySample],
    stored_lap_time_ms: int | None,
) -> CleanTelemetryTrace:
    cleaned: list[RawTelemetrySample] = []

    last_distance_m: float | None = None
    last_relative_time_ms: int | None = None

    for sample in sorted(samples, key=lambda item: item.sample_index):
        if not _is_valid_sample(sample):
            continue

        if (
            last_distance_m is not None
            and sample.distance_m <= last_distance_m
        ):
            continue

        if (
            last_relative_time_ms is not None
            and sample.relative_time_ms < last_relative_time_ms
        ):
            continue

        cleaned.append(sample)
        last_distance_m = sample.distance_m
        last_relative_time_ms = sample.relative_time_ms

    distance_span_m: float | None = None
    time_coverage_ratio: float | None = None

    if len(cleaned) >= 2:
        distance_span_m = round(
            cleaned[-1].distance_m - cleaned[0].distance_m,
            3,
        )

        if stored_lap_time_ms and stored_lap_time_ms > 0:
            trace_duration_ms = (
                cleaned[-1].relative_time_ms
                - cleaned[0].relative_time_ms
            )

            time_coverage_ratio = round(
                trace_duration_ms / stored_lap_time_ms,
                4,
            )

    return CleanTelemetryTrace(
        samples=tuple(cleaned),
        raw_sample_count=len(samples),
        distance_span_m=distance_span_m,
        time_coverage_ratio=time_coverage_ratio,
    )


def build_normalized_distance_bins(
    trace: CleanTelemetryTrace,
    bin_count: int,
) -> tuple[NormalizedTelemetryBin | None, ...]:
    empty_bins = tuple(None for _ in range(bin_count))

    if (
        len(trace.samples) < 2
        or trace.distance_span_m is None
        or trace.distance_span_m <= 0
    ):
        return empty_bins

    start_distance_m = trace.samples[0].distance_m
    distance_span_m = trace.distance_span_m

    buckets: list[list[RawTelemetrySample]] = [
        [] for _ in range(bin_count)
    ]

    for sample in trace.samples:
        progress = (
            sample.distance_m - start_distance_m
        ) / distance_span_m

        progress = min(max(progress, 0.0), 1.0)
        bin_index = min(int(progress * bin_count), bin_count - 1)

        buckets[bin_index].append(sample)

    result: list[NormalizedTelemetryBin | None] = []

    for index, bucket in enumerate(buckets):
        if not bucket:
            result.append(None)
            continue

        result.append(
            NormalizedTelemetryBin(
                normalized_progress=round(
                    (index + 0.5) / bin_count,
                    6,
                ),
                source_sample_count=len(bucket),
                speed_kph=_median(
                    [sample.speed_kph for sample in bucket]
                ),
                throttle_percentage=_median(
                    [
                        sample.throttle_percentage
                        for sample in bucket
                    ]
                ),
                brake_share=round(
                    sum(
                        1
                        for sample in bucket
                        if sample.brake_applied
                    )
                    / len(bucket),
                    4,
                ),
                rpm=_median(
                    [
                        float(sample.rpm)
                        for sample in bucket
                        if sample.rpm is not None
                    ]
                ),
                gear=_mode(
                    [
                        sample.gear
                        for sample in bucket
                        if sample.gear is not None
                    ]
                ),
                drs=_mode(
                    [
                        sample.drs
                        for sample in bucket
                        if sample.drs is not None
                    ]
                ),
            )
        )

    return tuple(result)


def _is_valid_sample(sample: RawTelemetrySample) -> bool:
    numeric_values = [
        sample.distance_m,
        sample.speed_kph,
        sample.throttle_percentage,
    ]

    if not all(isfinite(value) for value in numeric_values):
        return False

    if sample.relative_time_ms < 0:
        return False

    if sample.distance_m < 0 or sample.speed_kph < 0:
        return False

    return 0 <= sample.throttle_percentage <= 100


def _median(values: list[float]) -> float | None:
    if not values:
        return None

    ordered = sorted(values)
    middle = len(ordered) // 2

    if len(ordered) % 2 == 1:
        return round(ordered[middle], 3)

    return round(
        (ordered[middle - 1] + ordered[middle]) / 2,
        3,
    )


def _mode(values: list[int]) -> int | None:
    if not values:
        return None

    counts = Counter(values)
    highest_count = max(counts.values())

    for value in values:
        if counts[value] == highest_count:
            return value

    return None
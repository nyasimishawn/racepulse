from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Protocol


BASELINE_BAND_MS = 200
ROBUST_FASTEST_LAP_COUNT = 3
MIN_BASELINE_LAPS = 2


class CompoundPaceLap(Protocol):
    lap_number: int
    lap_time_ms: int | None
    compound: str | None


@dataclass(frozen=True)
class CompoundPaceBaseline:
    driver_number: str
    compound: str
    sample_count: int
    fastest_lap_time_ms: int
    robust_fastest_lap_time_ms: float
    baseline_lap_numbers: tuple[int, ...]
    data_quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class CompoundRelativeLapScore:
    lap_number: int
    compound: str | None
    lap_time_ms: int | None
    baseline_ms: float | None
    delta_to_compound_baseline_ms: float | None
    band_ms: int
    classification: str
    data_quality_flags: tuple[str, ...]


def normalize_compound(compound: str | None) -> str | None:
    if compound is None:
        return None

    cleaned = compound.strip().upper()
    return cleaned or None


def build_driver_compound_baselines(
    laps_by_driver: dict[str, Iterable[CompoundPaceLap]],
    *,
    min_laps: int = MIN_BASELINE_LAPS,
    robust_lap_count: int = ROBUST_FASTEST_LAP_COUNT,
) -> dict[tuple[str, str], CompoundPaceBaseline]:
    baselines: dict[tuple[str, str], CompoundPaceBaseline] = {}

    for driver_number, laps in laps_by_driver.items():
        laps_by_compound: dict[str, list[CompoundPaceLap]] = {}

        for lap in laps:
            compound = normalize_compound(lap.compound)
            if compound is None or lap.lap_time_ms is None:
                continue
            laps_by_compound.setdefault(compound, []).append(lap)

        for compound, compound_laps in laps_by_compound.items():
            ordered = sorted(
                compound_laps,
                key=lambda lap: (
                    lap.lap_time_ms
                    if lap.lap_time_ms is not None
                    else 999_999_999,
                    lap.lap_number,
                ),
            )

            if len(ordered) < min_laps:
                continue

            baseline_laps = ordered[:robust_lap_count]
            baseline_times = [
                lap.lap_time_ms
                for lap in baseline_laps
                if lap.lap_time_ms is not None
            ]

            flags: list[str] = []
            if len(ordered) < robust_lap_count:
                flags.append("LIMITED_BASELINE_SAMPLE")

            baselines[(driver_number, compound)] = CompoundPaceBaseline(
                driver_number=driver_number,
                compound=compound,
                sample_count=len(ordered),
                fastest_lap_time_ms=min(baseline_times),
                robust_fastest_lap_time_ms=float(median(baseline_times)),
                baseline_lap_numbers=tuple(
                    lap.lap_number
                    for lap in baseline_laps
                ),
                data_quality_flags=tuple(flags),
            )

    return baselines


def score_lap_against_compound_baseline(
    lap: CompoundPaceLap,
    baseline: CompoundPaceBaseline | None,
    *,
    band_ms: int = BASELINE_BAND_MS,
) -> CompoundRelativeLapScore:
    flags: list[str] = []
    compound = normalize_compound(lap.compound)

    if lap.lap_time_ms is None:
        flags.append("MISSING_LAP_TIME")

    if compound is None:
        flags.append("UNKNOWN_COMPOUND")

    if baseline is None:
        flags.append("COMPOUND_BASELINE_UNAVAILABLE")

    if lap.lap_time_ms is None or baseline is None:
        return CompoundRelativeLapScore(
            lap_number=lap.lap_number,
            compound=compound,
            lap_time_ms=lap.lap_time_ms,
            baseline_ms=None,
            delta_to_compound_baseline_ms=None,
            band_ms=band_ms,
            classification="NOT_SCORED",
            data_quality_flags=tuple(flags),
        )

    delta = round(
        lap.lap_time_ms - baseline.robust_fastest_lap_time_ms,
        2,
    )

    if delta < -band_ms:
        classification = "FASTER_THAN_BASELINE"
    elif delta > band_ms:
        classification = "SLOWER_THAN_BASELINE"
    else:
        classification = "ON_BASELINE"

    return CompoundRelativeLapScore(
        lap_number=lap.lap_number,
        compound=compound,
        lap_time_ms=lap.lap_time_ms,
        baseline_ms=baseline.robust_fastest_lap_time_ms,
        delta_to_compound_baseline_ms=delta,
        band_ms=band_ms,
        classification=classification,
        data_quality_flags=tuple(flags),
    )

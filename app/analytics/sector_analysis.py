from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SectorTimes:
    sector_1_time_ms: int | None
    sector_2_time_ms: int | None
    sector_3_time_ms: int | None


def calculate_best_sector_times(
    sector_rows: Iterable[
        tuple[int | None, int | None, int | None]
    ],
) -> SectorTimes:
    rows = list(sector_rows)

    return SectorTimes(
        sector_1_time_ms=_minimum(row[0] for row in rows),
        sector_2_time_ms=_minimum(row[1] for row in rows),
        sector_3_time_ms=_minimum(row[2] for row in rows),
    )


def calculate_sector_deltas(
    driver_best: SectorTimes,
    session_best: SectorTimes,
) -> SectorTimes:
    return SectorTimes(
        sector_1_time_ms=_delta(
            driver_best.sector_1_time_ms,
            session_best.sector_1_time_ms,
        ),
        sector_2_time_ms=_delta(
            driver_best.sector_2_time_ms,
            session_best.sector_2_time_ms,
        ),
        sector_3_time_ms=_delta(
            driver_best.sector_3_time_ms,
            session_best.sector_3_time_ms,
        ),
    )


def _minimum(values: Iterable[int | None]) -> int | None:
    populated = [
        value
        for value in values
        if value is not None
    ]

    return min(populated) if populated else None


def _delta(
    value: int | None,
    benchmark: int | None,
) -> int | None:
    if value is None or benchmark is None:
        return None

    return value - benchmark
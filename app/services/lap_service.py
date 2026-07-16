from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.lap import LapResponse


class RaceSessionNotFoundError(LookupError):
    pass


class LapImportError(RuntimeError):
    pass


class LapImportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.fastf1_provider = FastF1Provider()

    def import_laps(self, race_session_id: UUID) -> tuple[int, int]:
        context = self.db.execute(
            select(RaceSession, Meeting)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(RaceSession.id == race_session_id)
        ).one_or_none()

        if context is None:
            raise RaceSessionNotFoundError("Race session not found.")

        race_session, meeting = context

        try:
            fastf1_session = self.fastf1_provider.load_laps_session(
                year=meeting.year,
                event_name=meeting.name,
                session_identifier=race_session.session_identifier,
            )
        except Exception as error:
            raise LapImportError(
                "FastF1 could not load lap data for this session."
            ) from error

        drivers = self.db.scalars(
            select(Driver)
            .join(SessionResult, SessionResult.driver_id == Driver.id)
            .where(SessionResult.race_session_id == race_session_id)
        ).all()

        drivers_by_number = {
            driver.driver_number: driver
            for driver in drivers
        }

        existing_laps = {
            (lap.driver_id, lap.lap_number): lap
            for lap in self.db.scalars(
                select(Lap).where(Lap.race_session_id == race_session_id)
            ).all()
        }

        laps_upserted = 0
        laps_skipped = 0

        try:
            for _, lap_row in fastf1_session.laps.iterrows():
                driver_number = self._text(lap_row.get("DriverNumber"))
                lap_number = self._integer(lap_row.get("LapNumber"))

                driver = drivers_by_number.get(driver_number)

                if driver is None or lap_number is None:
                    laps_skipped += 1
                    continue

                lap = existing_laps.get((driver.id, lap_number))

                if lap is None:
                    lap = Lap(
                        race_session_id=race_session_id,
                        driver_id=driver.id,
                        lap_number=lap_number,
                    )
                    self.db.add(lap)
                    existing_laps[(driver.id, lap_number)] = lap

                self._apply_lap_values(lap, lap_row)
                laps_upserted += 1

            self.db.commit()

            return laps_upserted, laps_skipped

        except Exception as error:
            self.db.rollback()

            raise LapImportError(
                "Lap data could not be saved to the database."
            ) from error

    def _apply_lap_values(self, lap: Lap, row: pd.Series) -> None:
        lap.lap_start_at = self._datetime(row.get("LapStartDate"))

        lap.lap_start_time_ms = self._timedelta_ms(
            row.get("LapStartTime")
        )
        lap.lap_time_ms = self._timedelta_ms(row.get("LapTime"))

        lap.sector_1_time_ms = self._timedelta_ms(
            row.get("Sector1Time")
        )
        lap.sector_2_time_ms = self._timedelta_ms(
            row.get("Sector2Time")
        )
        lap.sector_3_time_ms = self._timedelta_ms(
            row.get("Sector3Time")
        )

        lap.speed_i1 = self._decimal(row.get("SpeedI1"))
        lap.speed_i2 = self._decimal(row.get("SpeedI2"))
        lap.speed_fl = self._decimal(row.get("SpeedFL"))
        lap.speed_st = self._decimal(row.get("SpeedST"))

        lap.stint = self._integer(row.get("Stint"))
        lap.compound = self._text(row.get("Compound"))
        lap.tyre_life = self._decimal(row.get("TyreLife"))
        lap.fresh_tyre = self._boolean(row.get("FreshTyre"))

        lap.pit_in_time_ms = self._timedelta_ms(row.get("PitInTime"))
        lap.pit_out_time_ms = self._timedelta_ms(row.get("PitOutTime"))

        lap.track_status = self._text(row.get("TrackStatus"))
        lap.position = self._integer(row.get("Position"))

        lap.is_personal_best = self._boolean(
            row.get("IsPersonalBest")
        )
        lap.is_accurate = self._boolean(row.get("IsAccurate"))

        lap.deleted = self._boolean(row.get("Deleted"))
        lap.deleted_reason = self._text(row.get("DeletedReason"))

        lap.fastf1_generated = self._boolean(
            row.get("FastF1Generated")
        )

    @staticmethod
    def _text(value: object) -> str | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        text = str(value).strip()

        if not text or text.lower() in {"nan", "nat", "none"}:
            return None

        return text

    @staticmethod
    def _integer(value: object) -> int | None:
        text = LapImportService._text(value)

        if text is None:
            return None

        try:
            return int(float(text))
        except ValueError:
            return None

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        text = LapImportService._text(value)

        if text is None:
            return None

        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    @staticmethod
    def _boolean(value: object) -> bool | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        if isinstance(value, bool):
            return value

        text = str(value).strip().lower()

        if text in {"true", "1", "yes"}:
            return True

        if text in {"false", "0", "no"}:
            return False

        return None

    @staticmethod
    def _timedelta_ms(value: object) -> int | None:
        if value is None:
            return None

        try:
            delta = pd.Timedelta(value)

            if pd.isna(delta):
                return None

            return int(round(delta.total_seconds() * 1000))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if value is None:
            return None

        try:
            if bool(pd.isna(value)):
                return None
        except (TypeError, ValueError):
            pass

        if isinstance(value, pd.Timestamp):
            value = value.to_pydatetime()

        if not isinstance(value, datetime):
            return None

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value


class LapQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def session_exists(self, race_session_id: UUID) -> bool:
        return self.db.get(RaceSession, race_session_id) is not None

    def list_laps(
        self,
        race_session_id: UUID,
        driver_number: str | None,
        limit: int,
        offset: int,
    ) -> list[LapResponse]:
        statement = (
            select(Lap, Driver)
            .join(Driver, Lap.driver_id == Driver.id)
            .where(Lap.race_session_id == race_session_id)
            .order_by(Driver.driver_number, Lap.lap_number)
            .limit(limit)
            .offset(offset)
        )

        if driver_number is not None:
            statement = statement.where(
                Driver.driver_number == driver_number.strip()
            )

        rows = self.db.execute(statement).all()

        return [
            LapResponse(
                driver_number=driver.driver_number,
                abbreviation=driver.abbreviation,
                lap_number=lap.lap_number,
                lap_start_at=lap.lap_start_at,
                lap_time_ms=lap.lap_time_ms,
                sector_1_time_ms=lap.sector_1_time_ms,
                sector_2_time_ms=lap.sector_2_time_ms,
                sector_3_time_ms=lap.sector_3_time_ms,
                speed_i1=lap.speed_i1,
                speed_i2=lap.speed_i2,
                speed_fl=lap.speed_fl,
                speed_st=lap.speed_st,
                stint=lap.stint,
                compound=lap.compound,
                tyre_life=lap.tyre_life,
                fresh_tyre=lap.fresh_tyre,
                position=lap.position,
                track_status=lap.track_status,
                is_personal_best=lap.is_personal_best,
                is_accurate=lap.is_accurate,
                deleted=lap.deleted,
                deleted_reason=lap.deleted_reason,
            )
            for lap, driver in rows
        ]
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
from app.models.telemetry_point import TelemetryPoint
from app.providers.fastf1_provider import FastF1Provider
from app.schemas.telemetry import TelemetryPointResponse


class StoredLapNotFoundError(LookupError):
    pass


class TelemetryUnavailableError(RuntimeError):
    pass


class TelemetryImportError(RuntimeError):
    pass


class TelemetryImportService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.fastf1_provider = FastF1Provider()

    def import_lap_telemetry(
            self,
            *,
            race_session_id: UUID,
            driver_number: str,
            lap_number: int,
    ) -> int:
        context = self.db.execute(
            select(Lap, Driver, RaceSession, Meeting)
            .join(Driver, Lap.driver_id == Driver.id)
            .join(
                RaceSession,
                Lap.race_session_id == RaceSession.id,
            )
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(
                Lap.race_session_id == race_session_id,
                Driver.driver_number == driver_number,
                Lap.lap_number == lap_number,
            )
        ).one_or_none()

        if context is None:
            raise StoredLapNotFoundError(
                "The requested driver lap has not been imported."
            )

        lap, driver, race_session, meeting = context

        try:
            fastf1_session = self.fastf1_provider.load_telemetry_session(
                year=meeting.year,
                event_name=meeting.name,
                session_identifier=race_session.session_identifier,
            )
        except Exception as error:
            raise TelemetryImportError(
                "FastF1 could not retrieve telemetry for this lap."
            ) from error

        return self.import_loaded_lap_telemetry(
            lap=lap,
            driver=driver,
            race_session=race_session,
            fastf1_session=fastf1_session,
        )

    def import_loaded_lap_telemetry(
            self,
            *,
            lap: Lap,
            driver: Driver,
            race_session: RaceSession,
            fastf1_session,
    ) -> int:
        telemetry = self._get_lap_telemetry(
            lap=lap,
            driver=driver,
            fastf1_session=fastf1_session,
        )

        existing_points = {
            point.sample_index: point
            for point in self.db.scalars(
                select(TelemetryPoint).where(
                    TelemetryPoint.lap_id == lap.id
                )
            ).all()
        }

        points_upserted = 0
        seen_indexes: set[int] = set()

        try:
            for sample_index, (_, row) in enumerate(
                    telemetry.iterrows()
            ):
                relative_time_ms = self._timedelta_ms(row.get("Time"))

                if relative_time_ms is None:
                    continue

                point = existing_points.get(sample_index)

                if point is None:
                    point = TelemetryPoint(
                        race_session_id=race_session.id,
                        driver_id=driver.id,
                        lap_id=lap.id,
                        sample_index=sample_index,
                        relative_time_ms=relative_time_ms,
                    )
                    self.db.add(point)

                self._apply_values(
                    point=point,
                    row=row,
                    relative_time_ms=relative_time_ms,
                )

                seen_indexes.add(sample_index)
                points_upserted += 1

            for sample_index, point in existing_points.items():
                if sample_index not in seen_indexes:
                    self.db.delete(point)

            self.db.commit()
            return points_upserted

        except Exception as error:
            self.db.rollback()

            raise TelemetryImportError(
                "Telemetry points could not be saved."
            ) from error

    def _get_lap_telemetry(
            self,
            *,
            lap: Lap,
            driver: Driver,
            fastf1_session,
    ):
        try:
            selected_laps = (
                fastf1_session.laps
                .pick_drivers(driver.driver_number)
                .pick_laps(lap.lap_number)
            )

            if selected_laps.empty:
                raise TelemetryUnavailableError(
                    "FastF1 has no telemetry for this driver lap."
                )

            telemetry = selected_laps.get_telemetry(
                frequency="original"
            )

            if telemetry.empty:
                raise TelemetryUnavailableError(
                    "FastF1 returned an empty telemetry trace."
                )

            return telemetry

        except TelemetryUnavailableError:
            raise

        except Exception as error:
            raise TelemetryImportError(
                "FastF1 could not retrieve telemetry for this lap."
            ) from error

    def _apply_values(
        self,
        *,
        point: TelemetryPoint,
        row: pd.Series,
        relative_time_ms: int,
    ) -> None:
        sample_source = self._text(row.get("Source"))

        point.relative_time_ms = relative_time_ms
        point.session_time_ms = self._timedelta_ms(
            row.get("SessionTime")
        )
        point.sampled_at = self._datetime(row.get("Date"))

        point.speed_kph = self._decimal(row.get("Speed"))
        point.rpm = self._integer(row.get("RPM"))
        point.gear = self._integer(row.get("nGear"))
        point.throttle_percentage = self._decimal(
            row.get("Throttle")
        )
        point.brake_applied = self._boolean(row.get("Brake"))
        point.drs = self._integer(row.get("DRS"))

        point.x = self._decimal(row.get("X"))
        point.y = self._decimal(row.get("Y"))
        point.z = self._decimal(row.get("Z"))
        point.distance_m = self._decimal(row.get("Distance"))

        point.sample_source = sample_source
        point.is_interpolated = (
                sample_source is not None
                and sample_source.casefold()
                in {"interpolation", "interpolated"}
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
        text = TelemetryImportService._text(value)

        if text is None:
            return None

        try:
            return int(float(text))
        except ValueError:
            return None

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        text = TelemetryImportService._text(value)

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


class TelemetryQueryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_points(
        self,
        *,
        race_session_id: UUID,
        driver_number: str,
        lap_number: int,
        limit: int,
    ) -> list[TelemetryPointResponse]:
        rows = self.db.execute(
            select(TelemetryPoint)
            .join(Lap, TelemetryPoint.lap_id == Lap.id)
            .join(Driver, Lap.driver_id == Driver.id)
            .where(
                TelemetryPoint.race_session_id == race_session_id,
                Driver.driver_number == driver_number,
                Lap.lap_number == lap_number,
            )
            .order_by(TelemetryPoint.relative_time_ms)
            .limit(limit)
        ).scalars().all()

        return [
            TelemetryPointResponse(
                relative_time_ms=point.relative_time_ms,
                session_time_ms=point.session_time_ms,
                sampled_at=point.sampled_at,
                speed_kph=point.speed_kph,
                rpm=point.rpm,
                gear=point.gear,
                throttle_percentage=point.throttle_percentage,
                brake_applied=point.brake_applied,
                drs=point.drs,
                x=point.x,
                y=point.y,
                z=point.z,
                distance_m=point.distance_m,
                sample_source=point.sample_source,
                is_interpolated=point.is_interpolated,
            )
            for point in rows
        ]
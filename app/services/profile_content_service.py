from __future__ import annotations

from decimal import Decimal
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.models.curated_content import (
    DriverProfile,
    EditorialUpdate,
    ProfileNotableMoment,
    TeamProfile,
)
from app.models.driver import Driver
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.editorial import (
    EditorialPublicationStatus,
    EditorialUpdateCreateRequest,
    EditorialUpdateResponse,
    EditorialUpdateType,
    EditorialUpdateUpdateRequest,
)
from app.schemas.profiles import (
    ContentConfidence,
    CuratedAttributionResponse,
    DriverHistoryResponse,
    DriverProfileResponse,
    DriverProfileUpsertRequest,
    DriverSummaryResponse,
    NotableMomentResponse,
    ProfileNotableMomentCreateRequest,
    ProfileNotableMomentUpdateRequest,
    SeasonTimingHistoryStatsResponse,
    TeamHistoryResponse,
    TeamProfileResponse,
    TeamProfileUpsertRequest,
    TeamSummaryResponse,
    TimingCoverageResponse,
    TimingHistoryStatsResponse,
)


TIMING_COVERAGE_DISCLAIMER = (
    "Statistics use only RacePulse's imported public timing for the "
    "listed seasons and sources. Missing meetings, sessions, result rows, "
    "or laps are not inferred; recorded laps led are not complete career "
    "totals unless the relevant lap coverage has been imported."
)


class ProfileContentError(Exception):
    pass


class DriverNotFoundError(ProfileContentError):
    pass


class TeamNotFoundError(ProfileContentError):
    pass


class NotableMomentNotFoundError(ProfileContentError):
    pass


class EditorialUpdateNotFoundError(ProfileContentError):
    pass


class EditorialAssociationError(ProfileContentError):
    pass


class ProfileContentValidationError(ProfileContentError):
    pass


class ProfileContentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_drivers(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DriverSummaryResponse]:
        statement = select(Driver, DriverProfile).outerjoin(
            DriverProfile, DriverProfile.driver_id == Driver.id
        )
        normalized_query = (query or "").strip()

        if normalized_query:
            pattern = f"%{normalized_query}%"
            statement = statement.where(
                or_(
                    Driver.full_name.ilike(pattern),
                    Driver.first_name.ilike(pattern),
                    Driver.last_name.ilike(pattern),
                    Driver.abbreviation.ilike(pattern),
                    Driver.driver_number.ilike(pattern),
                )
            )

        drivers = self.db.execute(
            statement.order_by(
                Driver.full_name.is_(None),
                Driver.full_name,
                Driver.driver_number,
                Driver.id,
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            self._driver_summary(driver, profile)
            for driver, profile in drivers
        ]

    def list_teams(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TeamSummaryResponse]:
        statement = select(Team, TeamProfile).outerjoin(
            TeamProfile, TeamProfile.team_id == Team.id
        )
        normalized_query = (query or "").strip()

        if normalized_query:
            statement = statement.where(
                Team.name.ilike(f"%{normalized_query}%")
            )

        teams = self.db.execute(
            statement.order_by(Team.name, Team.id).offset(offset).limit(limit)
        ).all()
        return [self._team_summary(team, profile) for team, profile in teams]

    def get_driver_profile(self, driver_id: UUID) -> DriverProfileResponse:
        driver = self._require_driver(driver_id)
        profile = self.db.scalar(
            select(DriverProfile).where(DriverProfile.driver_id == driver_id)
        )
        moments = self.db.scalars(
            select(ProfileNotableMoment)
            .where(ProfileNotableMoment.driver_id == driver_id)
            .order_by(
                ProfileNotableMoment.occurred_at.desc(),
                ProfileNotableMoment.created_at.desc(),
            )
        ).all()

        return DriverProfileResponse(
            **self._driver_summary(driver, profile).model_dump(),
            details=profile.details if profile else None,
            recorded_teams=[
                self._team_summary(team)
                for team in self.db.scalars(
                    select(Team)
                    .join(SessionResult, SessionResult.team_id == Team.id)
                    .where(SessionResult.driver_id == driver_id)
                    .distinct()
                    .order_by(Team.name, Team.id)
                ).all()
            ],
            profile_id=profile.id if profile else None,
            biography=profile.biography if profile else None,
            attribution=(
                self._attribution(profile) if profile is not None else None
            ),
            notable_moments=[
                self._moment_response(moment) for moment in moments
            ],
        )

    def get_team_profile(self, team_id: UUID) -> TeamProfileResponse:
        team = self._require_team(team_id)
        profile = self.db.scalar(
            select(TeamProfile).where(TeamProfile.team_id == team_id)
        )
        moments = self.db.scalars(
            select(ProfileNotableMoment)
            .where(ProfileNotableMoment.team_id == team_id)
            .order_by(
                ProfileNotableMoment.occurred_at.desc(),
                ProfileNotableMoment.created_at.desc(),
            )
        ).all()

        return TeamProfileResponse(
            **self._team_summary(team, profile).model_dump(),
            details=profile.details if profile else None,
            recorded_drivers=[
                self._driver_summary(driver)
                for driver in self.db.scalars(
                    select(Driver)
                    .join(SessionResult, SessionResult.driver_id == Driver.id)
                    .where(SessionResult.team_id == team_id)
                    .distinct()
                    .order_by(Driver.full_name, Driver.id)
                ).all()
            ],
            profile_id=profile.id if profile else None,
            biography=profile.biography if profile else None,
            attribution=(
                self._attribution(profile) if profile is not None else None
            ),
            notable_moments=[
                self._moment_response(moment) for moment in moments
            ],
        )

    def upsert_driver_profile(
        self,
        *,
        driver_id: UUID,
        payload: DriverProfileUpsertRequest,
        editor_profile_id: UUID,
    ) -> DriverProfileResponse:
        self._require_driver(driver_id)
        profile = self.db.scalar(
            select(DriverProfile).where(DriverProfile.driver_id == driver_id)
        )

        if profile is None:
            profile = DriverProfile(
                driver_id=driver_id,
                created_by_profile_id=editor_profile_id,
            )
            self.db.add(profile)

        self._apply_profile_payload(profile, payload)
        profile.updated_by_profile_id = editor_profile_id
        self._commit()
        return self.get_driver_profile(driver_id)

    def upsert_team_profile(
        self,
        *,
        team_id: UUID,
        payload: TeamProfileUpsertRequest,
        editor_profile_id: UUID,
    ) -> TeamProfileResponse:
        self._require_team(team_id)
        profile = self.db.scalar(
            select(TeamProfile).where(TeamProfile.team_id == team_id)
        )

        if profile is None:
            profile = TeamProfile(
                team_id=team_id,
                created_by_profile_id=editor_profile_id,
            )
            self.db.add(profile)

        self._apply_profile_payload(profile, payload)
        profile.updated_by_profile_id = editor_profile_id
        self._commit()
        return self.get_team_profile(team_id)

    def create_driver_notable_moment(
        self,
        *,
        driver_id: UUID,
        payload: ProfileNotableMomentCreateRequest,
        editor_profile_id: UUID,
    ) -> NotableMomentResponse:
        self._require_driver(driver_id)
        moment = ProfileNotableMoment(
            driver_id=driver_id,
            title=payload.title,
            description=payload.description,
            occurred_at=payload.occurred_at,
            source_url=payload.source_url,
            publisher=payload.publisher,
            published_at=payload.published_at,
            confidence=payload.confidence.value,
            data_quality_flags=payload.data_quality_flags,
            created_by_profile_id=editor_profile_id,
            updated_by_profile_id=editor_profile_id,
        )
        self.db.add(moment)
        self._commit()
        self.db.refresh(moment)
        return self._moment_response(moment)

    def create_team_notable_moment(
        self,
        *,
        team_id: UUID,
        payload: ProfileNotableMomentCreateRequest,
        editor_profile_id: UUID,
    ) -> NotableMomentResponse:
        self._require_team(team_id)
        moment = ProfileNotableMoment(
            team_id=team_id,
            title=payload.title,
            description=payload.description,
            occurred_at=payload.occurred_at,
            source_url=payload.source_url,
            publisher=payload.publisher,
            published_at=payload.published_at,
            confidence=payload.confidence.value,
            data_quality_flags=payload.data_quality_flags,
            created_by_profile_id=editor_profile_id,
            updated_by_profile_id=editor_profile_id,
        )
        self.db.add(moment)
        self._commit()
        self.db.refresh(moment)
        return self._moment_response(moment)

    def update_driver_notable_moment(
        self,
        *,
        driver_id: UUID,
        moment_id: UUID,
        payload: ProfileNotableMomentUpdateRequest,
        editor_profile_id: UUID,
    ) -> NotableMomentResponse:
        self._require_driver(driver_id)
        moment = self._require_moment(
            moment_id,
            driver_id=driver_id,
            team_id=None,
        )
        self._apply_moment_update(moment, payload)
        moment.updated_by_profile_id = editor_profile_id
        self._commit()
        self.db.refresh(moment)
        return self._moment_response(moment)

    def update_team_notable_moment(
        self,
        *,
        team_id: UUID,
        moment_id: UUID,
        payload: ProfileNotableMomentUpdateRequest,
        editor_profile_id: UUID,
    ) -> NotableMomentResponse:
        self._require_team(team_id)
        moment = self._require_moment(
            moment_id,
            driver_id=None,
            team_id=team_id,
        )
        self._apply_moment_update(moment, payload)
        moment.updated_by_profile_id = editor_profile_id
        self._commit()
        self.db.refresh(moment)
        return self._moment_response(moment)

    def delete_driver_notable_moment(
        self,
        *,
        driver_id: UUID,
        moment_id: UUID,
    ) -> None:
        self._require_driver(driver_id)
        moment = self._require_moment(
            moment_id,
            driver_id=driver_id,
            team_id=None,
        )
        self.db.delete(moment)
        self._commit()

    def delete_team_notable_moment(
        self,
        *,
        team_id: UUID,
        moment_id: UUID,
    ) -> None:
        self._require_team(team_id)
        moment = self._require_moment(
            moment_id,
            driver_id=None,
            team_id=team_id,
        )
        self.db.delete(moment)
        self._commit()

    def get_driver_history(
        self,
        *,
        driver_id: UUID,
        year: int | None,
    ) -> DriverHistoryResponse:
        driver = self._require_driver(driver_id)
        result_rows = self._driver_result_rows(driver_id, year)
        lap_rows = self._driver_lap_rows(driver_id, year)
        totals, seasons = self._history_stats(result_rows, lap_rows)

        return DriverHistoryResponse(
            driver=self._driver_summary(driver),
            filter_year=year,
            totals=totals,
            seasons=seasons,
            coverage=self._coverage(result_rows, lap_rows),
        )

    def get_team_history(
        self,
        *,
        team_id: UUID,
        year: int | None,
    ) -> TeamHistoryResponse:
        team = self._require_team(team_id)
        result_rows = self._team_result_rows(team_id, year)
        lap_rows = self._team_lap_rows(team_id, year)
        totals, seasons = self._history_stats(result_rows, lap_rows)

        return TeamHistoryResponse(
            team=self._team_summary(team),
            filter_year=year,
            totals=totals,
            seasons=seasons,
            coverage=self._coverage(result_rows, lap_rows),
        )

    def _driver_result_rows(
        self,
        driver_id: UUID,
        year: int | None,
    ) -> list[tuple[SessionResult, RaceSession, Meeting]]:
        statement = (
            select(SessionResult, RaceSession, Meeting)
            .join(
                RaceSession,
                SessionResult.race_session_id == RaceSession.id,
            )
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(
                SessionResult.driver_id == driver_id,
                self._race_session_predicate(),
            )
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        return self.db.execute(
            statement.order_by(
                Meeting.year, Meeting.event_date, RaceSession.id
            )
        ).all()

    def _team_result_rows(
        self,
        team_id: UUID,
        year: int | None,
    ) -> list[tuple[SessionResult, RaceSession, Meeting]]:
        statement = (
            select(SessionResult, RaceSession, Meeting)
            .join(
                RaceSession,
                SessionResult.race_session_id == RaceSession.id,
            )
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(
                SessionResult.team_id == team_id,
                self._race_session_predicate(),
            )
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        return self.db.execute(
            statement.order_by(
                Meeting.year, Meeting.event_date, RaceSession.id
            )
        ).all()

    def _driver_lap_rows(
        self,
        driver_id: UUID,
        year: int | None,
    ) -> list[tuple[int, int, int]]:
        statement = (
            select(
                Meeting.year.label("year"),
                func.count(Lap.id).label("lap_count"),
                func.coalesce(
                    func.sum(case((Lap.position == 1, 1), else_=0)),
                    0,
                ).label("laps_led"),
            )
            .select_from(Lap)
            .join(RaceSession, Lap.race_session_id == RaceSession.id)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .where(
                Lap.driver_id == driver_id,
                Lap.deleted.is_not(True),
                self._race_session_predicate(),
            )
            .group_by(Meeting.year)
            .order_by(Meeting.year)
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        return [
            (int(row.year), int(row.lap_count), int(row.laps_led))
            for row in self.db.execute(statement).all()
        ]

    def _team_lap_rows(
        self,
        team_id: UUID,
        year: int | None,
    ) -> list[tuple[int, int, int]]:
        statement = (
            select(
                Meeting.year.label("year"),
                func.count(Lap.id).label("lap_count"),
                func.coalesce(
                    func.sum(case((Lap.position == 1, 1), else_=0)),
                    0,
                ).label("laps_led"),
            )
            .select_from(Lap)
            .join(RaceSession, Lap.race_session_id == RaceSession.id)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .join(
                SessionResult,
                and_(
                    SessionResult.race_session_id == Lap.race_session_id,
                    SessionResult.driver_id == Lap.driver_id,
                ),
            )
            .where(
                SessionResult.team_id == team_id,
                Lap.deleted.is_not(True),
                self._race_session_predicate(),
            )
            .group_by(Meeting.year)
            .order_by(Meeting.year)
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        return [
            (int(row.year), int(row.lap_count), int(row.laps_led))
            for row in self.db.execute(statement).all()
        ]

    def _history_stats(
        self,
        result_rows: list[tuple[SessionResult, RaceSession, Meeting]],
        lap_rows: list[tuple[int, int, int]],
    ) -> tuple[
        TimingHistoryStatsResponse,
        list[SeasonTimingHistoryStatsResponse],
    ]:
        total = self._empty_stats()
        by_year: dict[int, dict[str, int | Decimal | None]] = {}

        for result, _race_session, meeting in result_rows:
            season = by_year.setdefault(meeting.year, self._empty_stats())
            self._add_result(total, result)
            self._add_result(season, result)

        for year, _lap_count, laps_led in lap_rows:
            season = by_year.setdefault(year, self._empty_stats())
            total["recorded_laps_led"] = (
                int(total["recorded_laps_led"]) + laps_led
            )
            season["recorded_laps_led"] = (
                int(season["recorded_laps_led"]) + laps_led
            )

        totals = self._stats_response(total)
        seasons = [
            SeasonTimingHistoryStatsResponse(
                year=season_year,
                **self._stats_response(stats).model_dump(),
            )
            for season_year, stats in sorted(by_year.items(), reverse=True)
        ]
        return totals, seasons

    def _coverage(
        self,
        result_rows: list[tuple[SessionResult, RaceSession, Meeting]],
        lap_rows: list[tuple[int, int, int]],
    ) -> TimingCoverageResponse:
        years = sorted({meeting.year for _, _, meeting in result_rows})
        source_names = sorted(
            {meeting.source for _, _, meeting in result_rows}
        )
        session_times = [
            meeting.event_date or race_session.started_at
            for _, race_session, meeting in result_rows
            if meeting.event_date is not None
            or race_session.started_at is not None
        ]

        return TimingCoverageResponse(
            source_names=source_names,
            available_years=years,
            earliest_imported_session_at=(
                min(session_times) if session_times else None
            ),
            latest_imported_session_at=(
                max(session_times) if session_times else None
            ),
            race_result_count=len(result_rows),
            recorded_lap_count=sum(lap_count for _, lap_count, _ in lap_rows),
            complete_historical_coverage=False,
            disclaimer=TIMING_COVERAGE_DISCLAIMER,
        )

    @staticmethod
    def _empty_stats() -> dict[str, int | Decimal | None]:
        return {
            "race_entries": 0,
            "wins": 0,
            "podiums": 0,
            "points": Decimal("0"),
            "best_finish": None,
            "recorded_laps_led": 0,
        }

    @staticmethod
    def _add_result(
        stats: dict[str, int | Decimal | None],
        result: SessionResult,
    ) -> None:
        stats["race_entries"] = int(stats["race_entries"]) + 1
        stats["points"] = Decimal(stats["points"]) + (
            result.points if result.points is not None else Decimal("0")
        )

        if result.position is None or result.position < 1:
            return

        if result.position == 1:
            stats["wins"] = int(stats["wins"]) + 1

        if result.position <= 3:
            stats["podiums"] = int(stats["podiums"]) + 1

        current_best = stats["best_finish"]
        if current_best is None or result.position < int(current_best):
            stats["best_finish"] = result.position

    @staticmethod
    def _stats_response(
        stats: dict[str, int | Decimal | None],
    ) -> TimingHistoryStatsResponse:
        return TimingHistoryStatsResponse(
            race_entries=int(stats["race_entries"]),
            wins=int(stats["wins"]),
            podiums=int(stats["podiums"]),
            points=Decimal(stats["points"]),
            best_finish=(
                int(stats["best_finish"])
                if stats["best_finish"] is not None
                else None
            ),
            recorded_laps_led=int(stats["recorded_laps_led"]),
        )

    @staticmethod
    def _race_session_predicate():
        return or_(
            RaceSession.session_identifier == "R",
            func.lower(RaceSession.session_type) == "race",
        )

    def _require_driver(self, driver_id: UUID) -> Driver:
        driver = self.db.get(Driver, driver_id)
        if driver is None:
            raise DriverNotFoundError("Driver not found.")
        return driver

    def _require_team(self, team_id: UUID) -> Team:
        team = self.db.get(Team, team_id)
        if team is None:
            raise TeamNotFoundError("Team not found.")
        return team

    def _require_moment(
        self,
        moment_id: UUID,
        *,
        driver_id: UUID | None,
        team_id: UUID | None,
    ) -> ProfileNotableMoment:
        moment = self.db.get(ProfileNotableMoment, moment_id)
        if moment is None:
            raise NotableMomentNotFoundError("Notable moment not found.")

        if moment.driver_id != driver_id or moment.team_id != team_id:
            raise NotableMomentNotFoundError("Notable moment not found.")

        return moment

    @staticmethod
    def _driver_summary(
        driver: Driver,
        profile: DriverProfile | None = None,
    ) -> DriverSummaryResponse:
        return DriverSummaryResponse(
            id=driver.id,
            driver_number=driver.driver_number,
            abbreviation=driver.abbreviation,
            first_name=driver.first_name,
            last_name=driver.last_name,
            full_name=driver.full_name,
            country_code=driver.country_code,
            source=driver.source,
            short_bio=profile.short_bio if profile else None,
            avatar=profile.avatar if profile else None,
        )

    @staticmethod
    def _team_summary(
        team: Team,
        profile: TeamProfile | None = None,
    ) -> TeamSummaryResponse:
        return TeamSummaryResponse(
            id=team.id,
            name=team.name,
            colour=team.colour,
            source=team.source,
            short_bio=profile.short_bio if profile else None,
            avatar=profile.avatar if profile else None,
        )

    @staticmethod
    def _attribution(
        content: DriverProfile | TeamProfile | ProfileNotableMoment,
    ) -> CuratedAttributionResponse:
        return CuratedAttributionResponse(
            source_url=content.source_url,
            publisher=content.publisher,
            published_at=content.published_at,
            confidence=ContentConfidence(content.confidence),
            data_quality_flags=list(content.data_quality_flags),
        )

    def _moment_response(
        self,
        moment: ProfileNotableMoment,
    ) -> NotableMomentResponse:
        return NotableMomentResponse(
            id=moment.id,
            title=moment.title,
            description=moment.description,
            occurred_at=moment.occurred_at,
            attribution=self._attribution(moment),
            created_at=moment.created_at,
            updated_at=moment.updated_at,
        )

    @staticmethod
    def _apply_profile_payload(
        profile: DriverProfile | TeamProfile,
        payload: DriverProfileUpsertRequest | TeamProfileUpsertRequest,
    ) -> None:
        profile.biography = payload.biography
        # Preserve new fields for older clients that only send a biography.
        # Explicit null clears a field; supplied objects replace it in full.
        for field in ("short_bio", "avatar", "details"):
            if field in payload.model_fields_set:
                value = getattr(payload, field)
                if field != "short_bio" and value is not None:
                    value = value.model_dump(mode="json")
                setattr(profile, field, value)
        profile.source_url = payload.source_url
        profile.publisher = payload.publisher
        profile.published_at = payload.published_at
        profile.confidence = payload.confidence.value
        profile.data_quality_flags = payload.data_quality_flags

    @staticmethod
    def _apply_moment_update(
        moment: ProfileNotableMoment,
        payload: ProfileNotableMomentUpdateRequest,
    ) -> None:
        values = payload.model_dump(exclude_unset=True)

        required_fields = {
            "title",
            "description",
            "source_url",
            "publisher",
            "published_at",
            "confidence",
            "data_quality_flags",
        }
        if any(
            values.get(field) is None
            for field in required_fields.intersection(values)
        ):
            raise ProfileContentValidationError(
                "Required notable-moment fields cannot be cleared."
            )

        for field, value in values.items():
            if field == "confidence" and value is not None:
                value = value.value
            setattr(moment, field, value)

    def _commit(self) -> None:
        self.db.commit()


class EditorialContentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_public_updates(
        self,
        *,
        update_type: EditorialUpdateType | None,
        meeting_id: UUID | None,
        race_session_id: UUID | None,
        driver_id: UUID | None,
        team_id: UUID | None,
        limit: int,
        offset: int = 0,
        category: str | None = None,
        calendar_weekend_id: UUID | None = None,
    ) -> list[EditorialUpdateResponse]:
        return self._list_updates(
            update_type=update_type,
            publication_status=EditorialPublicationStatus.PUBLISHED,
            meeting_id=meeting_id,
            race_session_id=race_session_id,
            driver_id=driver_id,
            team_id=team_id,
            limit=limit,
            public_only=True,
            offset=offset,
            category=category,
            calendar_weekend_id=calendar_weekend_id,
        )

    def list_editor_updates(
        self,
        *,
        update_type: EditorialUpdateType | None,
        publication_status: EditorialPublicationStatus | None,
        meeting_id: UUID | None,
        race_session_id: UUID | None,
        driver_id: UUID | None,
        team_id: UUID | None,
        limit: int,
    ) -> list[EditorialUpdateResponse]:
        return self._list_updates(
            update_type=update_type,
            publication_status=publication_status,
            meeting_id=meeting_id,
            race_session_id=race_session_id,
            driver_id=driver_id,
            team_id=team_id,
            limit=limit,
        )

    def _list_updates(
        self,
        *,
        update_type: EditorialUpdateType | None,
        publication_status: EditorialPublicationStatus | None,
        meeting_id: UUID | None,
        race_session_id: UUID | None,
        driver_id: UUID | None,
        team_id: UUID | None,
        limit: int,
        public_only: bool = False,
        offset: int = 0,
        category: str | None = None,
        calendar_weekend_id: UUID | None = None,
    ) -> list[EditorialUpdateResponse]:
        statement = select(EditorialUpdate)
        if category:
            statement = statement.where(
                func.coalesce(
                    EditorialUpdate.context["category"].as_string(), "GENERAL"
                )
                == category
            )
        if calendar_weekend_id:
            statement = statement.where(
                EditorialUpdate.context["calendar_weekend_id"].as_string()
                == str(calendar_weekend_id)
            )

        if publication_status is not None:
            statement = statement.where(
                EditorialUpdate.publication_status == publication_status.value
            )
        if public_only:
            statement = statement.where(
                EditorialUpdate.published_at <= datetime.now(UTC)
            )
        if update_type is not None:
            statement = statement.where(
                EditorialUpdate.update_type == update_type.value
            )
        if meeting_id is not None:
            statement = statement.where(
                EditorialUpdate.meeting_id == meeting_id
            )
        if race_session_id is not None:
            statement = statement.where(
                EditorialUpdate.race_session_id == race_session_id
            )
        if driver_id is not None:
            statement = statement.where(EditorialUpdate.driver_id == driver_id)
        if team_id is not None:
            statement = statement.where(EditorialUpdate.team_id == team_id)

        updates = self.db.scalars(
            statement.order_by(
                EditorialUpdate.published_at.desc(),
                EditorialUpdate.created_at.desc(),
                EditorialUpdate.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return [self._response(update) for update in updates]

    def get_public_update(self, update_id: UUID) -> EditorialUpdateResponse:
        update = self.db.scalar(
            select(EditorialUpdate).where(
                EditorialUpdate.id == update_id,
                EditorialUpdate.publication_status == "PUBLISHED",
                EditorialUpdate.published_at <= datetime.now(UTC),
            )
        )
        if update is None:
            raise EditorialUpdateNotFoundError("Editorial update not found.")
        return self._response(update)

    def create_update(
        self,
        *,
        payload: EditorialUpdateCreateRequest,
        editor_profile_id: UUID,
    ) -> EditorialUpdateResponse:
        self._validate_context(payload.context)
        self._validate_associations(
            meeting_id=payload.meeting_id,
            race_session_id=payload.race_session_id,
            driver_id=payload.driver_id,
            team_id=payload.team_id,
        )
        update = EditorialUpdate(
            context=payload.context.model_dump(mode="json"),
            update_type=payload.update_type.value,
            publication_status=payload.publication_status.value,
            title=payload.title,
            body=payload.body,
            meeting_id=payload.meeting_id,
            race_session_id=payload.race_session_id,
            driver_id=payload.driver_id,
            team_id=payload.team_id,
            source_url=payload.source_url,
            publisher=payload.publisher,
            published_at=payload.published_at,
            confidence=payload.confidence.value,
            data_quality_flags=payload.data_quality_flags,
            created_by_profile_id=editor_profile_id,
            updated_by_profile_id=editor_profile_id,
        )
        self.db.add(update)
        self.db.commit()
        self.db.refresh(update)
        return self._response(update)

    def update_update(
        self,
        *,
        update_id: UUID,
        payload: EditorialUpdateUpdateRequest,
        editor_profile_id: UUID,
    ) -> EditorialUpdateResponse:
        update = self._require_update(update_id)
        values = payload.model_dump(exclude_unset=True)
        if "context" in values:
            if payload.context is None:
                raise ProfileContentValidationError(
                    "Context cannot be cleared."
                )
            self._validate_context(payload.context)
            values["context"] = payload.context.model_dump(mode="json")

        required_fields = {
            "update_type",
            "publication_status",
            "title",
            "body",
            "source_url",
            "publisher",
            "published_at",
            "confidence",
            "data_quality_flags",
        }
        if any(
            values.get(field) is None
            for field in required_fields.intersection(values)
        ):
            raise ProfileContentValidationError(
                "Required editorial fields cannot be cleared."
            )

        for field, value in values.items():
            if field in {"update_type", "publication_status", "confidence"}:
                if value is not None:
                    value = value.value
            setattr(update, field, value)

        self._validate_associations(
            meeting_id=update.meeting_id,
            race_session_id=update.race_session_id,
            driver_id=update.driver_id,
            team_id=update.team_id,
        )
        update.updated_by_profile_id = editor_profile_id
        self.db.commit()
        self.db.refresh(update)
        return self._response(update)

    def delete_update(self, update_id: UUID) -> None:
        update = self._require_update(update_id)
        self.db.delete(update)
        self.db.commit()

    def _validate_associations(
        self,
        *,
        meeting_id: UUID | None,
        race_session_id: UUID | None,
        driver_id: UUID | None,
        team_id: UUID | None,
    ) -> None:
        meeting = self.db.get(Meeting, meeting_id) if meeting_id else None
        race_session = (
            self.db.get(RaceSession, race_session_id)
            if race_session_id
            else None
        )

        if meeting_id is not None and meeting is None:
            raise EditorialAssociationError("Meeting not found.")
        if race_session_id is not None and race_session is None:
            raise EditorialAssociationError("Race session not found.")
        if driver_id is not None and self.db.get(Driver, driver_id) is None:
            raise EditorialAssociationError("Driver not found.")
        if team_id is not None and self.db.get(Team, team_id) is None:
            raise EditorialAssociationError("Team not found.")
        if (
            meeting is not None
            and race_session is not None
            and race_session.meeting_id != meeting.id
        ):
            raise EditorialAssociationError(
                "The race session does not belong to the supplied meeting."
            )

    def _validate_context(self, context):
        from app.models.calendar import CalendarWeekend

        if (
            context.calendar_weekend_id
            and self.db.get(CalendarWeekend, context.calendar_weekend_id)
            is None
        ):
            raise EditorialAssociationError("Calendar weekend not found.")

    def _require_update(self, update_id: UUID) -> EditorialUpdate:
        update = self.db.get(EditorialUpdate, update_id)
        if update is None:
            raise EditorialUpdateNotFoundError("Editorial update not found.")
        return update

    @staticmethod
    def _response(update: EditorialUpdate) -> EditorialUpdateResponse:
        return EditorialUpdateResponse(
            context=update.context or {},
            id=update.id,
            update_type=EditorialUpdateType(update.update_type),
            publication_status=EditorialPublicationStatus(
                update.publication_status
            ),
            title=update.title,
            body=update.body,
            meeting_id=update.meeting_id,
            race_session_id=update.race_session_id,
            driver_id=update.driver_id,
            team_id=update.team_id,
            source_url=update.source_url,
            publisher=update.publisher,
            published_at=update.published_at,
            confidence=ContentConfidence(update.confidence),
            data_quality_flags=list(update.data_quality_flags),
            created_at=update.created_at,
            updated_at=update.updated_at,
        )

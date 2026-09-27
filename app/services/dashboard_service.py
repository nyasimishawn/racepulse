from collections import defaultdict
from datetime import UTC, datetime
from statistics import mean
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.schemas.dashboard import (
    DashboardTrack,
    TeamWinEstimate,
    TrackDashboardResponse,
    TrackDriverStats,
)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )


def _track_key(meeting: Meeting) -> tuple[str, str, str]:
    # Do not join providers or guess that differently named circuits are equal.
    return (
        meeting.source,
        (meeting.country_name or "").strip().casefold(),
        (meeting.location or meeting.name).strip().casefold(),
    )


class DashboardService:
    def __init__(self, db: Session):
        self.db = db

    def tracks(self) -> list[DashboardTrack]:
        groups = defaultdict(list)
        for meeting in self.db.scalars(select(Meeting)).all():
            groups[_track_key(meeting)].append(meeting)
        return sorted(
            [
                self._track(
                    sorted(group, key=lambda m: (m.year, str(m.id)))[-1], group
                )
                for group in groups.values()
            ],
            key=lambda track: (track.name, track.source),
        )

    @staticmethod
    def _track(meeting: Meeting, matches: list[Meeting]) -> DashboardTrack:
        return DashboardTrack(
            meeting_id=meeting.id,
            name=meeting.location or meeting.name,
            country=meeting.country_name,
            source=meeting.source,
            available_years=sorted({m.year for m in matches}, reverse=True),
            matching_basis=(
                "SOURCE_COUNTRY_LOCATION"
                if meeting.location
                else "SOURCE_COUNTRY_MEETING_NAME"
            ),
        )

    def dashboard(
        self, meeting_id: UUID, season_year: int, as_of: datetime | None = None
    ) -> TrackDashboardResponse:
        selected = self.db.get(Meeting, meeting_id)
        if selected is None:
            raise LookupError("Track meeting not found.")
        now = datetime.now(UTC)
        cutoff = min(
            _utc(as_of) if as_of else now,
            now,
            datetime(season_year + 1, 1, 1, tzinfo=UTC),
        )
        meetings = self.db.scalars(
            select(Meeting).where(
                Meeting.source == selected.source,
                Meeting.year <= season_year,
            )
        ).all()
        matches = [
            m for m in meetings if _track_key(m) == _track_key(selected)
        ]
        track_ids = {m.id for m in matches}
        rows = self.db.execute(
            select(SessionResult, RaceSession, Meeting, Driver, Team)
            .join(RaceSession, SessionResult.race_session_id == RaceSession.id)
            .join(Meeting, RaceSession.meeting_id == Meeting.id)
            .join(Driver, SessionResult.driver_id == Driver.id)
            .outerjoin(Team, SessionResult.team_id == Team.id)
            .where(
                Meeting.source == selected.source,
                Meeting.year <= season_year,
                RaceSession.session_identifier.in_(["R", "Q"]),
            )
        ).all()
        flags = {"IMPORTED_HISTORY_ONLY", "UNCALIBRATED_ESTIMATES"}
        usable = []
        for row in rows:
            result, session, meeting, driver, team = row
            timestamp = session.started_at or meeting.event_date
            if timestamp is None:
                flags.add("UNDATED_SESSIONS_EXCLUDED")
            elif _utc(timestamp) < cutoff:
                usable.append(row)
        track_rows = [row for row in usable if row[2].id in track_ids]
        races = [row for row in usable if row[1].session_identifier == "R"]
        season = [row for row in races if row[2].year == season_year]
        stats: dict[UUID, TrackDriverStats] = {}
        qualifying = defaultdict(list)
        teammate_groups = defaultdict(list)
        race_ids, qualifying_ids = set(), set()
        for result, session, meeting, driver, team in track_rows:
            stat = stats.setdefault(
                driver.id,
                TrackDriverStats(
                    driver_id=driver.id,
                    driver_number=driver.driver_number,
                    name=driver.full_name
                    or driver.abbreviation
                    or driver.driver_number,
                ),
            )
            if session.session_identifier == "Q":
                qualifying_ids.add(session.id)
                if result.position is not None and result.position > 0:
                    stat.qualifying_entries += 1
                    stat.qualifying_poles += int(result.position == 1)
                    qualifying[driver.id].append(result.position)
                    if team:
                        teammate_groups[(session.id, team.id)].append(result)
                continue
            race_ids.add(session.id)
            stat.race_entries += 1
            stat.points += float(result.points or 0)
            stat.points_missing_count += int(result.points is None)
            stat.podiums += int(
                result.position is not None and 1 <= result.position <= 3
            )
            if result.position == 1:
                stat.wins += 1
                if result.grid_position == 1:
                    stat.wins_from_pole += 1
                elif (
                    result.grid_position is not None
                    and result.grid_position >= 0
                ):
                    stat.wins_outside_pole += 1
                else:
                    stat.wins_with_unknown_grid += 1
        advantages = defaultdict(list)
        for results in teammate_groups.values():
            for result in results:
                others = [
                    r.position
                    for r in results
                    if r.driver_id != result.driver_id
                ]
                if others:
                    advantages[result.driver_id].append(
                        mean(others) - result.position
                    )
        for driver_id, stat in stats.items():
            if qualifying[driver_id]:
                stat.average_qualifying_position = round(
                    mean(qualifying[driver_id]), 2
                )
            if advantages[driver_id]:
                stat.teammate_qualifying_advantage = round(
                    mean(advantages[driver_id]), 2
                )
                stat.teammate_comparison_count = len(advantages[driver_id])
        for result, _, _, driver, _ in season:
            if driver.id in stats:
                stats[driver.id].season_points += float(result.points or 0)
                stats[driver.id].season_race_entries += 1
                stats[driver.id].season_points_missing_count += int(
                    result.points is None
                )
        if any(row[0].points is None for row in season):
            flags.add("SEASON_POINTS_PARTIAL")
        estimates, available, latest = self._estimates(
            season, track_rows, stats, flags
        )
        insights = []
        for stat in sorted(stats.values(), key=lambda s: -s.qualifying_poles):
            if stat.qualifying_poles >= 2:
                insights.append(
                    f"{stat.name}: {stat.qualifying_poles} poles from "
                    f"{stat.qualifying_entries} imported qualifying results at this circuit."
                )
            if (
                stat.teammate_comparison_count >= 3
                and stat.teammate_qualifying_advantage > 0
            ):
                insights.append(
                    f"{stat.name} qualified an average of "
                    f"{stat.teammate_qualifying_advantage:g} positions ahead of "
                    f"teammates here ({stat.teammate_comparison_count} comparisons)."
                )
        if not race_ids:
            flags.add("NO_TRACK_RACE_HISTORY")
        if any(stat.points_missing_count for stat in stats.values()):
            flags.add("TRACK_POINTS_PARTIAL")
        return TrackDashboardResponse(
            track=self._track(selected, matches),
            season_year=season_year,
            as_of=cutoff,
            drivers=sorted(
                stats.values(), key=lambda s: (-s.wins, -s.podiums, s.name)
            ),
            team_estimates=estimates,
            track_race_count=len(race_ids),
            track_qualifying_count=len(qualifying_ids),
            season_race_count=len({row[1].id for row in season}),
            latest_season_race_at=latest,
            forecast_available=available,
            model_explanation=(
                "Uncalibrated heuristic: 60% season race-points share, 25% circuit "
                "race-win share, 15% circuit poles of drivers in each team's latest "
                "imported race lineup. Each component adds one pseudo-count per "
                "team and normalizes across teams in the latest imported race. "
                "Requires three races with ten entries and exactly one winner each, "
                "plus known team/points data. These are scenario estimates, "
                "not calibrated probabilities; no claim about underlying car strength."
            ),
            insights=insights[:12],
            data_quality_flags=sorted(flags),
            coverage_disclaimer=(
                "Only imported Race and Qualifying results before the displayed "
                "cutoff are counted; sprints are excluded. Circuit matching uses "
                "source plus country/location, falling back to meeting name; layout "
                "changes are not identified. Missing history is not zero career "
                "performance. Points are stored race points. Grid P1 defines wins "
                "from pole; qualifying poles are counted separately. Forecasts use "
                "the latest imported lineup, not a verified current entry list."
            ),
        )

    @staticmethod
    def _estimates(season, track_rows, stats, flags):
        if not season:
            flags.add("NO_CURRENT_SEASON_RESULTS")
            return [], False, None
        latest_row = max(
            season,
            key=lambda row: (
                _utc(row[1].started_at or row[2].event_date),
                str(row[1].id),
            ),
        )
        latest_session = latest_row[1]
        latest_at = _utc(latest_session.started_at or latest_row[2].event_date)
        lineup = [row for row in season if row[1].id == latest_session.id]
        teams = {row[4].id: row[4] for row in lineup if row[4] is not None}
        form, season_wins, track_wins, poles = (
            defaultdict(float) for _ in range(4)
        )
        for result, _, _, _, team in season:
            if team and team.id in teams:
                form[team.id] += max(float(result.points or 0), 0)
                season_wins[team.id] += int(result.position == 1)
        for result, session, _, _, team in track_rows:
            if team and team.id in teams and session.session_identifier == "R":
                track_wins[team.id] += int(result.position == 1)
        for _, _, _, driver, team in lineup:
            if team and driver.id in stats:
                poles[team.id] += stats[driver.id].qualifying_poles
        session_ids = {row[1].id for row in season}
        winners = defaultdict(int)
        entries = defaultdict(int)
        for row in season:
            winners[row[1].id] += int(row[0].position == 1)
            entries[row[1].id] += 1
        available = (
            len(session_ids) >= 3
            and len(lineup) >= 10
            and len(teams) >= 2
            and all(
                winners[id] == 1 and entries[id] >= 10 for id in session_ids
            )
            and all(row[0].points is not None and row[4] for row in season)
        )
        if not available:
            flags.add("INSUFFICIENT_SEASON_COVERAGE_FOR_ESTIMATE")

        def share(component, team_id):
            return (component[team_id] + 1) / (
                sum(component.values()) + len(teams)
            )

        estimates = []
        for team_id, team in teams.items():
            components = [share(c, team_id) for c in (form, track_wins, poles)]
            estimate = sum(
                w * c for w, c in zip((0.60, 0.25, 0.15), components)
            )
            estimates.append(
                TeamWinEstimate(
                    team_id=team_id,
                    name=team.name,
                    colour=team.colour,
                    estimated_win_percent=round(100 * estimate, 2)
                    if available
                    else None,
                    season_points=form[team_id],
                    season_wins=int(season_wins[team_id]),
                    track_wins=int(track_wins[team_id]),
                    driver_track_poles=int(poles[team_id]),
                    season_form_share=components[0],
                    track_history_share=components[1],
                    driver_affinity_share=components[2],
                )
            )
        return (
            sorted(
                estimates,
                key=lambda e: -(e.estimated_win_percent or e.season_points),
            ),
            available,
            latest_at,
        )

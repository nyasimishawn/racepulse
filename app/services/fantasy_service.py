from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.services.calendar_service import calendar_identifier, fantasy_schedule
from app.models.fantasy import (
    FantasyEntryStatus,
    FantasyGroup,
    FantasyGroupMember,
    FantasyGroupWeekendEligibility,
    FantasyGroupWeekendResult,
    FantasyPickScoreStatus,
    FantasyPrediction,
    FantasyPredictionPick,
    FantasyPredictionPickScore,
    FantasyQuestionResolution,
    FantasyQuestionResolutionStatus,
    FantasyWeekendQuestion,
    FantasyWeekendQuestionStatus,
)
from app.models.lap import Lap
from app.models.meeting import Meeting
from app.models.race_session import RaceSession
from app.models.session_result import SessionResult
from app.models.team import Team
from app.models.user_profile import UserProfile
from app.schemas.fantasy import (
    FantasyCommunityOptionResponse,
    FantasyCommunityResponse,
    FantasyDashboardResponse,
    FantasyDriverOptionResponse,
    FantasyEntryProgressResponse,
    FantasyFinalizeResponse,
    FantasyGroupCreateRequest,
    FantasyGroupDetailResponse,
    FantasyGroupInviteCodeResponse,
    FantasyGroupJoinRequest,
    FantasyGroupMemberResponse,
    FantasyGroupPodiumResponse,
    FantasyGroupPodiumRowResponse,
    FantasyGroupSummaryResponse,
    FantasyLeaderboardResponse,
    FantasyLeaderboardRowResponse,
    FantasyPickScoreResponse,
    FantasyPredictionAnswerInput,
    FantasyPredictionAnswerResponse,
    FantasyPredictionResponse,
    FantasyPredictionUpdateRequest,
    FantasyQuestionResolutionRequest,
    FantasyQuestionResolutionResponse,
    FantasyQuestionSaveRequest,
    FantasyQuestionSaveResponse,
    FantasyQuestionResponse,
    FantasyRaceSummaryResponse,
    FantasyScoreRunResponse,
    FantasyTeamOptionResponse,
)


class FantasyError(Exception):
    pass


class FantasyRaceNotFoundError(FantasyError):
    pass


class FantasyNotRaceSessionError(FantasyError):
    pass


class FantasyInvalidRequestError(FantasyError):
    pass


class FantasyPredictionLockedError(FantasyError):
    def __init__(self, question_keys: list[str]) -> None:
        self.question_keys = question_keys
        super().__init__(
            "One or more prediction questions are already locked."
        )


class FantasyGroupNotFoundError(FantasyError):
    pass


class FantasyGroupPermissionError(FantasyError):
    pass


class FantasyGroupFullError(FantasyError):
    pass


class FantasyResolutionStateError(FantasyError):
    def __init__(self, pending_question_keys: list[str]) -> None:
        self.pending_question_keys = pending_question_keys
        super().__init__(
            "Every Fantasy question must be resolved or not scored "
            "before this weekend can be finalized."
        )


class FantasyGroupWeekendNotFinalizedError(FantasyError):
    pass


class FantasyCommunityPrivacyError(FantasyError):
    pass


@dataclass(frozen=True, slots=True)
class FantasyQuestionDefinition:
    key: str
    label: str
    category: str
    answer_kind: str
    target_session_id: UUID
    target_session_name: str
    locks_at: datetime | None
    max_points: int
    selection_limit: int = 1
    qualifying_column: str | None = None
    race_position: int | None = None
    snapshot_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class FantasyRaceContext:
    race: RaceSession
    meeting: Meeting
    sessions: list[RaceSession]


@dataclass(frozen=True, slots=True)
class DerivedResolution:
    driver_id: UUID | None
    team_id: UUID | None
    driver_ids: list[str]
    source_reference: str
    data_quality_flags: list[str]


class FantasyService:
    _PODIUM_KEYS = ("RACE_P1", "RACE_P2", "RACE_P3")
    _QUALIFYING_KEYS = (
        "Q1_FASTEST",
        "Q2_FASTEST",
        "Q3_FASTEST",
    )

    def __init__(
        self,
        db: Session,
        now: datetime | None = None,
    ) -> None:
        self.db = db
        self._fixed_now = now

    def list_races(
        self,
        year: int | None = None,
    ) -> list[FantasyRaceSummaryResponse]:
        statement = (
            select(RaceSession, Meeting)
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .order_by(
                Meeting.year.desc(),
                RaceSession.started_at.desc(),
                RaceSession.name,
            )
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        rows = self.db.execute(statement).all()

        return [
            self._race_summary(
                FantasyRaceContext(
                    race=race,
                    meeting=meeting,
                    sessions=[],
                )
            )
            for race, meeting in rows
            if self._is_race_session(race)
        ]

    def get_prediction(
        self,
        user_profile_id: UUID,
        race_session_id: UUID,
    ) -> FantasyPredictionResponse:
        context = self._load_race_context(race_session_id)
        questions = self._questions_for_context(context)
        prediction = self._prediction_for(
            user_profile_id,
            race_session_id,
        )
        if prediction is not None:
            self._sync_entry_lifecycle(context, prediction, questions)

        self.db.commit()
        return self._prediction_response(context, prediction)

    def get_entry_progress(
        self,
        user_profile_id: UUID,
        race_session_id: UUID,
    ) -> FantasyEntryProgressResponse:
        context = self._load_race_context(race_session_id)
        prediction = self._prediction_for(
            user_profile_id,
            race_session_id,
        )
        response = self._entry_progress_response(
            context,
            prediction,
            user_profile_id,
        )
        self.db.commit()
        return response

    def save_question(
        self,
        *,
        user_profile_id: UUID,
        race_session_id: UUID,
        question_key: str,
        payload: FantasyQuestionSaveRequest,
    ) -> FantasyQuestionSaveResponse:
        normalized_key = question_key.strip().upper()
        update = FantasyPredictionUpdateRequest(
            answers=(
                []
                if payload.clear
                else [
                    FantasyPredictionAnswerInput(
                        question_key=normalized_key,
                        driver_id=payload.driver_id,
                        team_id=payload.team_id,
                        driver_ids=payload.driver_ids,
                    )
                ]
            ),
            clear_question_keys=[normalized_key] if payload.clear else [],
        )
        self.save_prediction(
            user_profile_id,
            race_session_id,
            update,
        )
        entry = self.get_entry_progress(
            user_profile_id,
            race_session_id,
        )
        question = next(
            item for item in entry.questions if item.key == normalized_key
        )
        return FantasyQuestionSaveResponse(entry=entry, question=question)

    def get_dashboard(
        self,
        *,
        user_profile_id: UUID,
        year: int | None,
    ) -> FantasyDashboardResponse:
        response = self._dashboard_response(user_profile_id, year)
        self.db.commit()
        return response

    def save_prediction(
        self,
        user_profile_id: UUID,
        race_session_id: UUID,
        payload: FantasyPredictionUpdateRequest,
    ) -> FantasyPredictionResponse:
        context = self._load_race_context(race_session_id)
        questions = self._questions_for_context(context)
        definitions = {
            question.key: question
            for question in questions
        }

        if not payload.answers and not payload.clear_question_keys:
            return self.get_prediction(
                user_profile_id,
                race_session_id,
            )

        requested_keys = {
            answer.question_key for answer in payload.answers
        }.union(payload.clear_question_keys)

        unknown_keys = sorted(requested_keys.difference(definitions))

        if unknown_keys:
            raise FantasyInvalidRequestError(
                f"Unknown Fantasy question(s): {', '.join(unknown_keys)}."
            )

        locked_keys = sorted(
            key
            for key in requested_keys
            if not self._question_is_open(definitions[key])
        )

        if locked_keys:
            raise FantasyPredictionLockedError(locked_keys)

        driver_ids, team_ids = self._candidate_id_sets(context)

        for answer in payload.answers:
            self._validate_prediction_answer(
                definitions[answer.question_key],
                answer,
                driver_ids,
                team_ids,
            )

        prediction = self._prediction_for(
            user_profile_id,
            race_session_id,
        )

        if prediction is None:
            prediction = FantasyPrediction(
                user_profile_id=user_profile_id,
                race_session_id=race_session_id,
            )
            self.db.add(prediction)
            self.db.flush()

        picks_by_key = {
            pick.question_key: pick
            for pick in self.db.scalars(
                select(FantasyPredictionPick).where(
                    FantasyPredictionPick.prediction_id
                    == prediction.id
                )
            ).all()
        }

        for question_key in payload.clear_question_keys:
            pick = picks_by_key.pop(question_key, None)

            if pick is not None:
                self._delete_pick_and_score(pick)

        for answer in payload.answers:
            definition = definitions[answer.question_key]
            pick = picks_by_key.get(answer.question_key)

            if pick is None:
                pick = FantasyPredictionPick(
                    prediction_id=prediction.id,
                    question_key=definition.key,
                    target_session_id=definition.target_session_id,
                )
                self.db.add(pick)
                picks_by_key[definition.key] = pick
            else:
                self._delete_pick_score(pick)

            pick.target_session_id = definition.target_session_id
            pick.driver_id = answer.driver_id
            pick.team_id = answer.team_id
            pick.driver_ids = [
                str(driver_id)
                for driver_id in (answer.driver_ids or [])
            ]

        self._validate_podium_choices(picks_by_key)
        self._sync_entry_lifecycle(
            context,
            prediction,
            list(definitions.values()),
        )
        self.db.commit()

        return self.get_prediction(
            user_profile_id,
            race_session_id,
        )

    def score_available_questions(
        self,
        race_session_id: UUID,
    ) -> FantasyScoreRunResponse:
        context = self._load_race_context(race_session_id)
        resolutions = self._resolutions_for_race(race_session_id)
        questions = self._questions_for_context(context)

        resolved_keys: list[str] = []
        pending_keys: list[str] = []
        updated_pick_count = 0

        for question in questions:
            if not self._question_is_locked(question):
                pending_keys.append(question.key)
                continue

            resolution = resolutions.get(question.key)

            if (
                resolution is None
                or resolution.resolution_source != "EDITOR"
            ):
                derived = self._automatic_resolution(context, question)

                if derived is not None:
                    resolution = self._upsert_resolution(
                        context=context,
                        question=question,
                        status=(
                            FantasyQuestionResolutionStatus.RESOLVED
                        ),
                        resolution_source="AUTOMATED",
                        driver_id=derived.driver_id,
                        team_id=derived.team_id,
                        driver_ids=derived.driver_ids,
                        source_reference=derived.source_reference,
                        data_quality_flags=derived.data_quality_flags,
                        resolved_by_profile_id=None,
                    )
                    resolutions[question.key] = resolution

            if (
                resolution is None
                or resolution.status
                == FantasyQuestionResolutionStatus.PENDING
            ):
                pending_keys.append(question.key)
                continue

            resolved_keys.append(question.key)
            updated_pick_count += self._score_question(
                context,
                question,
                resolution,
            )

        self._sync_entries_for_race(context, questions)
        self.db.commit()

        return FantasyScoreRunResponse(
            race_session_id=race_session_id,
            resolved_question_keys=resolved_keys,
            pending_question_keys=pending_keys,
            updated_pick_count=updated_pick_count,
        )

    def set_question_resolution(
        self,
        *,
        race_session_id: UUID,
        question_key: str,
        payload: FantasyQuestionResolutionRequest,
        resolved_by_profile_id: UUID,
    ) -> FantasyQuestionResolutionResponse:
        context = self._load_race_context(race_session_id)
        questions = self._questions_for_context(context)
        definitions = {
            question.key: question
            for question in questions
        }
        question = definitions.get(question_key.strip().upper())

        if question is None:
            raise FantasyInvalidRequestError(
                "This Fantasy question does not exist for the race."
            )

        if payload.source_reference is None:
            raise FantasyInvalidRequestError(
                "An editor resolution needs a source reference."
            )

        status = FantasyQuestionResolutionStatus(payload.status)
        driver_ids, team_ids = self._candidate_id_sets(context)

        if status == FantasyQuestionResolutionStatus.RESOLVED:
            driver_id, team_id, actual_driver_ids = (
                self._validate_manual_resolution(
                    question,
                    payload,
                    driver_ids,
                    team_ids,
                )
            )
        else:
            if (
                payload.actual_driver_id is not None
                or payload.actual_team_id is not None
                or payload.actual_driver_ids
            ):
                raise FantasyInvalidRequestError(
                    "NOT_SCORED questions must not contain an outcome."
                )

            driver_id = None
            team_id = None
            actual_driver_ids = []

        if (
            question.race_position is not None
            and driver_id is not None
        ):
            self._validate_manual_podium_resolution(
                race_session_id,
                question.key,
                driver_id,
            )

        resolution = self._upsert_resolution(
            context=context,
            question=question,
            status=status,
            resolution_source="EDITOR",
            driver_id=driver_id,
            team_id=team_id,
            driver_ids=actual_driver_ids,
            source_reference=payload.source_reference,
            data_quality_flags=payload.data_quality_flags,
            resolved_by_profile_id=resolved_by_profile_id,
        )

        self._score_question(context, question, resolution)
        if self._has_finalized_results(race_session_id):
            self._finalize_groups(context, questions)

        self._sync_entries_for_race(context, questions)
        self.db.commit()

        return self._resolution_response(resolution)

    def finalize_weekend(
        self,
        race_session_id: UUID,
    ) -> FantasyFinalizeResponse:
        context = self._load_race_context(race_session_id)

        if context.race.started_at is None:
            raise FantasyInvalidRequestError(
                "The race needs a start time before group results "
                "can be finalized."
            )

        score_run = self.score_available_questions(race_session_id)

        if score_run.pending_question_keys:
            raise FantasyResolutionStateError(
                score_run.pending_question_keys
            )

        questions = self._questions_for_context(context)
        response = self._finalize_groups(context, questions)
        self._sync_entries_for_race(context, questions)
        self.db.commit()
        return response

    def get_global_leaderboard(
        self,
        *,
        current_profile_id: UUID,
        year: int | None,
        race_session_id: UUID | None,
    ) -> FantasyLeaderboardResponse:
        if race_session_id is not None:
            self._load_race_context(race_session_id)

        statement = (
            select(
                UserProfile.id,
                UserProfile.display_name,
                FantasyPredictionPick.question_key,
                FantasyPredictionPickScore.points_awarded,
                FantasyPredictionPickScore.is_exact,
            )
            .select_from(FantasyPredictionPickScore)
            .join(
                FantasyPredictionPick,
                FantasyPredictionPick.id
                == FantasyPredictionPickScore.pick_id,
            )
            .join(
                FantasyPrediction,
                FantasyPrediction.id
                == FantasyPredictionPick.prediction_id,
            )
            .join(
                UserProfile,
                UserProfile.id == FantasyPrediction.user_profile_id,
            )
            .join(
                RaceSession,
                RaceSession.id == FantasyPrediction.race_session_id,
            )
            .join(
                Meeting,
                Meeting.id == RaceSession.meeting_id,
            )
            .where(
                FantasyPredictionPickScore.status
                == FantasyPickScoreStatus.SCORED
            )
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        if race_session_id is not None:
            statement = statement.where(
                FantasyPrediction.race_session_id == race_session_id
            )

        stats: dict[UUID, list[object]] = {}

        for (
            profile_id,
            display_name,
            question_key,
            points_awarded,
            is_exact,
        ) in self.db.execute(statement).all():
            row = stats.setdefault(
                profile_id,
                [
                    display_name or "RacePulse fan",
                    0,
                    0,
                    0,
                ],
            )
            row[1] = int(row[1]) + int(points_awarded)

            if is_exact and question_key in self._PODIUM_KEYS:
                row[2] = int(row[2]) + 1

            if is_exact and question_key in self._QUALIFYING_KEYS:
                row[3] = int(row[3]) + 1

        return FantasyLeaderboardResponse(
            scope="GLOBAL",
            group_id=None,
            race_session_id=race_session_id,
            year=year,
            rows=self._leaderboard_rows(
                stats,
                current_profile_id,
            ),
        )

    def create_group(
        self,
        owner_profile_id: UUID,
        payload: FantasyGroupCreateRequest,
    ) -> FantasyGroupDetailResponse:
        group = FantasyGroup(
            owner_profile_id=owner_profile_id,
            name=payload.name,
            invite_code=self._new_invite_code(),
            max_members=payload.max_members,
        )
        self.db.add(group)
        self.db.flush()

        self.db.add(
            FantasyGroupMember(
                group_id=group.id,
                user_profile_id=owner_profile_id,
                created_at=self._now(),
            )
        )
        self.db.commit()

        return self._group_detail(group, owner_profile_id)

    def list_groups(
        self,
        profile_id: UUID,
    ) -> list[FantasyGroupSummaryResponse]:
        rows = self.db.execute(
            select(FantasyGroup, FantasyGroupMember)
            .join(
                FantasyGroupMember,
                FantasyGroupMember.group_id == FantasyGroup.id,
            )
            .where(FantasyGroupMember.user_profile_id == profile_id)
            .order_by(FantasyGroup.created_at.desc())
        ).all()

        return [
            self._group_summary(group, member, profile_id)
            for group, member in rows
        ]

    def join_group(
        self,
        profile_id: UUID,
        payload: FantasyGroupJoinRequest,
    ) -> FantasyGroupDetailResponse:
        group = self.db.scalar(
            select(FantasyGroup)
            .where(FantasyGroup.invite_code == payload.invite_code)
            .with_for_update()
        )

        if group is None:
            raise FantasyGroupNotFoundError(
                "The Fantasy group invite code is invalid."
            )

        existing_member = self.db.scalar(
            select(FantasyGroupMember).where(
                FantasyGroupMember.group_id == group.id,
                FantasyGroupMember.user_profile_id == profile_id,
            )
        )

        if existing_member is not None:
            return self._group_detail(group, profile_id)

        self._materialize_locked_group_eligibilities(group)

        member_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(FantasyGroupMember)
                .where(FantasyGroupMember.group_id == group.id)
            )
            or 0
        )

        if member_count >= group.max_members:
            raise FantasyGroupFullError(
                "This Fantasy group already has its maximum members."
            )

        self.db.add(
            FantasyGroupMember(
                group_id=group.id,
                user_profile_id=profile_id,
                created_at=self._now(),
            )
        )
        self.db.commit()

        return self._group_detail(group, profile_id)

    def get_group_detail(
        self,
        group_id: UUID,
        profile_id: UUID,
    ) -> FantasyGroupDetailResponse:
        group = self._get_group(group_id)
        self._require_group_member(group, profile_id)
        return self._group_detail(group, profile_id)

    def rotate_group_invite_code(
        self,
        group_id: UUID,
        profile_id: UUID,
    ) -> FantasyGroupInviteCodeResponse:
        group = self._get_group(group_id)
        self._require_group_owner(group, profile_id)

        group.invite_code = self._new_invite_code()
        self.db.commit()

        return FantasyGroupInviteCodeResponse(
            group_id=group.id,
            invite_code=group.invite_code,
        )

    def remove_group_member(
        self,
        *,
        group_id: UUID,
        owner_profile_id: UUID,
        member_profile_id: UUID,
    ) -> None:
        group = self._get_group(group_id)
        self._require_group_owner(group, owner_profile_id)

        if member_profile_id == group.owner_profile_id:
            raise FantasyInvalidRequestError(
                "The group owner cannot be removed."
            )

        member = self.db.scalar(
            select(FantasyGroupMember).where(
                FantasyGroupMember.group_id == group_id,
                FantasyGroupMember.user_profile_id == member_profile_id,
            )
        )

        if member is None:
            raise FantasyInvalidRequestError(
                "That user is not a member of this group."
            )

        self._materialize_locked_group_eligibilities(group)
        self.db.delete(member)
        self.db.commit()

    def get_group_leaderboard(
        self,
        *,
        group_id: UUID,
        current_profile_id: UUID,
        year: int | None,
        race_session_id: UUID | None,
    ) -> FantasyLeaderboardResponse:
        group = self._get_group(group_id)
        self._require_group_member(group, current_profile_id)

        statement = (
            select(
                FantasyGroupWeekendResult,
                UserProfile.display_name,
            )
            .join(
                UserProfile,
                UserProfile.id
                == FantasyGroupWeekendResult.user_profile_id,
            )
            .where(FantasyGroupWeekendResult.group_id == group_id)
        )

        if year is not None:
            statement = (
                statement.join(
                    RaceSession,
                    RaceSession.id
                    == FantasyGroupWeekendResult.race_session_id,
                )
                .join(
                    Meeting,
                    Meeting.id == RaceSession.meeting_id,
                )
                .where(Meeting.year == year)
            )

        if race_session_id is not None:
            statement = statement.where(
                FantasyGroupWeekendResult.race_session_id
                == race_session_id
            )

        stats: dict[UUID, list[object]] = {}

        for result, display_name in self.db.execute(statement).all():
            row = stats.setdefault(
                result.user_profile_id,
                [
                    display_name or "RacePulse fan",
                    0,
                    0,
                    0,
                ],
            )
            row[1] = int(row[1]) + result.points
            row[2] = int(row[2]) + result.exact_podium_hits
            row[3] = int(row[3]) + result.exact_qualifying_hits

        return FantasyLeaderboardResponse(
            scope="GROUP",
            group_id=group_id,
            race_session_id=race_session_id,
            year=year,
            rows=self._leaderboard_rows(
                stats,
                current_profile_id,
            ),
        )

    def get_group_podium(
        self,
        *,
        group_id: UUID,
        race_session_id: UUID,
        profile_id: UUID,
    ) -> FantasyGroupPodiumResponse:
        group = self._get_group(group_id)
        self._require_group_member(group, profile_id)

        rows = self.db.execute(
            select(
                FantasyGroupWeekendResult,
                UserProfile.display_name,
            )
            .join(
                UserProfile,
                UserProfile.id
                == FantasyGroupWeekendResult.user_profile_id,
            )
            .where(
                FantasyGroupWeekendResult.group_id == group_id,
                FantasyGroupWeekendResult.race_session_id
                == race_session_id,
            )
            .order_by(
                FantasyGroupWeekendResult.rank,
                FantasyGroupWeekendResult.points.desc(),
            )
        ).all()

        if not rows:
            raise FantasyGroupWeekendNotFinalizedError(
                "This group's weekend results have not been finalized."
            )

        return FantasyGroupPodiumResponse(
            group_id=group_id,
            race_session_id=race_session_id,
            rows=[
                FantasyGroupPodiumRowResponse(
                    rank=result.rank,
                    display_name=display_name or "RacePulse fan",
                    points=result.points,
                    exact_podium_hits=result.exact_podium_hits,
                    exact_qualifying_hits=(
                        result.exact_qualifying_hits
                    ),
                )
                for result, display_name in rows[:3]
            ],
        )

    def get_community_percentages(
        self,
        *,
        race_session_id: UUID,
        question_key: str,
        current_profile_id: UUID,
    ) -> FantasyCommunityResponse:
        context = self._load_race_context(race_session_id)
        definitions = {
            question.key: question
            for question in self._questions_for_context(context)
        }
        question = definitions.get(question_key.strip().upper())

        if question is None:
            raise FantasyInvalidRequestError(
                "This Fantasy question does not exist for the race."
            )

        prediction = self._prediction_for(
            current_profile_id,
            race_session_id,
        )
        own_pick = None

        if prediction is not None:
            own_pick = self.db.scalar(
                select(FantasyPredictionPick).where(
                    FantasyPredictionPick.prediction_id == prediction.id,
                    FantasyPredictionPick.question_key == question.key,
                )
            )

        if not self._question_is_locked(question) and own_pick is None:
            raise FantasyCommunityPrivacyError(
                "Community percentages unlock after you submit a pick "
                "or when the question locks."
            )

        picks = self.db.scalars(
            select(FantasyPredictionPick)
            .join(
                FantasyPrediction,
                FantasyPrediction.id
                == FantasyPredictionPick.prediction_id,
            )
            .where(
                FantasyPrediction.race_session_id == race_session_id,
                FantasyPredictionPick.question_key == question.key,
            )
        ).all()

        drivers, teams = self._candidate_options(context)
        driver_labels = {
            str(driver.id): driver.display_name
            for driver in drivers
        }
        team_labels = {
            team.id: team.name
            for team in teams
        }

        counts: dict[UUID | str, int] = {}

        for pick in picks:
            if question.answer_kind == "DRIVER" and pick.driver_id:
                counts[pick.driver_id] = counts.get(pick.driver_id, 0) + 1

            elif question.answer_kind == "TEAM" and pick.team_id:
                counts[pick.team_id] = counts.get(pick.team_id, 0) + 1

            elif question.answer_kind == "DRIVER_LIST":
                for driver_id in pick.driver_ids:
                    counts[driver_id] = counts.get(driver_id, 0) + 1

        total_predictions = len(picks)
        options: list[FantasyCommunityOptionResponse] = []

        for option_id, selection_count in counts.items():
            if question.answer_kind == "TEAM":
                uuid_option_id = option_id
                label = team_labels.get(
                    uuid_option_id,
                    "Unknown team",
                )
            else:
                uuid_option_id = UUID(str(option_id))
                label = driver_labels.get(
                    str(uuid_option_id),
                    "Unknown driver",
                )

            options.append(
                FantasyCommunityOptionResponse(
                    option_id=uuid_option_id,
                    label=label,
                    selection_count=selection_count,
                    percentage_of_predictions=round(
                        (selection_count / total_predictions) * 100,
                        1,
                    )
                    if total_predictions
                    else 0.0,
                )
            )

        options.sort(
            key=lambda option: (
                -option.selection_count,
                option.label.casefold(),
            )
        )

        return FantasyCommunityResponse(
            question_key=question.key,
            total_predictions=total_predictions,
            multiple_selection=(
                question.answer_kind == "DRIVER_LIST"
            ),
            options=options,
        )

    def _load_race_context(
        self,
        race_session_id: UUID,
    ) -> FantasyRaceContext:
        race = self.db.get(RaceSession, race_session_id)

        if race is None:
            raise FantasyRaceNotFoundError(
                "Race session was not found."
            )

        if not self._is_race_session(race):
            raise FantasyNotRaceSessionError(
                "Fantasy predictions are available for race sessions."
            )

        meeting = self.db.get(Meeting, race.meeting_id)

        if meeting is None:
            raise FantasyRaceNotFoundError(
                "The race meeting was not found."
            )

        sessions = self.db.scalars(
            select(RaceSession)
            .where(RaceSession.meeting_id == meeting.id)
            .order_by(RaceSession.started_at, RaceSession.name)
        ).all()

        return FantasyRaceContext(
            race=race,
            meeting=meeting,
            sessions=sessions,
        )

    def _questions_for_context(
        self,
        context: FantasyRaceContext,
    ) -> list[FantasyQuestionDefinition]:
        derived_questions = self._derived_questions_for_context(context)
        schedule = fantasy_schedule(self.db, context.meeting.id)
        identifiers = {
            s.id: calendar_identifier(s.session_identifier)
            for s in context.sessions
        }
        derived_questions = [
            replace(
                question,
                locks_at=schedule[identifiers[question.target_session_id]],
            )
            if identifiers.get(question.target_session_id) in schedule
            else question
            for question in derived_questions
        ]
        snapshots = self.db.scalars(
            select(FantasyWeekendQuestion)
            .where(
                FantasyWeekendQuestion.race_session_id == context.race.id
            )
            .order_by(
                FantasyWeekendQuestion.sort_order,
                FantasyWeekendQuestion.question_key,
            )
        ).all()
        snapshots_by_key = {
            snapshot.question_key: snapshot for snapshot in snapshots
        }
        derived_by_key = {
            question.key: question for question in derived_questions
        }

        for sort_order, question in enumerate(derived_questions, start=1):
            snapshot = snapshots_by_key.get(question.key)

            if snapshot is None:
                snapshot = FantasyWeekendQuestion(
                    race_session_id=context.race.id,
                    question_key=question.key,
                    label=question.label,
                    category=question.category,
                    answer_kind=question.answer_kind,
                    target_session_id=question.target_session_id,
                    target_session_name=question.target_session_name,
                    locks_at=question.locks_at,
                    max_points=question.max_points,
                    selection_limit=question.selection_limit,
                    sort_order=sort_order,
                    status=self._snapshot_status_for_lock(question.locks_at),
                )
                self.db.add(snapshot)
                snapshots.append(snapshot)
                snapshots_by_key[question.key] = snapshot
                continue

            if snapshot.status not in {
                FantasyWeekendQuestionStatus.RESOLVED,
                FantasyWeekendQuestionStatus.NOT_SCORED,
            }:
                if identifiers.get(question.target_session_id) in schedule:
                    snapshot.locks_at = question.locks_at
                snapshot.status = self._snapshot_status_for_lock(
                    snapshot.locks_at
                )
                if (
                    identifiers.get(question.target_session_id) in schedule
                    and question.locks_at is None
                ):
                    snapshot.status = FantasyWeekendQuestionStatus.UNAVAILABLE

        self.db.flush()
        snapshots.sort(
            key=lambda snapshot: (
                snapshot.sort_order,
                snapshot.question_key,
            )
        )

        definitions = []
        for snapshot in snapshots:
            definition = self._definition_from_snapshot(
                snapshot, derived_by_key.get(snapshot.question_key)
            )
            identifier = identifiers.get(snapshot.target_session_id)
            if identifier in schedule and schedule[identifier] is None:
                definition = replace(definition, locks_at=None)
            definitions.append(definition)
        return definitions

    def _definition_from_snapshot(
        self,
        snapshot: FantasyWeekendQuestion,
        derived: FantasyQuestionDefinition | None,
    ) -> FantasyQuestionDefinition:
        qualifying_column = (
            derived.qualifying_column if derived is not None else None
        )
        race_position = (
            derived.race_position if derived is not None else None
        )

        if qualifying_column is None and snapshot.question_key in {
            "Q1_FASTEST",
            "Q2_FASTEST",
            "Q3_FASTEST",
        }:
            qualifying_column = (
                f"{snapshot.question_key[:2].casefold()}_time_ms"
            )

        if race_position is None:
            race_position = {
                "RACE_P1": 1,
                "RACE_P2": 2,
                "RACE_P3": 3,
            }.get(snapshot.question_key)

        return FantasyQuestionDefinition(
            key=snapshot.question_key,
            label=snapshot.label,
            category=snapshot.category,
            answer_kind=snapshot.answer_kind,
            target_session_id=snapshot.target_session_id,
            target_session_name=snapshot.target_session_name,
            locks_at=snapshot.locks_at,
            max_points=snapshot.max_points,
            selection_limit=snapshot.selection_limit,
            qualifying_column=qualifying_column,
            race_position=race_position,
            snapshot_id=snapshot.id,
        )

    def _snapshot_status_for_lock(
        self,
        locks_at: datetime | None,
    ) -> FantasyWeekendQuestionStatus:
        if locks_at is None:
            return FantasyWeekendQuestionStatus.UNAVAILABLE

        if self._now() >= self._as_utc(locks_at):
            return FantasyWeekendQuestionStatus.LOCKED

        return FantasyWeekendQuestionStatus.OPEN

    def _derived_questions_for_context(
        self,
        context: FantasyRaceContext,
    ) -> list[FantasyQuestionDefinition]:
        questions: list[FantasyQuestionDefinition] = []
        practice_by_identifier = {
            session.session_identifier.strip().upper(): session
            for session in context.sessions
            if session.session_identifier.strip().upper()
            in {"FP1", "FP2", "FP3"}
        }

        for identifier in ("FP1", "FP2", "FP3"):
            session = practice_by_identifier.get(identifier)

            if session is not None:
                questions.append(
                    FantasyQuestionDefinition(
                        key=f"{identifier}_P1",
                        label=f"{identifier} fastest driver",
                        category="PRACTICE",
                        answer_kind="DRIVER",
                        target_session_id=session.id,
                        target_session_name=session.name,
                        locks_at=session.started_at,
                        max_points=2,
                    )
                )

        qualifying = next(
            (
                session
                for session in context.sessions
                if self._is_qualifying_session(session)
            ),
            None,
        )

        if qualifying is not None:
            for segment in ("Q1", "Q2", "Q3"):
                questions.append(
                    FantasyQuestionDefinition(
                        key=f"{segment}_FASTEST",
                        label=f"{segment} fastest driver",
                        category="QUALIFYING",
                        answer_kind="DRIVER",
                        target_session_id=qualifying.id,
                        target_session_name=qualifying.name,
                        locks_at=qualifying.started_at,
                        max_points=3,
                        qualifying_column=(
                            f"{segment.casefold()}_time_ms"
                        ),
                    )
                )

        race = context.race
        race_questions = [
            (
                "RACE_P1",
                "Race winner",
                "DRIVER",
                10,
                1,
            ),
            (
                "RACE_P2",
                "Second place",
                "DRIVER",
                10,
                2,
            ),
            (
                "RACE_P3",
                "Third place",
                "DRIVER",
                10,
                3,
            ),
            (
                "RACE_FASTEST_LAP",
                "Fastest-lap driver",
                "DRIVER",
                5,
                None,
            ),
            (
                "RACE_WINNING_TEAM",
                "Winning team",
                "TEAM",
                5,
                None,
            ),
            (
                "RACE_TOP_SPEED",
                "Highest speed-trap driver",
                "DRIVER",
                5,
                None,
            ),
            (
                "RACE_DNF_DRIVERS",
                "Drivers to retire",
                "DRIVER_LIST",
                8,
                None,
            ),
        ]

        for key, label, answer_kind, points, race_position in race_questions:
            questions.append(
                FantasyQuestionDefinition(
                    key=key,
                    label=label,
                    category="RACE",
                    answer_kind=answer_kind,
                    target_session_id=race.id,
                    target_session_name=race.name,
                    locks_at=race.started_at,
                    max_points=points,
                    selection_limit=(
                        2 if key == "RACE_DNF_DRIVERS" else 1
                    ),
                    race_position=race_position,
                )
            )

        return questions

    def _prediction_response(
        self,
        context: FantasyRaceContext,
        prediction: FantasyPrediction | None,
    ) -> FantasyPredictionResponse:
        picks: list[FantasyPredictionPick] = []

        if prediction is not None:
            picks = self.db.scalars(
                select(FantasyPredictionPick).where(
                    FantasyPredictionPick.prediction_id == prediction.id
                )
            ).all()

        picks_by_key = {
            pick.question_key: pick
            for pick in picks
        }
        scores_by_pick = self._scores_for_picks(picks)
        resolutions = self._resolutions_for_race(context.race.id)
        drivers, teams = self._candidate_options(context)

        total_points = sum(
            score.points_awarded
            for score in scores_by_pick.values()
            if score.status == FantasyPickScoreStatus.SCORED
        )

        questions = [
            self._question_response(
                question,
                picks_by_key.get(question.key),
                scores_by_pick,
                resolutions.get(question.key),
            )
            for question in self._questions_for_context(context)
        ]

        return FantasyPredictionResponse(
            prediction_id=prediction.id if prediction else None,
            race=self._race_summary(context),
            total_points=total_points,
            questions=questions,
            drivers=drivers,
            teams=teams,
        )

    def _entry_progress_response(
        self,
        context: FantasyRaceContext,
        prediction: FantasyPrediction | None,
        user_profile_id: UUID,
    ) -> FantasyEntryProgressResponse:
        definitions = self._questions_for_context(context)

        if prediction is not None:
            self._sync_entry_lifecycle(context, prediction, definitions)

        prediction_response = self._prediction_response(context, prediction)
        questions = prediction_response.questions
        total_question_count = len(questions)
        answered_question_count = sum(
            question.is_answered for question in questions
        )
        locked_question_count = sum(
            question.state == "LOCKED" for question in questions
        )
        resolved_question_count = sum(
            question.status in {"RESOLVED", "NOT_SCORED"}
            for question in questions
        )
        scored_question_count = sum(
            question.answer is not None
            and question.answer.score is not None
            and question.answer.score.status in {"SCORED", "NOT_SCORED"}
            for question in questions
        )

        return FantasyEntryProgressResponse(
            prediction_id=prediction.id if prediction is not None else None,
            race=prediction_response.race,
            status=(
                prediction.status.value
                if prediction is not None
                else FantasyEntryStatus.DRAFT.value
            ),
            total_points=prediction_response.total_points,
            total_question_count=total_question_count,
            answered_question_count=answered_question_count,
            locked_question_count=locked_question_count,
            resolved_question_count=resolved_question_count,
            scored_question_count=scored_question_count,
            completion_percentage=(
                round(
                    (answered_question_count / total_question_count) * 100,
                    1,
                )
                if total_question_count
                else 0.0
            ),
            next_deadline=self._next_deadline(definitions),
            personal_race_rank=(
                self._personal_global_rank(
                    user_profile_id,
                    year=None,
                    race_session_id=context.race.id,
                )
                if prediction is not None
                else None
            ),
            personal_season_rank=(
                self._personal_global_rank(
                    user_profile_id,
                    year=context.meeting.year,
                    race_session_id=None,
                )
                if prediction is not None
                else None
            ),
            questions=questions,
        )

    def _dashboard_response(
        self,
        user_profile_id: UUID,
        year: int | None,
    ) -> FantasyDashboardResponse:
        selected_year = year or self._now().year
        rows = self.db.execute(
            select(RaceSession, Meeting)
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(Meeting.year == selected_year)
            .order_by(RaceSession.started_at, RaceSession.name)
        ).all()
        entries: list[FantasyEntryProgressResponse] = []

        for race, _ in rows:
            if not self._is_race_session(race):
                continue

            context = self._load_race_context(race.id)
            prediction = self._prediction_for(user_profile_id, race.id)

            if (
                prediction is None
                and race.started_at is not None
                and self._as_utc(race.started_at) < self._now()
            ):
                continue

            entries.append(
                self._entry_progress_response(
                    context,
                    prediction,
                    user_profile_id,
                )
            )

        entries.sort(
            key=lambda entry: (
                entry.next_deadline is None,
                entry.next_deadline or datetime.max.replace(tzinfo=UTC),
                entry.race.started_at or datetime.max.replace(tzinfo=UTC),
            )
        )
        season_stats = self._global_score_stats(
            year=selected_year,
            race_session_id=None,
        )
        season_points = int(
            season_stats.get(user_profile_id, ["", 0, 0, 0])[1]
        )

        return FantasyDashboardResponse(
            season_year=selected_year,
            season_points=season_points,
            season_rank=self._personal_global_rank(
                user_profile_id,
                year=selected_year,
                race_session_id=None,
            ),
            next_deadline=next(
                (
                    entry.next_deadline
                    for entry in entries
                    if entry.next_deadline is not None
                ),
                None,
            ),
            entries=entries,
            groups=self.list_groups(user_profile_id),
        )

    def _sync_entry_lifecycle(
        self,
        context: FantasyRaceContext,
        prediction: FantasyPrediction,
        questions: list[FantasyQuestionDefinition],
    ) -> None:
        picks = self.db.scalars(
            select(FantasyPredictionPick).where(
                FantasyPredictionPick.prediction_id == prediction.id
            )
        ).all()
        question_keys = {question.key for question in questions}
        answered_question_count = sum(
            pick.question_key in question_keys for pick in picks
        )
        resolutions = self._resolutions_for_race(context.race.id)
        lock_times = [
            self._as_utc(question.locks_at)
            for question in questions
            if question.locks_at is not None
        ]
        all_locked = bool(questions) and all(
            self._question_is_locked(question) for question in questions
        )
        all_resolved = bool(questions) and all(
            (resolution := resolutions.get(question.key)) is not None
            and resolution.status
            != FantasyQuestionResolutionStatus.PENDING
            for question in questions
        )
        has_any_resolution = any(
            resolution.status != FantasyQuestionResolutionStatus.PENDING
            for resolution in resolutions.values()
        )

        prediction.first_lock_at = min(lock_times) if lock_times else None
        prediction.last_lock_at = max(lock_times) if lock_times else None

        if self._has_finalized_result_for_profile(
            context.race.id,
            prediction.user_profile_id,
        ):
            status = FantasyEntryStatus.FINALIZED
        elif all_locked and all_resolved:
            status = FantasyEntryStatus.SCORED
        elif all_locked and has_any_resolution:
            status = FantasyEntryStatus.SCORING
        elif all_locked:
            status = FantasyEntryStatus.LOCKED
        elif answered_question_count == 0:
            status = FantasyEntryStatus.DRAFT
        elif answered_question_count == len(questions):
            status = FantasyEntryStatus.COMPLETE
        else:
            status = FantasyEntryStatus.IN_PROGRESS

        prediction.status = status
        now = self._now()

        if (
            answered_question_count == len(questions)
            and questions
            and prediction.completed_at is None
        ):
            prediction.completed_at = now

        if status in {
            FantasyEntryStatus.SCORED,
            FantasyEntryStatus.FINALIZED,
        }:
            prediction.scored_at = now

        if status == FantasyEntryStatus.FINALIZED:
            prediction.finalized_at = now

    def _sync_entries_for_race(
        self,
        context: FantasyRaceContext,
        questions: list[FantasyQuestionDefinition],
    ) -> None:
        predictions = self.db.scalars(
            select(FantasyPrediction).where(
                FantasyPrediction.race_session_id == context.race.id
            )
        ).all()

        for prediction in predictions:
            self._sync_entry_lifecycle(context, prediction, questions)

    def _next_deadline(
        self,
        questions: list[FantasyQuestionDefinition],
    ) -> datetime | None:
        deadlines = sorted(
            self._as_utc(question.locks_at)
            for question in questions
            if question.locks_at is not None
            and self._now() < self._as_utc(question.locks_at)
        )
        return deadlines[0] if deadlines else None

    def _race_summary(
        self,
        context: FantasyRaceContext,
    ) -> FantasyRaceSummaryResponse:
        return FantasyRaceSummaryResponse(
            race_session_id=context.race.id,
            meeting_id=context.meeting.id,
            meeting_name=context.meeting.name,
            year=context.meeting.year,
            session_name=context.race.name,
            started_at=context.race.started_at,
        )

    def _candidate_options(
        self,
        context: FantasyRaceContext,
    ) -> tuple[
        list[FantasyDriverOptionResponse],
        list[FantasyTeamOptionResponse],
    ]:
        session_ids = [
            session.id for session in context.sessions
        ]
        participant_rows = self.db.execute(
            select(
                SessionResult.driver_id,
                SessionResult.team_id,
            ).where(SessionResult.race_session_id.in_(session_ids))
        ).all()

        driver_ids = {
            driver_id for driver_id, _ in participant_rows
        }
        team_ids = {
            team_id
            for _, team_id in participant_rows
            if team_id is not None
        }
        team_by_driver = {
            driver_id: team_id
            for driver_id, team_id in participant_rows
            if team_id is not None
        }

        driver_statement = select(Driver)

        if driver_ids:
            driver_statement = driver_statement.where(
                Driver.id.in_(driver_ids)
            )
        else:
            driver_statement = driver_statement.where(
                Driver.source == context.meeting.source
            )

        team_statement = select(Team)

        if team_ids:
            team_statement = team_statement.where(
                Team.id.in_(team_ids)
            )
        else:
            team_statement = team_statement.where(
                Team.source == context.meeting.source
            )

        teams = self.db.scalars(team_statement).all()
        team_by_id = {team.id: team for team in teams}
        drivers = self.db.scalars(driver_statement).all()

        driver_options = [
            FantasyDriverOptionResponse(
                id=driver.id,
                driver_number=driver.driver_number,
                abbreviation=driver.abbreviation,
                display_name=self._driver_display_name(driver),
                team_id=team_by_driver.get(driver.id),
                team_name=(
                    team_by_id[team_by_driver[driver.id]].name
                    if team_by_driver.get(driver.id) in team_by_id
                    else None
                ),
            )
            for driver in drivers
        ]
        driver_options.sort(
            key=lambda driver: driver.display_name.casefold()
        )

        team_options = [
            FantasyTeamOptionResponse(
                id=team.id,
                name=team.name,
                colour=team.colour,
            )
            for team in teams
        ]
        team_options.sort(key=lambda team: team.name.casefold())

        return driver_options, team_options

    def _candidate_id_sets(
        self,
        context: FantasyRaceContext,
    ) -> tuple[set[UUID], set[UUID]]:
        drivers, teams = self._candidate_options(context)
        return (
            {driver.id for driver in drivers},
            {team.id for team in teams},
        )

    def _validate_prediction_answer(
        self,
        question: FantasyQuestionDefinition,
        answer: FantasyPredictionAnswerInput,
        candidate_driver_ids: set[UUID],
        candidate_team_ids: set[UUID],
    ) -> None:
        if question.answer_kind == "DRIVER":
            if (
                answer.driver_id is None
                or answer.team_id is not None
                or answer.driver_ids is not None
            ):
                raise FantasyInvalidRequestError(
                    f"{question.label} requires one driver."
                )

            if answer.driver_id not in candidate_driver_ids:
                raise FantasyInvalidRequestError(
                    "Selected driver is not available for this weekend."
                )

        elif question.answer_kind == "TEAM":
            if (
                answer.team_id is None
                or answer.driver_id is not None
                or answer.driver_ids is not None
            ):
                raise FantasyInvalidRequestError(
                    f"{question.label} requires one team."
                )

            if answer.team_id not in candidate_team_ids:
                raise FantasyInvalidRequestError(
                    "Selected team is not available for this weekend."
                )

        else:
            if (
                answer.driver_id is not None
                or answer.team_id is not None
                or answer.driver_ids is None
                or not answer.driver_ids
                or len(answer.driver_ids) > question.selection_limit
            ):
                raise FantasyInvalidRequestError(
                    f"{question.label} allows up to "
                    f"{question.selection_limit} drivers."
                )

            if not set(answer.driver_ids).issubset(
                candidate_driver_ids
            ):
                raise FantasyInvalidRequestError(
                    "Selected driver is not available for this weekend."
                )

    def _validate_podium_choices(
        self,
        picks_by_key: dict[str, FantasyPredictionPick],
    ) -> None:
        selected_drivers = [
            picks_by_key[key].driver_id
            for key in self._PODIUM_KEYS
            if key in picks_by_key
            and picks_by_key[key].driver_id is not None
        ]

        if len(selected_drivers) != len(set(selected_drivers)):
            raise FantasyInvalidRequestError(
                "The P1, P2, and P3 picks must be different drivers."
            )

    def _automatic_resolution(
        self,
        context: FantasyRaceContext,
        question: FantasyQuestionDefinition,
    ) -> DerivedResolution | None:
        if question.category == "PRACTICE":
            result = self.db.scalar(
                select(SessionResult)
                .where(
                    SessionResult.race_session_id
                    == question.target_session_id,
                    SessionResult.position == 1,
                )
                .order_by(SessionResult.driver_id)
            )

            if result is None:
                return None

            return DerivedResolution(
                driver_id=result.driver_id,
                team_id=None,
                driver_ids=[],
                source_reference=(
                    f"session_results:{question.target_session_id}"
                ),
                data_quality_flags=["PUBLIC_TIMING_RESULT"],
            )

        if question.qualifying_column is not None:
            time_column = getattr(
                SessionResult,
                question.qualifying_column,
            )
            result = self.db.scalar(
                select(SessionResult)
                .where(
                    SessionResult.race_session_id
                    == question.target_session_id,
                    time_column.is_not(None),
                )
                .order_by(time_column, SessionResult.driver_id)
            )

            if result is None:
                return None

            return DerivedResolution(
                driver_id=result.driver_id,
                team_id=None,
                driver_ids=[],
                source_reference=(
                    f"session_results:{question.target_session_id}"
                ),
                data_quality_flags=["PUBLIC_QUALIFYING_TIMING"],
            )

        if question.race_position is not None:
            result = self.db.scalar(
                select(SessionResult)
                .where(
                    SessionResult.race_session_id == context.race.id,
                    SessionResult.position == question.race_position,
                )
                .order_by(SessionResult.driver_id)
            )

            if result is None:
                return None

            return DerivedResolution(
                driver_id=result.driver_id,
                team_id=None,
                driver_ids=[],
                source_reference=(
                    f"session_results:{context.race.id}"
                ),
                data_quality_flags=["PUBLIC_RACE_CLASSIFICATION"],
            )

        if question.key == "RACE_WINNING_TEAM":
            result = self.db.scalar(
                select(SessionResult)
                .where(
                    SessionResult.race_session_id == context.race.id,
                    SessionResult.position == 1,
                    SessionResult.team_id.is_not(None),
                )
            )

            if result is None:
                return None

            return DerivedResolution(
                driver_id=None,
                team_id=result.team_id,
                driver_ids=[],
                source_reference=(
                    f"session_results:{context.race.id}"
                ),
                data_quality_flags=["PUBLIC_RACE_CLASSIFICATION"],
            )

        if question.key == "RACE_FASTEST_LAP":
            lap = self.db.scalar(
                self._valid_laps(context.race.id).order_by(
                    Lap.lap_time_ms,
                    Lap.driver_id,
                )
            )

            if lap is None:
                return None

            return DerivedResolution(
                driver_id=lap.driver_id,
                team_id=None,
                driver_ids=[],
                source_reference=f"laps:{context.race.id}:fastest_lap",
                data_quality_flags=["PUBLIC_TIMING_LAPS"],
            )

        if question.key == "RACE_TOP_SPEED":
            lap = self.db.scalar(
                self._valid_laps(context.race.id)
                .where(Lap.speed_st.is_not(None))
                .order_by(
                    Lap.speed_st.desc(),
                    Lap.driver_id,
                )
            )

            if lap is None:
                return None

            return DerivedResolution(
                driver_id=lap.driver_id,
                team_id=None,
                driver_ids=[],
                source_reference=f"laps:{context.race.id}:speed_trap",
                data_quality_flags=["SPEED_TRAP_PROXY"],
            )

        # DNF classifications are deliberately editor-verified in V1.
        return None

    def _valid_laps(self, race_session_id: UUID):
        return select(Lap).where(
            Lap.race_session_id == race_session_id,
            Lap.lap_time_ms.is_not(None),
            Lap.is_accurate.is_(True),
            or_(
                Lap.deleted.is_(False),
                Lap.deleted.is_(None),
            ),
            or_(
                Lap.fastf1_generated.is_(False),
                Lap.fastf1_generated.is_(None),
            ),
        )

    def _upsert_resolution(
        self,
        *,
        context: FantasyRaceContext,
        question: FantasyQuestionDefinition,
        status: FantasyQuestionResolutionStatus,
        resolution_source: str,
        driver_id: UUID | None,
        team_id: UUID | None,
        driver_ids: list[str],
        source_reference: str | None,
        data_quality_flags: list[str],
        resolved_by_profile_id: UUID | None,
    ) -> FantasyQuestionResolution:
        resolution = self.db.scalar(
            select(FantasyQuestionResolution).where(
                FantasyQuestionResolution.race_session_id
                == context.race.id,
                FantasyQuestionResolution.question_key == question.key,
            )
        )

        if resolution is None:
            resolution = FantasyQuestionResolution(
                race_session_id=context.race.id,
                target_session_id=question.target_session_id,
                question_key=question.key,
            )
            self.db.add(resolution)

        resolution.target_session_id = question.target_session_id
        resolution.status = status
        resolution.resolution_source = resolution_source
        resolution.actual_driver_id = driver_id
        resolution.actual_team_id = team_id
        resolution.actual_driver_ids = list(driver_ids)
        resolution.source_reference = source_reference
        resolution.data_quality_flags = list(data_quality_flags)
        resolution.resolved_by_profile_id = resolved_by_profile_id
        resolution.resolved_at = self._now()

        if question.snapshot_id is not None:
            snapshot = self.db.get(
                FantasyWeekendQuestion,
                question.snapshot_id,
            )
            if snapshot is not None:
                snapshot.status = {
                    FantasyQuestionResolutionStatus.RESOLVED: (
                        FantasyWeekendQuestionStatus.RESOLVED
                    ),
                    FantasyQuestionResolutionStatus.NOT_SCORED: (
                        FantasyWeekendQuestionStatus.NOT_SCORED
                    ),
                }.get(
                    status,
                    self._snapshot_status_for_lock(question.locks_at),
                )

        return resolution

    def _validate_manual_resolution(
        self,
        question: FantasyQuestionDefinition,
        payload: FantasyQuestionResolutionRequest,
        candidate_driver_ids: set[UUID],
        candidate_team_ids: set[UUID],
    ) -> tuple[UUID | None, UUID | None, list[str]]:
        if question.answer_kind == "DRIVER":
            if (
                payload.actual_driver_id is None
                or payload.actual_team_id is not None
                or payload.actual_driver_ids is not None
            ):
                raise FantasyInvalidRequestError(
                    f"{question.label} requires one driver outcome."
                )

            if payload.actual_driver_id not in candidate_driver_ids:
                raise FantasyInvalidRequestError(
                    "Resolved driver is not available for this weekend."
                )

            return payload.actual_driver_id, None, []

        if question.answer_kind == "TEAM":
            if (
                payload.actual_team_id is None
                or payload.actual_driver_id is not None
                or payload.actual_driver_ids is not None
            ):
                raise FantasyInvalidRequestError(
                    f"{question.label} requires one team outcome."
                )

            if payload.actual_team_id not in candidate_team_ids:
                raise FantasyInvalidRequestError(
                    "Resolved team is not available for this weekend."
                )

            return None, payload.actual_team_id, []

        if (
            payload.actual_driver_id is not None
            or payload.actual_team_id is not None
            or payload.actual_driver_ids is None
        ):
            raise FantasyInvalidRequestError(
                f"{question.label} requires a driver list outcome."
            )

        if not set(payload.actual_driver_ids).issubset(
            candidate_driver_ids
        ):
            raise FantasyInvalidRequestError(
                "Resolved driver is not available for this weekend."
            )

        return (
            None,
            None,
            [
                str(driver_id)
                for driver_id in payload.actual_driver_ids
            ],
        )

    def _validate_manual_podium_resolution(
        self,
        race_session_id: UUID,
        question_key: str,
        driver_id: UUID,
    ) -> None:
        existing = self._resolutions_for_race(race_session_id)

        for podium_key in self._PODIUM_KEYS:
            resolution = existing.get(podium_key)

            if (
                podium_key != question_key
                and resolution is not None
                and resolution.status
                == FantasyQuestionResolutionStatus.RESOLVED
                and resolution.actual_driver_id == driver_id
            ):
                raise FantasyInvalidRequestError(
                    "Resolved podium positions must use different drivers."
                )

    def _score_question(
        self,
        context: FantasyRaceContext,
        question: FantasyQuestionDefinition,
        resolution: FantasyQuestionResolution,
    ) -> int:
        prediction_ids = self.db.scalars(
            select(FantasyPrediction.id).where(
                FantasyPrediction.race_session_id == context.race.id
            )
        ).all()

        if not prediction_ids:
            return 0

        picks = self.db.scalars(
            select(FantasyPredictionPick).where(
                FantasyPredictionPick.prediction_id.in_(prediction_ids),
                FantasyPredictionPick.question_key == question.key,
            )
        ).all()
        scores_by_pick = self._scores_for_picks(picks)

        for pick in picks:
            status, points, is_exact, breakdown = self._score_pick(
                context,
                question,
                pick,
                resolution,
            )
            score = scores_by_pick.get(pick.id)

            if score is None:
                score = FantasyPredictionPickScore(
                    pick_id=pick.id,
                )
                self.db.add(score)

            score.status = status
            score.points_awarded = points
            score.max_points = question.max_points
            score.is_exact = is_exact
            score.breakdown = breakdown
            score.scored_at = self._now()

        return len(picks)

    def _score_pick(
        self,
        context: FantasyRaceContext,
        question: FantasyQuestionDefinition,
        pick: FantasyPredictionPick,
        resolution: FantasyQuestionResolution,
    ) -> tuple[
        FantasyPickScoreStatus,
        int,
        bool,
        dict[str, object],
    ]:
        if resolution.status == FantasyQuestionResolutionStatus.NOT_SCORED:
            return (
                FantasyPickScoreStatus.NOT_SCORED,
                0,
                False,
                {
                    "reason": "Question outcome was not reliably "
                    "verifiable.",
                },
            )

        if resolution.status != FantasyQuestionResolutionStatus.RESOLVED:
            return (
                FantasyPickScoreStatus.PENDING,
                0,
                False,
                {},
            )

        if question.answer_kind == "DRIVER":
            is_exact = pick.driver_id == resolution.actual_driver_id

            if is_exact:
                return (
                    FantasyPickScoreStatus.SCORED,
                    question.max_points,
                    True,
                    {
                        "actual_driver_id": str(
                            resolution.actual_driver_id
                        ),
                    },
                )

            podium_driver_ids = self._podium_driver_ids(
                context.race.id
            )

            if (
                question.race_position is not None
                and pick.driver_id in podium_driver_ids
            ):
                return (
                    FantasyPickScoreStatus.SCORED,
                    min(3, question.max_points),
                    False,
                    {
                        "actual_driver_id": str(
                            resolution.actual_driver_id
                        ),
                        "podium_bonus": True,
                    },
                )

            return (
                FantasyPickScoreStatus.SCORED,
                0,
                False,
                {
                    "actual_driver_id": str(
                        resolution.actual_driver_id
                    )
                    if resolution.actual_driver_id
                    else None,
                },
            )

        if question.answer_kind == "TEAM":
            is_exact = pick.team_id == resolution.actual_team_id

            return (
                FantasyPickScoreStatus.SCORED,
                question.max_points if is_exact else 0,
                is_exact,
                {
                    "actual_team_id": str(
                        resolution.actual_team_id
                    )
                    if resolution.actual_team_id
                    else None,
                },
            )

        selected_driver_ids = set(pick.driver_ids)
        actual_driver_ids = set(resolution.actual_driver_ids)
        correct_driver_ids = selected_driver_ids.intersection(
            actual_driver_ids
        )
        points = min(
            len(correct_driver_ids) * 4,
            question.max_points,
        )

        return (
            FantasyPickScoreStatus.SCORED,
            points,
            bool(selected_driver_ids)
            and selected_driver_ids == actual_driver_ids,
            {
                "correct_driver_ids": sorted(correct_driver_ids),
                "actual_driver_ids": sorted(actual_driver_ids),
            },
        )

    def _podium_driver_ids(
        self,
        race_session_id: UUID,
    ) -> set[UUID]:
        resolutions = self._resolutions_for_race(race_session_id)

        return {
            resolution.actual_driver_id
            for key in self._PODIUM_KEYS
            if (resolution := resolutions.get(key)) is not None
            and resolution.status
            == FantasyQuestionResolutionStatus.RESOLVED
            and resolution.actual_driver_id is not None
        }

    def _prediction_for(
        self,
        user_profile_id: UUID,
        race_session_id: UUID,
    ) -> FantasyPrediction | None:
        return self.db.scalar(
            select(FantasyPrediction).where(
                FantasyPrediction.user_profile_id == user_profile_id,
                FantasyPrediction.race_session_id == race_session_id,
            )
        )

    def _scores_for_picks(
        self,
        picks: list[FantasyPredictionPick],
    ) -> dict[UUID, FantasyPredictionPickScore]:
        if not picks:
            return {}

        scores = self.db.scalars(
            select(FantasyPredictionPickScore).where(
                FantasyPredictionPickScore.pick_id.in_(
                    [pick.id for pick in picks]
                )
            )
        ).all()

        return {score.pick_id: score for score in scores}

    def _resolutions_for_race(
        self,
        race_session_id: UUID,
    ) -> dict[str, FantasyQuestionResolution]:
        resolutions = self.db.scalars(
            select(FantasyQuestionResolution).where(
                FantasyQuestionResolution.race_session_id
                == race_session_id
            )
        ).all()

        return {
            resolution.question_key: resolution
            for resolution in resolutions
        }

    def _answer_response(
        self,
        pick: FantasyPredictionPick | None,
        scores_by_pick: dict[UUID, FantasyPredictionPickScore],
    ) -> FantasyPredictionAnswerResponse | None:
        if pick is None:
            return None

        score = scores_by_pick.get(pick.id)

        return FantasyPredictionAnswerResponse(
            question_key=pick.question_key,
            driver_id=pick.driver_id,
            team_id=pick.team_id,
            driver_ids=pick.driver_ids,
            updated_at=pick.updated_at,
            score=(
                FantasyPickScoreResponse(
                    status=score.status.value,
                    points_awarded=score.points_awarded,
                    max_points=score.max_points,
                    is_exact=score.is_exact,
                    breakdown=score.breakdown,
                    scored_at=score.scored_at,
                )
                if score is not None
                else None
            ),
        )

    def _question_response(
        self,
        question: FantasyQuestionDefinition,
        pick: FantasyPredictionPick | None,
        scores_by_pick: dict[UUID, FantasyPredictionPickScore],
        resolution: FantasyQuestionResolution | None,
    ) -> FantasyQuestionResponse:
        status = self._question_status(question, resolution)

        return FantasyQuestionResponse(
            key=question.key,
            label=question.label,
            category=question.category,
            answer_kind=question.answer_kind,
            target_session_id=question.target_session_id,
            target_session_name=question.target_session_name,
            locks_at=question.locks_at,
            state=self._question_state(question),
            max_points=question.max_points,
            selection_limit=question.selection_limit,
            answer=self._answer_response(pick, scores_by_pick),
            resolution=self._resolution_response(resolution),
            status=status.value,
            is_answered=pick is not None,
        )

    def _question_status(
        self,
        question: FantasyQuestionDefinition,
        resolution: FantasyQuestionResolution | None,
    ) -> FantasyWeekendQuestionStatus:
        if resolution is not None:
            if resolution.status == FantasyQuestionResolutionStatus.RESOLVED:
                return FantasyWeekendQuestionStatus.RESOLVED

            if resolution.status == FantasyQuestionResolutionStatus.NOT_SCORED:
                return FantasyWeekendQuestionStatus.NOT_SCORED

        return self._snapshot_status_for_lock(question.locks_at)

    def _resolution_response(
        self,
        resolution: FantasyQuestionResolution | None,
    ) -> FantasyQuestionResolutionResponse | None:
        if resolution is None:
            return None

        return FantasyQuestionResolutionResponse(
            question_key=resolution.question_key,
            status=resolution.status.value,
            resolution_source=resolution.resolution_source,
            actual_driver_id=resolution.actual_driver_id,
            actual_team_id=resolution.actual_team_id,
            actual_driver_ids=resolution.actual_driver_ids,
            source_reference=resolution.source_reference,
            data_quality_flags=resolution.data_quality_flags,
            resolved_at=resolution.resolved_at,
        )

    def _finalize_groups(
        self,
        context: FantasyRaceContext,
        questions: list[FantasyQuestionDefinition],
    ) -> FantasyFinalizeResponse:
        first_lock_at = self._first_question_lock(questions)

        if first_lock_at is None:
            raise FantasyInvalidRequestError(
                "Fantasy questions need a scheduled lock time before "
                "group results can be finalized."
            )

        groups = self.db.scalars(select(FantasyGroup)).all()
        result_count = 0

        for group in groups:
            eligible_members = self._eligible_group_members(
                group,
                context,
                first_lock_at,
            )
            ranked_members = self._rank_group_members(
                context.race.id,
                eligible_members,
            )
            existing_results = self.db.scalars(
                select(FantasyGroupWeekendResult).where(
                    FantasyGroupWeekendResult.group_id == group.id,
                    FantasyGroupWeekendResult.race_session_id
                    == context.race.id,
                )
            ).all()
            results_by_profile_id = {
                result.user_profile_id: result
                for result in existing_results
            }
            eligible_profile_ids = {
                member.user_profile_id for member in eligible_members
            }

            for result in existing_results:
                if result.user_profile_id not in eligible_profile_ids:
                    self.db.delete(result)

            for (
                rank,
                profile_id,
                points,
                podium_hits,
                qualifying_hits,
                scored_count,
            ) in ranked_members:
                result = results_by_profile_id.get(profile_id)

                if result is None:
                    result = FantasyGroupWeekendResult(
                        group_id=group.id,
                        race_session_id=context.race.id,
                        user_profile_id=profile_id,
                        rank=rank,
                        points=points,
                        exact_podium_hits=podium_hits,
                        exact_qualifying_hits=qualifying_hits,
                        scored_question_count=scored_count,
                        finalized_at=self._now(),
                    )
                    self.db.add(result)
                else:
                    result.rank = rank
                    result.points = points
                    result.exact_podium_hits = podium_hits
                    result.exact_qualifying_hits = qualifying_hits
                    result.scored_question_count = scored_count
                    result.finalized_at = self._now()

                result_count += 1

        self.db.flush()
        return FantasyFinalizeResponse(
            race_session_id=context.race.id,
            finalized_group_count=len(groups),
            created_or_updated_result_count=result_count,
        )

    def _eligible_group_members(
        self,
        group: FantasyGroup,
        context: FantasyRaceContext,
        first_lock_at: datetime,
    ) -> list[FantasyGroupWeekendEligibility]:
        existing = self.db.scalars(
            select(FantasyGroupWeekendEligibility).where(
                FantasyGroupWeekendEligibility.group_id == group.id,
                FantasyGroupWeekendEligibility.race_session_id
                == context.race.id,
            )
        ).all()
        existing_profile_ids = {
            eligibility.user_profile_id for eligibility in existing
        }
        members = self.db.scalars(
            select(FantasyGroupMember).where(
                FantasyGroupMember.group_id == group.id
            )
        ).all()

        for member in members:
            if (
                member.user_profile_id not in existing_profile_ids
                and self._joined_by_first_question_lock(
                    member.created_at,
                    first_lock_at,
                )
            ):
                self.db.add(
                    FantasyGroupWeekendEligibility(
                        group_id=group.id,
                        race_session_id=context.race.id,
                        user_profile_id=member.user_profile_id,
                        membership_joined_at=member.created_at,
                        first_question_locks_at=first_lock_at,
                        locked_at=self._now(),
                    )
                )

        self.db.flush()
        return self.db.scalars(
            select(FantasyGroupWeekendEligibility)
            .where(
                FantasyGroupWeekendEligibility.group_id == group.id,
                FantasyGroupWeekendEligibility.race_session_id
                == context.race.id,
            )
            .order_by(FantasyGroupWeekendEligibility.created_at)
        ).all()

    def _materialize_locked_group_eligibilities(
        self,
        group: FantasyGroup,
    ) -> None:
        race_sessions = self.db.scalars(select(RaceSession)).all()

        for race_session in race_sessions:
            if not self._is_race_session(race_session):
                continue

            context = self._load_race_context(race_session.id)
            first_lock_at = self._first_question_lock(
                self._questions_for_context(context)
            )

            if (
                first_lock_at is not None
                and self._now() >= self._as_utc(first_lock_at)
            ):
                self._eligible_group_members(
                    group,
                    context,
                    first_lock_at,
                )

    @staticmethod
    def _first_question_lock(
        questions: list[FantasyQuestionDefinition],
    ) -> datetime | None:
        lock_times = [
            question.locks_at
            for question in questions
            if question.locks_at is not None
        ]
        return min(lock_times) if lock_times else None

    def _joined_by_first_question_lock(
        self,
        joined_at: datetime,
        first_question_locks_at: datetime,
    ) -> bool:
        return self._as_utc(joined_at) <= self._as_utc(
            first_question_locks_at
        )

    def _rank_group_members(
        self,
        race_session_id: UUID,
        members: list[
            FantasyGroupMember | FantasyGroupWeekendEligibility
        ],
    ) -> list[tuple[int, UUID, int, int, int, int]]:
        values: list[tuple[UUID, int, int, int, int]] = []

        for member in members:
            (
                points,
                podium_hits,
                qualifying_hits,
                scored_count,
            ) = self._race_score_stats(
                member.user_profile_id,
                race_session_id,
            )
            values.append(
                (
                    member.user_profile_id,
                    points,
                    podium_hits,
                    qualifying_hits,
                    scored_count,
                )
            )

        values.sort(
            key=lambda item: (
                -item[1],
                -item[2],
                -item[3],
                str(item[0]),
            )
        )

        ranked: list[tuple[int, UUID, int, int, int, int]] = []
        last_metrics: tuple[int, int, int] | None = None
        current_rank = 0

        for index, value in enumerate(values, start=1):
            profile_id, points, podium_hits, qualifying_hits, scored = value
            metrics = (points, podium_hits, qualifying_hits)

            if metrics != last_metrics:
                current_rank = index
                last_metrics = metrics

            ranked.append(
                (
                    current_rank,
                    profile_id,
                    points,
                    podium_hits,
                    qualifying_hits,
                    scored,
                )
            )

        return ranked

    def _race_score_stats(
        self,
        profile_id: UUID,
        race_session_id: UUID,
    ) -> tuple[int, int, int, int]:
        rows = self.db.execute(
            select(
                FantasyPredictionPick.question_key,
                FantasyPredictionPickScore.points_awarded,
                FantasyPredictionPickScore.is_exact,
            )
            .select_from(FantasyPredictionPickScore)
            .join(
                FantasyPredictionPick,
                FantasyPredictionPick.id
                == FantasyPredictionPickScore.pick_id,
            )
            .join(
                FantasyPrediction,
                FantasyPrediction.id
                == FantasyPredictionPick.prediction_id,
            )
            .where(
                FantasyPrediction.user_profile_id == profile_id,
                FantasyPrediction.race_session_id == race_session_id,
                FantasyPredictionPickScore.status
                == FantasyPickScoreStatus.SCORED,
            )
        ).all()

        points = sum(int(points_awarded) for _, points_awarded, _ in rows)
        podium_hits = sum(
            1
            for question_key, _, is_exact in rows
            if is_exact and question_key in self._PODIUM_KEYS
        )
        qualifying_hits = sum(
            1
            for question_key, _, is_exact in rows
            if is_exact and question_key in self._QUALIFYING_KEYS
        )

        return points, podium_hits, qualifying_hits, len(rows)

    def _global_score_stats(
        self,
        *,
        year: int | None,
        race_session_id: UUID | None,
    ) -> dict[UUID, list[object]]:
        statement = (
            select(
                UserProfile.id,
                UserProfile.display_name,
                FantasyPredictionPick.question_key,
                FantasyPredictionPickScore.points_awarded,
                FantasyPredictionPickScore.is_exact,
            )
            .select_from(FantasyPredictionPickScore)
            .join(
                FantasyPredictionPick,
                FantasyPredictionPick.id
                == FantasyPredictionPickScore.pick_id,
            )
            .join(
                FantasyPrediction,
                FantasyPrediction.id
                == FantasyPredictionPick.prediction_id,
            )
            .join(
                UserProfile,
                UserProfile.id == FantasyPrediction.user_profile_id,
            )
            .join(
                RaceSession,
                RaceSession.id == FantasyPrediction.race_session_id,
            )
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(
                FantasyPredictionPickScore.status
                == FantasyPickScoreStatus.SCORED
            )
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        if race_session_id is not None:
            statement = statement.where(
                FantasyPrediction.race_session_id == race_session_id
            )

        stats: dict[UUID, list[object]] = {}

        for (
            profile_id,
            display_name,
            question_key,
            points_awarded,
            is_exact,
        ) in self.db.execute(statement).all():
            row = stats.setdefault(
                profile_id,
                [display_name or "RacePulse fan", 0, 0, 0],
            )
            row[1] = int(row[1]) + int(points_awarded)

            if is_exact and question_key in self._PODIUM_KEYS:
                row[2] = int(row[2]) + 1

            if is_exact and question_key in self._QUALIFYING_KEYS:
                row[3] = int(row[3]) + 1

        return stats

    def _personal_global_rank(
        self,
        user_profile_id: UUID,
        *,
        year: int | None,
        race_session_id: UUID | None,
    ) -> int | None:
        stats = self._global_score_stats(
            year=year,
            race_session_id=race_session_id,
        )

        if user_profile_id not in stats:
            if not self._profile_has_prediction_in_scope(
                user_profile_id,
                year=year,
                race_session_id=race_session_id,
            ):
                return None

            profile = self.db.get(UserProfile, user_profile_id)
            stats[user_profile_id] = [
                self._profile_display_name(profile),
                0,
                0,
                0,
            ]

        return next(
            (
                row.rank
                for row in self._leaderboard_rows(stats, user_profile_id)
                if row.is_current_user
            ),
            None,
        )

    def _profile_has_prediction_in_scope(
        self,
        user_profile_id: UUID,
        *,
        year: int | None,
        race_session_id: UUID | None,
    ) -> bool:
        statement = (
            select(FantasyPrediction.id)
            .join(
                RaceSession,
                RaceSession.id == FantasyPrediction.race_session_id,
            )
            .join(Meeting, Meeting.id == RaceSession.meeting_id)
            .where(FantasyPrediction.user_profile_id == user_profile_id)
            .limit(1)
        )

        if year is not None:
            statement = statement.where(Meeting.year == year)

        if race_session_id is not None:
            statement = statement.where(
                FantasyPrediction.race_session_id == race_session_id
            )

        return self.db.scalar(statement) is not None

    def _has_finalized_results(self, race_session_id: UUID) -> bool:
        return self.db.scalar(
            select(FantasyGroupWeekendResult.id)
            .where(
                FantasyGroupWeekendResult.race_session_id == race_session_id
            )
            .limit(1)
        ) is not None

    def _has_finalized_result_for_profile(
        self,
        race_session_id: UUID,
        user_profile_id: UUID,
    ) -> bool:
        return self.db.scalar(
            select(FantasyGroupWeekendResult.id)
            .where(
                FantasyGroupWeekendResult.race_session_id == race_session_id,
                FantasyGroupWeekendResult.user_profile_id == user_profile_id,
            )
            .limit(1)
        ) is not None

    def _leaderboard_rows(
        self,
        stats: dict[UUID, list[object]],
        current_profile_id: UUID,
    ) -> list[FantasyLeaderboardRowResponse]:
        values = [
            (
                profile_id,
                str(value[0]),
                int(value[1]),
                int(value[2]),
                int(value[3]),
            )
            for profile_id, value in stats.items()
        ]
        values.sort(
            key=lambda value: (
                -value[2],
                -value[3],
                -value[4],
                value[1].casefold(),
            )
        )

        rows: list[FantasyLeaderboardRowResponse] = []
        last_metrics: tuple[int, int, int] | None = None
        current_rank = 0

        for index, value in enumerate(values, start=1):
            (
                profile_id,
                display_name,
                points,
                podium_hits,
                qualifying_hits,
            ) = value
            metrics = (points, podium_hits, qualifying_hits)

            if metrics != last_metrics:
                current_rank = index
                last_metrics = metrics

            rows.append(
                FantasyLeaderboardRowResponse(
                    rank=current_rank,
                    display_name=display_name,
                    points=points,
                    exact_podium_hits=podium_hits,
                    exact_qualifying_hits=qualifying_hits,
                    is_current_user=(
                        profile_id == current_profile_id
                    ),
                )
            )

        return rows

    def _get_group(self, group_id: UUID) -> FantasyGroup:
        group = self.db.get(FantasyGroup, group_id)

        if group is None:
            raise FantasyGroupNotFoundError(
                "Fantasy group was not found."
            )

        return group

    def _require_group_member(
        self,
        group: FantasyGroup,
        profile_id: UUID,
    ) -> FantasyGroupMember:
        member = self.db.scalar(
            select(FantasyGroupMember).where(
                FantasyGroupMember.group_id == group.id,
                FantasyGroupMember.user_profile_id == profile_id,
            )
        )

        if member is None:
            raise FantasyGroupPermissionError(
                "You are not a member of this Fantasy group."
            )

        return member

    def _require_group_owner(
        self,
        group: FantasyGroup,
        profile_id: UUID,
    ) -> None:
        if group.owner_profile_id != profile_id:
            raise FantasyGroupPermissionError(
                "Only the group owner can do that."
            )

    def _group_summary(
        self,
        group: FantasyGroup,
        member: FantasyGroupMember,
        profile_id: UUID,
    ) -> FantasyGroupSummaryResponse:
        owner = self.db.get(UserProfile, group.owner_profile_id)
        member_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(FantasyGroupMember)
                .where(FantasyGroupMember.group_id == group.id)
            )
            or 0
        )

        return FantasyGroupSummaryResponse(
            id=group.id,
            name=group.name,
            owner_display_name=self._profile_display_name(owner),
            member_count=member_count,
            max_members=group.max_members,
            is_owner=group.owner_profile_id == profile_id,
            joined_at=member.created_at,
        )

    def _group_detail(
        self,
        group: FantasyGroup,
        profile_id: UUID,
    ) -> FantasyGroupDetailResponse:
        members = self.db.scalars(
            select(FantasyGroupMember)
            .where(FantasyGroupMember.group_id == group.id)
            .order_by(FantasyGroupMember.created_at)
        ).all()
        profile_ids = [member.user_profile_id for member in members]
        profiles = self.db.scalars(
            select(UserProfile).where(
                UserProfile.id.in_(profile_ids)
            )
        ).all()
        profiles_by_id = {
            profile.id: profile for profile in profiles
        }

        member_responses = [
            FantasyGroupMemberResponse(
                profile_id=member.user_profile_id,
                display_name=self._profile_display_name(
                    profiles_by_id.get(member.user_profile_id)
                ),
                is_owner=(
                    member.user_profile_id == group.owner_profile_id
                ),
                joined_at=member.created_at,
            )
            for member in members
        ]
        member_responses.sort(
            key=lambda member: (
                not member.is_owner,
                member.display_name.casefold(),
            )
        )

        owner = self.db.get(UserProfile, group.owner_profile_id)

        return FantasyGroupDetailResponse(
            id=group.id,
            name=group.name,
            owner_display_name=self._profile_display_name(owner),
            member_count=len(member_responses),
            max_members=group.max_members,
            invite_code=(
                group.invite_code
                if group.owner_profile_id == profile_id
                else None
            ),
            members=member_responses,
        )

    def _new_invite_code(self) -> str:
        for _ in range(20):
            code = (
                token_urlsafe(7)
                .replace("-", "")
                .replace("_", "")
                .upper()
            )

            exists = self.db.scalar(
                select(FantasyGroup.id).where(
                    FantasyGroup.invite_code == code
                )
            )

            if exists is None:
                return code

        raise RuntimeError("Could not create a unique invite code.")

    def _delete_pick_and_score(
        self,
        pick: FantasyPredictionPick,
    ) -> None:
        self._delete_pick_score(pick)
        self.db.delete(pick)

    def _delete_pick_score(
        self,
        pick: FantasyPredictionPick,
    ) -> None:
        score = self.db.scalar(
            select(FantasyPredictionPickScore).where(
                FantasyPredictionPickScore.pick_id == pick.id
            )
        )

        if score is not None:
            self.db.delete(score)

    def _question_state(
        self,
        question: FantasyQuestionDefinition,
    ) -> str:
        if question.locks_at is None:
            return "UNAVAILABLE"

        if self._question_is_locked(question):
            return "LOCKED"

        return "OPEN"

    def _question_is_open(
        self,
        question: FantasyQuestionDefinition,
    ) -> bool:
        return (
            question.locks_at is not None
            and not self._question_is_locked(question)
        )

    def _question_is_locked(
        self,
        question: FantasyQuestionDefinition,
    ) -> bool:
        return (
            question.locks_at is not None
            and self._now() >= self._as_utc(question.locks_at)
        )

    def _joined_before_race(
        self,
        joined_at: datetime,
        race_started_at: datetime,
    ) -> bool:
        return (
            self._as_utc(joined_at)
            <= self._as_utc(race_started_at)
        )

    def _now(self) -> datetime:
        return self._as_utc(
            self._fixed_now or datetime.now(UTC)
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value.astimezone(UTC)

    @staticmethod
    def _is_race_session(session: RaceSession) -> bool:
        identifier = session.session_identifier.strip().upper()

        if identifier:
            return identifier in {"R", "RACE"}

        return session.session_type.strip().casefold() == "race"

    @staticmethod
    def _is_qualifying_session(session: RaceSession) -> bool:
        identifier = session.session_identifier.strip().upper()

        if identifier:
            return identifier in {"Q", "QUALIFYING"}

        return (
            session.session_type.strip().casefold()
            == "qualifying"
        )

    @staticmethod
    def _driver_display_name(driver: Driver) -> str:
        return (
            driver.full_name
            or " ".join(
                part
                for part in [
                    driver.first_name,
                    driver.last_name,
                ]
                if part
            )
            or driver.abbreviation
            or driver.driver_number
        )

    @staticmethod
    def _profile_display_name(
        profile: UserProfile | None,
    ) -> str:
        if profile is None or not profile.display_name:
            return "RacePulse fan"

        return profile.display_name

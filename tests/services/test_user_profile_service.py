from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser
from app.models.user_profile import UserProfile
from app.schemas.user import CurrentUserProfileUpdate
from app.services.user_profile_service import UserProfileService


def make_user(
    *,
    username: str = "shawn",
    email: str = "shawn@example.com",
    display_name: str = "Shawn Matunda",
) -> AuthenticatedUser:
    return AuthenticatedUser(
        subject="keycloak-user-id",
        username=username,
        email=email,
        email_verified=True,
        display_name=display_name,
        realm_roles=frozenset({"fan"}),
    )


def test_profile_is_created_once_and_identity_is_refreshed(
    db_session: Session,
) -> None:
    service = UserProfileService(db_session)

    first = service.get_or_create_profile(make_user())
    second = service.get_or_create_profile(
        make_user(
            username="shawn-matunda",
            email="new-email@example.com",
        )
    )

    profiles = db_session.scalars(select(UserProfile)).all()

    assert len(profiles) == 1
    assert first.profile_id == second.profile_id
    assert second.username == "shawn-matunda"
    assert second.email == "new-email@example.com"
    assert second.display_name == "Shawn Matunda"
    assert second.roles == ["fan"]


def test_profile_display_name_can_be_updated_and_cleared(
    db_session: Session,
) -> None:
    service = UserProfileService(db_session)
    user = make_user()

    service.get_or_create_profile(user)

    updated = service.update_profile(
        user,
        CurrentUserProfileUpdate(display_name="Race Engineer"),
    )
    cleared = service.update_profile(
        user,
        CurrentUserProfileUpdate(display_name=None),
    )

    assert updated.display_name == "Race Engineer"
    assert cleared.display_name is None
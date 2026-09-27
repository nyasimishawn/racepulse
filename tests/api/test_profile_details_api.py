from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.models.driver import Driver
from app.models.team import Team


AVATAR = {
    "model_url": "https://assets.example.com/avatar.glb?v=1",
    "poster_url": "https://assets.example.com/avatar.webp",
    "alt_text": "A racing helmet in team colours",
    "credit": "RacePulse artist",
    "auto_rotate": True,
}
PAYLOAD = {
    "biography": "A full, sourced biography.",
    "short_bio": "A short introduction.",
    "source_url": "https://example.com/profile",
    "publisher": "Example Racing",
    "published_at": "2026-04-01T12:00:00Z",
    "avatar": AVATAR,
}


def seed(db: Session, kind: str) -> str:
    if kind == "drivers":
        entity = Driver(
            source="DEMO", driver_number="7", full_name="Example Driver"
        )
    else:
        entity = Team(source="DEMO", name="Example Racing")
    db.add(entity)
    db.commit()
    return str(entity.id)


def authenticate(role: str = "editor") -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        subject=f"profile-{role}",
        username=role,
        email=f"{role}@example.com",
        email_verified=True,
        display_name=role.title(),
        realm_roles=frozenset({role}),
    )


@pytest.mark.parametrize("kind,details", [
    ("drivers", {"date_of_birth": "1999-11-13", "nationality": "Example"}),
    ("teams", {"base": "Example City", "first_entry_year": 1966}),
])
def test_profiles_round_trip_and_keep_new_fields_for_old_clients(
    api_client: TestClient, db_session: Session, kind: str, details: dict,
) -> None:
    entity_id = seed(db_session, kind)
    path = f"/api/v1/{kind}/{entity_id}"
    authenticate()
    response = api_client.put(
        f"{path}/profile", json={**PAYLOAD, "details": details}
    )
    assert response.status_code == 200, response.text
    assert response.json()["avatar"] == AVATAR
    for key, value in details.items():
        assert response.json()["details"][key] == value

    # A biography-only client must not silently erase avatar/details data.
    legacy = {k: v for k, v in PAYLOAD.items() if k not in {"avatar", "short_bio"}}
    legacy["biography"] = "Updated biography."
    response = api_client.put(f"{path}/profile", json=legacy)
    assert response.json()["avatar"] == AVATAR
    assert response.json()["short_bio"] == PAYLOAD["short_bio"]
    assert response.json()["details"] is not None

    app.dependency_overrides.pop(get_current_user)
    public = api_client.get(path)
    assert public.status_code == 200
    assert public.json()["biography"] == "Updated biography."
    cards = api_client.get(f"/api/v1/{kind}?query=Example").json()
    assert cards[0]["short_bio"] == PAYLOAD["short_bio"]
    assert cards[0]["avatar"] == AVATAR

    authenticate()
    cleared = api_client.put(
        f"{path}/profile",
        json={**legacy, "avatar": None, "short_bio": None, "details": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["avatar"] is None
    assert cleared.json()["short_bio"] is None
    assert cleared.json()["details"] is None


@pytest.mark.parametrize("kind", ["drivers", "teams"])
def test_profile_card_pagination_and_missing_content(
    api_client: TestClient, db_session: Session, kind: str,
) -> None:
    seed(db_session, kind)
    seed(db_session, kind)
    first = api_client.get(f"/api/v1/{kind}?limit=1").json()
    second = api_client.get(f"/api/v1/{kind}?limit=1&offset=1").json()
    assert first[0]["id"] != second[0]["id"]
    assert first[0]["avatar"] is None
    assert first[0]["short_bio"] is None
    assert api_client.get(f"/api/v1/{kind}?offset=2").json() == []
    assert api_client.get(f"/api/v1/{kind}?query=absent").json() == []
    assert api_client.get(f"/api/v1/{kind}?offset=-1").status_code == 422
    assert api_client.get(f"/api/v1/{kind}/{uuid4()}").status_code == 404


@pytest.mark.parametrize("kind", ["drivers", "teams"])
@pytest.mark.parametrize("invalid", [
    {"short_bio": "   "},
    {"short_bio": "x" * 501},
    {"avatar": {**AVATAR, "model_url": "javascript:alert(1)"}},
    {"avatar": {**AVATAR, "model_url": "https://example.com/file.html"}},
    {"avatar": {**AVATAR, "poster_url": "file:///local.png"}},
    {"avatar": {**AVATAR, "alt_text": " "}},
    {"details": {"unknown_field": "not silently dropped"}},
])
def test_profile_rejects_invalid_content(
    api_client: TestClient, db_session: Session, kind: str, invalid: dict,
) -> None:
    entity_id = seed(db_session, kind)
    authenticate()
    response = api_client.put(
        f"/api/v1/{kind}/{entity_id}/profile", json={**PAYLOAD, **invalid}
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("kind", ["drivers", "teams"])
def test_fan_cannot_change_avatar(
    api_client: TestClient, db_session: Session, kind: str,
) -> None:
    entity_id = seed(db_session, kind)
    authenticate("fan")
    response = api_client.put(
        f"/api/v1/{kind}/{entity_id}/profile", json=PAYLOAD
    )
    assert response.status_code == 403

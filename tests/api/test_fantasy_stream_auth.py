from types import SimpleNamespace

from fastapi import HTTPException, status

from app.api.v1 import fantasy
from app.core.security import AuthenticatedUser


def test_fantasy_stream_rejects_a_missing_bearer_header() -> None:
    websocket = SimpleNamespace(headers={})

    assert fantasy._is_authorized_fantasy_stream(websocket) is False


def test_fantasy_stream_rejects_a_disabled_profile(monkeypatch) -> None:
    closed = False

    class FakeSession:
        def close(self) -> None:
            nonlocal closed
            closed = True

    websocket = SimpleNamespace(
        headers={"authorization": "Bearer access-token"}
    )
    monkeypatch.setattr(fantasy, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        fantasy,
        "authenticate_access_token",
        lambda _: AuthenticatedUser(
            subject="disabled-subject",
            username="fan",
            email=None,
            email_verified=False,
            display_name="Fan",
            realm_roles=frozenset({"fan"}),
        ),
    )

    def reject_disabled_profile(*args, **kwargs):
        del args, kwargs
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    monkeypatch.setattr(
        fantasy,
        "ensure_active_profile",
        reject_disabled_profile,
    )

    assert fantasy._is_authorized_fantasy_stream(websocket) is False
    assert closed is True

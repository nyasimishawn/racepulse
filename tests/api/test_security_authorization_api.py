from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.v1 import import_jobs
from app.core.rate_limit import expensive_request_rate_limit
from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.models.user_profile import UserProfile


def _override_current_user(*, subject: str, role: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        subject=subject,
        username="test-user",
        email="test-user@example.com",
        email_verified=True,
        display_name="Test User",
        realm_roles=frozenset({role}),
    )


def test_disabled_existing_profile_rejects_a_valid_overridden_token(
    api_client: TestClient,
    db_session: Session,
) -> None:
    subject = "disabled-keycloak-subject"
    db_session.add(
        UserProfile(
            keycloak_subject=subject,
            username="disabled-user",
            email="disabled@example.com",
            is_active=False,
            keycloak_roles=["fan"],
        )
    )
    db_session.commit()
    _override_current_user(subject=subject, role="fan")

    response = api_client.get("/api/v1/users/me")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "HTTP_403"
    assert response.json()["error"]["message"] == (
        "This RacePulse account is disabled."
    )


def test_fan_cannot_enqueue_an_import_job(
    api_client: TestClient,
    monkeypatch,
) -> None:
    _override_current_user(subject="fan-import-subject", role="fan")
    app.dependency_overrides[expensive_request_rate_limit] = lambda: None

    def queue_should_not_run(*args, **kwargs):
        del args, kwargs
        raise AssertionError("A fan request reached the import queue.")

    monkeypatch.setattr(import_jobs, "_queue_import", queue_should_not_run)

    response = api_client.post(
        "/api/v1/import-jobs",
        json={
            "year": 2026,
            "event_name": "Test Grand Prix",
            "session_type": "Race",
            "imported_session_id": None,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "HTTP_403"

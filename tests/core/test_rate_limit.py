from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.rate_limit import reset_fallback_rate_limits
from app.main import app


class _UnavailableRedis:
    async def incr(self, key: str) -> int:
        del key
        raise RedisError("Redis is unavailable for this test.")


def test_registration_rate_limit_fallback_returns_normalized_429(
    api_client: TestClient,
    monkeypatch,
) -> None:
    original_redis = app.state.redis
    reset_fallback_rate_limits()
    monkeypatch.setattr(settings, "registration_rate_limit", 1)
    monkeypatch.setattr(settings, "registration_rate_window_seconds", 60)
    monkeypatch.setattr(
        settings,
        "keycloak_public_registration_enabled",
        False,
    )
    app.state.redis = _UnavailableRedis()

    payload = {
        "username": "rate-limit-user",
        "email": "rate-limit@example.com",
        "display_name": "Rate Limit User",
        "password": "TestOnlyPassword1!",
    }
    try:
        allowed = api_client.post("/api/v1/accounts/register", json=payload)
        limited = api_client.post("/api/v1/accounts/register", json=payload)
    finally:
        app.state.redis = original_redis
        reset_fallback_rate_limits()

    assert allowed.status_code == 404
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json()["error"] == {
        "code": "HTTP_429",
        "message": "Too many requests. Try again later.",
        "details": None,
    }

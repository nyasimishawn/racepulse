from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models.curated_content import EditorialUpdate
from app.schemas.paddock import PaddockContext


def test_public_feed_filters_and_paginates_without_drafts_or_future(
    api_client, db_session
):
    now = datetime.now(UTC)
    for i in range(5):
        db_session.add(
            EditorialUpdate(
                title=f"Story {i}",
                body="Original summary",
                update_type="TEAM_UPDATE",
                publication_status="DRAFT" if i == 3 else "PUBLISHED",
                source_url="https://example.com/source",
                publisher="Test",
                confidence="HIGH",
                published_at=now + timedelta(days=1)
                if i == 4
                else now - timedelta(minutes=i),
                context={
                    "category": "DRIVER_MARKET" if i != 2 else "TECHNICAL"
                },
            )
        )
    db_session.commit()
    response = api_client.get(
        "/api/v1/editorial/updates?category=DRIVER_MARKET&limit=1&offset=1"
    )
    assert response.status_code == 200, response.text
    assert [item["title"] for item in response.json()] == ["Story 1"]
    assert len(api_client.get("/api/v1/editorial/updates").json()) == 3
    assert (
        api_client.get(
            "/api/v1/editorial/updates?category=INVALID"
        ).status_code
        == 422
    )


def test_context_validates_nomination_and_confirmed_seat():
    import pytest

    with pytest.raises(ValueError):
        PaddockContext(
            category="TYRES",
            calendar_weekend_id=uuid4(),
            tyres={"hard": "C4", "medium": "C3", "soft": "C5"},
        )
    with pytest.raises(ValueError):
        PaddockContext(
            category="DRIVER_MARKET",
            evidence="REPORTED",
            driver_market={
                "driver_name": "Test",
                "team_name": "Team",
                "season": 2027,
                "status": "CONFIRMED",
            },
        )


def test_legacy_stories_are_general_and_future_stories_are_editor_visible(
    api_client, db_session
):
    from app.core.security import AuthenticatedUser, get_current_user
    from app.main import app

    now = datetime.now(UTC)
    rows = []
    for title, context, published_at in [
        ("Legacy", None, now - timedelta(days=1)),
        ("Scheduled", {"category": "GENERAL"}, now + timedelta(days=1)),
    ]:
        row = EditorialUpdate(
            title=title,
            body="Original summary",
            update_type="TEAM_UPDATE",
            publication_status="PUBLISHED",
            source_url="https://example.com",
            publisher="Test",
            confidence="HIGH",
            published_at=published_at,
            context=context,
        )
        db_session.add(row)
        rows.append(row)
    db_session.commit()
    response = api_client.get("/api/v1/editorial/updates?category=GENERAL")
    assert response.status_code == 200
    assert [item["title"] for item in response.json()] == ["Legacy"]
    assert (
        api_client.get(f"/api/v1/editorial/updates/{rows[1].id}").status_code
        == 404
    )
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        subject="editor",
        username="editor",
        email=None,
        email_verified=False,
        display_name="Editor",
        realm_roles=frozenset({"editor"}),
        groups=frozenset(),
    )
    response = api_client.get(
        "/api/v1/editorial/updates/manage?publication_status=PUBLISHED"
    )
    assert response.status_code == 200
    assert {item["title"] for item in response.json()} == {
        "Legacy",
        "Scheduled",
    }

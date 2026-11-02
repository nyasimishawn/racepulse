from datetime import UTC, datetime

from app.models.curated_content import EditorialUpdate
from app.providers.news_provider import make_item
from app.services.news_service import NewsService


def test_news_feed_omits_seeded_examples_and_supports_date_only_filters(
    api_client, db_session
):
    db_session.add(
        EditorialUpdate(
            update_type="TEAM_UPDATE",
            publication_status="PUBLISHED",
            title="Illustrative seeded story",
            body="Example",
            source_url="https://example.com/story",
            publisher="Example",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            confidence="LOW",
            context={},
            data_quality_flags=[],
        )
    )
    db_session.commit()
    NewsService(db_session).ingest(
        [
            make_item(
                "motorsport_f1",
                "Mercedes update",
                "https://www.motorsport.com/f1/news/mercedes/1/",
                "2026-01-01T09:00:00Z",
            )
        ]
    )
    response = api_client.get(
        "/api/v1/editorial/news?since=2026-01-01&until=2026-02-01&q=mercedes&order=oldest"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Mercedes update"
    assert payload["coverage"]["complete"] is False
    assert "last_checked_at" in payload["sync"]


def test_invalid_cursor_and_preseason_range_are_validation_errors(api_client):
    assert (
        api_client.get("/api/v1/editorial/news?cursor=garbage").status_code
        == 422
    )
    assert (
        api_client.get("/api/v1/editorial/news?since=2025-01-01").status_code
        == 422
    )
    assert (
        api_client.get(
            "/api/v1/editorial/news?since=2026-02-01&until=2026-01-01"
        ).status_code
        == 422
    )

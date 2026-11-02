from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.cli.import_news_archive import import_metadata
from app.models.curated_content import EditorialUpdate
from app.models.news import ImportedNews, NewsSourceState
from app.providers.news_provider import make_item
from app.services.news_service import (
    NewsService,
    SEASON_START,
    category_for,
    utc,
)

NOW = datetime(2026, 10, 5, 20, tzinfo=UTC)


def item(number, title=None, date=None):
    return make_item(
        "motorsport_f1",
        title or f"News {number}",
        f"https://www.motorsport.com/f1/news/news-{number}/{number}/",
        date or "2026-01-01T09:00:00Z",
    )


def page(service, **kwargs):
    return service.page(
        since=SEASON_START,
        until=None,
        query="",
        order="newest",
        cursor=None,
        limit=2,
        now=NOW,
        **kwargs,
    )


def test_deduplication_preserves_original_time_and_editor_correction(
    db_session,
):
    service = NewsService(db_session)
    first = item(1)
    assert service.ingest([first, first], now=NOW) == 1
    row = db_session.scalar(select(EditorialUpdate))
    assert row.title == "News 1"
    assert row.context["evidence"] == "REPORTED"
    assert "original report" in row.body
    assert len(db_session.scalars(select(ImportedNews)).all()) == 1
    corrected = item(1, "Corrected publisher headline", "2026-01-01T10:00:00Z")
    assert service.ingest([corrected], now=NOW) == 0
    assert row.title == corrected.title
    # Source publication time must never be replaced with the collection time.
    assert utc(row.published_at).hour == 10


def test_feed_does_not_invent_missing_history_or_show_future_articles(
    db_session,
):
    service = NewsService(db_session)
    service.ingest(
        [
            item(1, date="2025-12-31T23:59:59Z"),
            item(2, date="2026-10-06T00:00:00Z"),
            item(3),
        ],
        now=NOW,
    )
    result = page(service)
    assert result.total == 1
    assert result.coverage.complete is False
    assert result.coverage.backfill_status == "PENDING"


def test_cursors_stable_with_same_time_and_new_imports(db_session):
    service = NewsService(db_session)
    service.ingest([item(i) for i in range(5)], now=NOW - timedelta(seconds=1))
    first = page(service)
    service.ingest([item(100)], now=NOW + timedelta(seconds=1))
    second = service.page(
        since=SEASON_START,
        until=None,
        query="",
        order="newest",
        cursor=first.next_cursor,
        limit=2,
        now=NOW + timedelta(seconds=2),
    )
    third = service.page(
        since=SEASON_START,
        until=None,
        query="",
        order="newest",
        cursor=second.next_cursor,
        limit=2,
        now=NOW + timedelta(seconds=2),
    )
    ids = [row.id for p in (first, second, third) for row in p.items]
    assert len(ids) == len(set(ids)) == 5
    assert third.next_cursor is None
    assert second.total == 5
    with pytest.raises(ValueError, match="cursor"):
        service.page(
            since=SEASON_START,
            until=None,
            query="Mercedes",
            order="newest",
            cursor=first.next_cursor,
            limit=2,
            now=NOW,
        )


def test_search_whole_archive_month_order_and_literal_wildcards(db_session):
    service = NewsService(db_session)
    service.ingest(
        [
            item(1, "Mercedes front wing news", "2026-01-01T09:00:00Z"),
            item(2, "Ferrari news", "2026-01-31T09:00:00Z"),
            item(3, "Mercedes news", "2026-02-01T00:00:00Z"),
        ],
        now=NOW,
    )
    result = service.page(
        since=SEASON_START,
        until=datetime(2026, 2, 1, tzinfo=UTC),
        query="Mercedes",
        order="oldest",
        cursor=None,
        limit=1,
        now=NOW,
    )
    assert result.total == 1
    assert result.items[0].title == "Mercedes front wing news"
    assert result.next_cursor is None
    wildcard = service.page(
        since=SEASON_START,
        until=None,
        query="%",
        order="newest",
        cursor=None,
        limit=10,
        now=NOW,
    )
    assert wildcard.total == 0


def test_archive_import_validates_all_rows_before_writing(db_session):
    valid = dict(
        title="January news",
        url=item(1).url,
        published_at="2026-01-01T09:00:00Z",
    )
    unsafe = dict(valid, url="https://example.com/f1/news/news/1/")
    with pytest.raises(ValueError, match="publisher"):
        import_metadata(db_session, {"stories": [valid, unsafe]}, NOW)
    assert db_session.scalar(select(EditorialUpdate)) is None
    data = {
        "stories": [valid],
        "manifest": {"archive_complete": True, "failures": []},
    }
    assert import_metadata(db_session, data, NOW) == 1
    assert import_metadata(db_session, data, NOW) == 0
    result = page(NewsService(db_session))
    assert result.coverage.complete is True
    assert result.sync.last_success_at is None


def test_source_failure_keeps_earlier_success_time_and_archive(db_session):
    service = NewsService(db_session)
    service.ingest([item(1)], now=NOW)
    state = db_session.get(NewsSourceState, "motorsport_f1")
    state.status = "ERROR"
    state.last_success_at = NOW - timedelta(hours=1)
    state.last_checked_at = NOW
    db_session.commit()
    result = page(service)
    assert result.sync.status == "DEGRADED"
    assert result.sync.last_success_at == NOW - timedelta(hours=1)
    assert result.total == 1


def test_typename_import_failure_writes_nothing_and_repaired_headlines_reclassify(
    db_session,
):
    bad = dict(
        title="System.Xml.XmlElement",
        url=item(1).url,
        published_at="2026-01-01T09:00:00Z",
    )
    with pytest.raises(ValueError, match="headline"):
        import_metadata(db_session, {"stories": [bad]}, NOW)
    assert db_session.scalar(select(EditorialUpdate)) is None
    service = NewsService(db_session)
    service.ingest([item(1, "Honda HRC boss to retire")], now=NOW)
    row = db_session.scalar(select(EditorialUpdate))
    row.context = {"category": "TYRES", "evidence": "REPORTED"}
    db_session.commit()
    service.ingest([item(1, "Honda HRC boss to retire")], now=NOW)
    assert row.context["category"] == "GENERAL"


@pytest.mark.parametrize(
    "title,category",
    [
        ("Honda HRC to get new boss with Watanabe set to retire", "GENERAL"),
        ("A tireless effort in the paddock", "GENERAL"),
        ("Pirelli explains the soft tyre compound", "TYRES"),
        ("Hard tires shape the weekend", "TYRES"),
    ],
)
def test_tyre_topics_match_words_without_retirement_false_positives(
    title, category
):
    assert category_for(title) == category

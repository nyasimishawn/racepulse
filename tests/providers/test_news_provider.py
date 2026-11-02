import httpx
import pytest

from app.providers.news_provider import (
    NewsFeedProvider,
    PublisherError,
    canonical_url,
    make_item,
    parse_feed,
)

RSS = b"""<?xml version="1.0"?><rss><channel><item>
<title>Mercedes &amp; Ferrari update</title>
<link>https://www.motorsport.com/f1/news/mercedes/1/?utm_source=rss</link>
<pubDate>Thu, 01 Jan 2026 10:15:00 +0100</pubDate>
<description>Full article content must not be copied.</description>
</item></channel></rss>"""


def test_rss_uses_source_metadata_not_body_or_crawl_time():
    [item] = parse_feed("motorsport_f1", RSS)
    assert item.title == "Mercedes & Ferrari update"
    assert item.url == "https://www.motorsport.com/f1/news/mercedes/1/"
    assert item.published_at.hour == 9
    assert item.publisher == "Motorsport.com"
    assert not hasattr(item, "body")


def test_bad_feed_and_entity_documents_fail_closed():
    for document in (
        b"<html>Challenge</html>",
        b'<!DOCTYPE rss [<!ENTITY a "bomb">]><rss/>',
    ):
        with pytest.raises(PublisherError):
            parse_feed("motorsport_f1", document)
    with pytest.raises(PublisherError):
        parse_feed(
            "motorsport_f1",
            RSS.replace(b"Thu, 01 Jan 2026 10:15:00 +0100", b""),
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.motorsport.com/f1/news/story/1/",
        "https://user:secret@www.motorsport.com/f1/news/story/1/",
        "https://127.0.0.1/f1/news/story/1/",
        "https://www.motorsport.com/f1/news/archive/2026/01/",
    ],
)
def test_unknown_or_unsafe_links_rejected(url):
    with pytest.raises(ValueError):
        canonical_url(url)


def test_naive_dates_and_mismatched_publisher_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        make_item(
            "motorsport_f1",
            "News",
            "https://www.motorsport.com/f1/news/story/1/",
            "2026-01-01T10:00:00",
        )
    with pytest.raises(ValueError, match="publisher"):
        make_item(
            "motorsport_f1",
            "News",
            "https://www.autosport.com/f1/news/story/1/",
            "2026-01-01T10:00:00Z",
        )


def test_access_block_does_not_retry_or_change_identity():
    requests = []

    def blocked(request):
        requests.append(request)
        return httpx.Response(403)

    with httpx.Client(transport=httpx.MockTransport(blocked)) as client:
        provider = NewsFeedProvider(client)
        with pytest.raises(PublisherError, match="403"):
            provider.fetch("motorsport_f1")
    assert len(requests) == 1


def test_rate_limit_retry_after_is_exposed_to_shared_worker():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429, headers={"Retry-After": "3600"}
            )
        )
    ) as client:
        with pytest.raises(PublisherError) as result:
            NewsFeedProvider(client).fetch("motorsport_f1")
    assert result.value.retry_after is not None


@pytest.mark.parametrize(
    "title", ["System.Xml.XmlElement", "System.Xml.XmlNode", "[object Object]"]
)
def test_object_type_names_cannot_be_imported_as_publisher_headlines(title):
    with pytest.raises(ValueError, match="headline"):
        make_item(
            "motorsport_f1",
            title,
            "https://www.motorsport.com/f1/news/story/1/",
            "2026-01-01T10:00:00Z",
        )

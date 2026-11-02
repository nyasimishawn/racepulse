"""Approved public feeds. Only headline/link/publication metadata is retained."""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import ssl
import re
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx


@dataclass(frozen=True)
class NewsSource:
    id: str
    publisher: str
    host: str
    feed_url: str


SOURCES = {
    "motorsport_f1": NewsSource(
        "motorsport_f1",
        "Motorsport.com",
        "www.motorsport.com",
        "https://www.motorsport.com/rss/f1/news/",
    ),
    "autosport_f1": NewsSource(
        "autosport_f1",
        "Autosport",
        "www.autosport.com",
        "https://www.autosport.com/rss/f1/news/",
    ),
}


@dataclass(frozen=True)
class NewsItem:
    source_id: str
    publisher: str
    title: str
    url: str
    published_at: datetime


class PublisherError(Exception):
    """A safe operator message, with no response body or credentials."""

    def __init__(self, message: str, retry_after: datetime | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _Text()
    parser.feed(unescape(value))
    return " ".join("".join(parser.parts).split())


def canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Publisher links must use HTTPS without credentials.")
    if parsed.port not in (None, 443):
        raise ValueError("Unsupported publisher port.")
    if host not in {source.host for source in SOURCES.values()}:
        raise ValueError("Unknown publisher host.")
    if not parsed.path.startswith("/f1/news/") or "/archive/" in parsed.path:
        raise ValueError("Only Formula 1 article links are accepted.")
    # Feed trackers/fragments must not create duplicate stories.
    return urlunsplit(("https", host, parsed.path.rstrip("/") + "/", "", ""))


def make_item(
    source_id: str, title: str, url: str, published: str
) -> NewsItem:
    source = SOURCES[source_id]
    normalized_url = canonical_url(url)
    if urlsplit(normalized_url).hostname != source.host:
        raise ValueError("The article host does not match its publisher.")
    normalized_title = plain_text(title)
    if not normalized_title or len(normalized_title) > 2000:
        raise ValueError("Missing or excessive publisher headline.")
    if (
        re.fullmatch(r"System\.[A-Za-z.\[\]]+", normalized_title)
        or normalized_title == "[object Object]"
    ):
        raise ValueError(
            "Publisher headline contained an object type name instead of text."
        )
    try:
        date = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError:
        date = parsedate_to_datetime(published)
    if date.tzinfo is None:
        raise ValueError(
            "Publisher publication times must include a timezone."
        )
    return NewsItem(
        source_id=source_id,
        publisher=source.publisher,
        title=normalized_title,
        url=normalized_url,
        published_at=date.astimezone(UTC),
    )


def parse_feed(source_id: str, content: bytes) -> list[NewsItem]:
    if (
        len(content) > 2_000_000
        or b"<!DOCTYPE" in content.upper()
        or b"<!ENTITY" in content.upper()
    ):
        raise PublisherError("Publisher feed exceeded the safe XML limits.")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise PublisherError(
            "Publisher returned an invalid RSS document."
        ) from error
    if root.tag != "rss" or root.find("channel") is None:
        raise PublisherError(
            "Publisher returned a document that was not an RSS feed."
        )
    entries = root.findall("./channel/item")
    items = []
    for entry in entries:
        try:
            items.append(
                make_item(
                    source_id,
                    entry.findtext("title") or "",
                    entry.findtext("link") or "",
                    entry.findtext("pubDate") or "",
                )
            )
        except (ValueError, TypeError, OverflowError):
            # Missing times are never replaced with the crawl time.
            continue
    if entries and not items:
        raise PublisherError(
            "Publisher feed had no usable dated Formula 1 headlines."
        )
    return items


class NewsFeedProvider:
    def __init__(self, client: httpx.Client | None = None):
        self._owned = client is None
        # Use OS trust roots (including managed Windows roots). TLS stays verified.
        self.client = client or httpx.Client(
            verify=ssl.create_default_context(),
            timeout=25,
            headers={"User-Agent": "RacePulse-News/1.0"},
            follow_redirects=False,
        )

    def close(self):
        if self._owned:
            self.client.close()

    def fetch(self, source_id: str) -> list[NewsItem]:
        source = SOURCES[source_id]
        try:
            response = self.client.get(source.feed_url)
            if response.status_code in {401, 403, 429}:
                retry_after = None
                retry_header = response.headers.get("Retry-After", "")
                if retry_header:
                    try:
                        if retry_header.isdecimal():
                            from datetime import timedelta

                            retry_after = datetime.now(UTC) + timedelta(
                                seconds=int(retry_header)
                            )
                        else:
                            retry_after = parsedate_to_datetime(
                                retry_header
                            ).astimezone(UTC)
                    except (ValueError, TypeError, OverflowError):
                        pass
                raise PublisherError(
                    f"Publisher feed unavailable ({response.status_code}); respect its access limits.",
                    retry_after=retry_after,
                )
            response.raise_for_status()
            return parse_feed(source_id, response.content)
        except httpx.HTTPError as error:
            raise PublisherError(
                "Publisher feed could not be reached securely."
            ) from error

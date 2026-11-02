# Paddock publisher news

The Paddock news API uses only rows tracked by the publisher ingester. Existing
editorial examples and manually curated stories remain available through their
original editorial endpoints and are not silently presented as imported news.

`GET /api/v1/editorial/news` accepts `since` (default 1 January 2026), exclusive
`until`, `q`, `order=newest|oldest`, opaque `cursor`, and `limit` (1–100). Times are
UTC. It returns items with the existing editorial response shape, `next_cursor`,
matching `total`, archive `coverage`, and source `sync` timestamps. Search applies
to the whole saved archive rather than just the current page. New imports do not
move a cursor's snapshot; refresh starts a new snapshot. Sorting is stable when
articles share publication times. Source links open the original publisher.

Only title, canonical link, publisher, and original publication time are retained.
Article text and publisher photographs are not copied. Each imported item is
labelled REPORTED, even when a headline quotes a team; an automated headline is
not an independently verified official announcement. Keyword topics help browse
the feed and are not factual claims about nominations, contracts or results.

Official public feed listings:

- Motorsport.com: <https://www.motorsport.com/rss/>
- Motorsport.com F1 feed: <https://www.motorsport.com/rss/f1/news/>
- Autosport F1 feed: <https://www.autosport.com/rss/f1/news/>

Apply the additive migration with `python -m alembic upgrade head`. Then run
`python -m app.workers.news_worker` (or the Compose `news-worker` service). A
database lease allows only one collector at a time. Feeds are checked every two
minutes during known race-weekend sessions and every ten minutes otherwise.
`--once` makes one check. `NEWS_SYNC_ENABLED=false` disables scheduled checks.
TLS verification remains enabled and uses the operating system's trust roots.

The feeds contain a rolling window, not all season history. Historical metadata
must be collected through source-permitted public archives or a licensed export,
then imported with
`python -m app.cli.import_news_archive --metadata-file PATH`. The verified JSON
contains `stories: [{source_id, publisher, title, url, published_at}]` and a
`manifest` with `archive_complete` and `failures`. The CLI validates the publisher,
HTTPS host, F1 article path, publication times, and the whole file before writing.
URL hashes deduplicate feed and archive records; repeated imports are safe.

An archive reported complete means the connected publisher's available public
listings were imported for the stated window. It does not mean every publisher
or every Formula 1 story on the internet is covered. Archive completeness and
recent feed-check time are separate. An HTTP 401, 403 or 429 is reported as a
source failure; the worker does not rotate identities or bypass access controls.
Saved stories remain readable through outages. Publisher corrections to the
headline/time are applied on the next sighting unless an editor has amended the
row. Feed removals do not erase historical articles.

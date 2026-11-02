"""Import verified public publisher headline metadata without article bodies.

The input is an operator-produced JSON manifest. No URL in this file is fetched,
and no arbitrary publisher/hostname can be imported. This lets archive collection
remain separate from the public API and honours publisher access restrictions.
"""

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.providers.news_provider import make_item
from app.services.news_service import NewsService, SEASON_START, utc
from app.workers.news_worker import acquire_lease, release_lease


def import_metadata(
    db: Session, payload: dict, now: datetime | None = None
) -> int:
    now = utc(now or datetime.now(UTC))
    # Validate the whole document before writing any row.
    if (
        not isinstance(payload.get("stories"), list)
        or len(payload["stories"]) > 20000
    ):
        raise ValueError(
            "Archive manifest needs at most 20,000 publisher metadata rows."
        )
    items = []
    for row in payload["stories"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid archive row.")
        source_id = row.get("source_id", "motorsport_f1")
        item = make_item(
            source_id,
            row["title"],
            row.get("url") or row["source_url"],
            row["published_at"],
        )
        if row.get("publisher", item.publisher) != item.publisher:
            raise ValueError(
                "Archive publisher does not match its approved source."
            )
        if not SEASON_START <= item.published_at <= now:
            raise ValueError(
                "Archive row lies outside the 2026-to-present window."
            )
        items.append(item)
    manifest = payload.get("manifest", {})
    failures = manifest.get("failures", [])
    complete = manifest.get("archive_complete") is True and not failures
    service = NewsService(db)
    count = 0
    for offset in range(0, len(items), 100):
        count += service.ingest(items[offset : offset + 100], now=now)
    for source_id in {item.source_id for item in items}:
        state = service.source_state(source_id)
        state.archive = {
            **state.archive,
            "status": "COMPLETE" if complete else "PARTIAL",
            "complete": complete,
            "imported_at": now.isoformat(),
            "start": SEASON_START.isoformat(),
            "scope": "Public Formula 1 headline listings supplied in the verified source manifest.",
            "metadata_count": sum(
                item.source_id == source_id for item in items
            ),
            "failures": len(failures),
        }
        # Archive collection isn't a recent RSS check. Preserve both timestamps.
    db.commit()
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-file", required=True, type=Path)
    args = parser.parse_args()
    if args.metadata_file.stat().st_size > 20_000_000:
        raise ValueError("Archive manifest is too large.")
    payload = json.loads(args.metadata_file.read_text(encoding="utf-8-sig"))
    owner = uuid4().hex
    with SessionLocal() as db:
        if not acquire_lease(db, owner, datetime.now(UTC)):
            raise RuntimeError(
                "News collector is busy. Retry the import after this check."
            )
        try:
            count = import_metadata(db, payload)
            print(
                f"Imported {count} new publisher headlines; existing rows were preserved."
            )
        finally:
            release_lease(db, owner)


if __name__ == "__main__":
    main()

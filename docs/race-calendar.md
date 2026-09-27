# Race calendar backend

The calendar exists independently of downloaded telemetry. All routes below
use the `/api/v1` prefix. Existing weekend download routes remain available.

Public reads:

- `GET /calendar?year=2026`: weekends ordered by round, including cancellations.
- `GET /weekends/next`: next scheduled or explicitly in-progress race weekend;
  returns JSON `null` when none is available.
- `GET /weekends/{calendar_id}/overview`: schedule, provenance, version and
  session IDs when an imported meeting has been linked.
- `GET /calendar/{calendar_id}/history`: published schedule revisions.

Calendar IDs and weekend-download IDs are different. Use the ID from the
calendar response for overview/history, and download IDs for the existing
download status/retry routes.

Editors publish using `POST /calendar`. Update with
`PUT /calendar/{calendar_id}?version={current_version}`. PUT replaces the full
schedule, increments its version, and saves an audit revision. Stale edits return
409. An editor token is required; fans cannot write schedules.

Example request shape (illustrative data, not a verified race announcement):

```json
{
  "year": 2026,
  "round_number": 1,
  "event_name": "Example Grand Prix",
  "status": "SCHEDULED",
  "meeting_id": null,
  "source_url": "https://example.com/race-announcement",
  "source_checked_at": "2026-09-01T10:00:00Z",
  "change_reason": "Initial schedule",
  "sessions": [
    {"identifier": "R", "name": "Race", "starts_at": "2026-10-01T12:00:00Z", "status": "SCHEDULED"}
  ]
}
```

Supply the complete weekend, including practice, qualifying and sprint sessions
where applicable. Identifiers are FP1, FP2, FP3, SQ, S, Q and R. Dates require an
explicit time zone and are normalized to UTC. Statuses are SCHEDULED, POSTPONED,
CANCELLED, IN_PROGRESS and COMPLETED. Postponed/cancelled sessions can omit times.
For manual entries the source URL records the editor's evidence. The automated
worker fetches structured schedules directly from Formula 1 race pages.

After importing a meeting, update the calendar with its `meeting_id`. The link
is explicit to avoid guessing between similarly named events. Session links are
then resolved by identifier on reads, including sessions imported later.
The provider identifier SS (Sprint Shootout) maps to calendar identifier SQ. Once
assigned, the meeting link cannot be replaced by this API. Historical imported
timestamps are preserved. Linked Fantasy questions use calendar times instead.
Unlinked meetings retain existing Fantasy behavior.

Future Fantasy deadlines follow schedule changes. Cancelled/postponed or removed
sessions are unavailable for predictions. Already elapsed prediction deadlines
are retained through suspension/rescheduling so an editor cannot reopen them
by announcing a later start. Cancellation does not automatically settle scores
or reverse already awarded points; scoring resolution remains an editor action.

## Setup and automatic synchronization

1. Apply migrations: `.venv\Scripts\python.exe -m alembic upgrade head`.
2. Import the current season:
   `.venv\Scripts\python.exe -m app.workers.calendar_sync_worker --once`.
3. Publish the reviewed starter stories:
   `.venv\Scripts\python.exe -m app.cli.seed_paddock`.
4. Run `.venv\Scripts\python.exe -m app.workers.calendar_sync_worker` for a
   refresh every six hours. `--year` selects a season; `--interval` adjusts the
   interval (minimum five minutes). Docker Compose includes `calendar-sync`
   with a restart policy. Apply migrations before starting the services.

The local worker must remain running; a manually launched process does not
restart itself after Windows reboots. `GET /calendar/sync-status?year=2026`
reports the last check, counts and any failed/missing source pages. Read this
status when checking operational health; an unchanged calendar is not proof
that a recent fetch succeeded.

Sync uses stable source URLs, explicit UTC dates and source statuses. It updates
changed entries with an audit revision, leaves unchanged entries alone, and
preserves existing entries on fetch/parser failures. A missing source page is
flagged for review rather than interpreted as a cancellation. PostgreSQL
advisory locks prevent overlapping workers for the same season. A manual PUT
takes ownership of that weekend and disables subsequent automatic overwrites.

On 2026-09-08 the development database was backed up and migrated to
`c3d4e5f6a7b8`, then populated with 23 weekends from the official 2026 calendar.
The initial and subsequent sync runs completed without source errors. Four
original, sourced starter stories were published, including the Madrid tyre
nomination. The seed command is repeatable and never overwrites existing stories.

## Paddock publishing

Existing editor-authenticated `/editorial/updates` create/update routes now
accept a `context` object. Public reads accept `category`, `offset`, `limit`
and `calendar_weekend_id`; drafts and future publications are excluded.

Context fields:

- `category`: GENERAL, WEEKEND, TYRES, DRIVER_MARKET or TECHNICAL.
- `evidence`: OFFICIAL, REPORTED, ANALYSIS or UNVERIFIED.
- `summary`, `why_it_matters` and optional `correction_note`: original editorial
  text. Existing body, source URL, publisher and timestamps remain required.
- `calendar_weekend_id`: links a story to the scheduled event before imports.
- `tyres`: `hard`, `medium`, `soft` compound codes; requires a calendar link and
  TYRES category. Codes must increase from hard to soft; skipped codes are allowed.
- `driver_market`: driver name, team name, season and CONFIRMED/REPORTED/RUMOUR
  status. CONFIRMED requires OFFICIAL evidence and DRIVER_MARKET category.

Flutter Paddock displays categories, pagination, explanations, corrections,
source links that can be copied, tyre roles and driver-seat statuses. Race
Weekend displays stories linked to its selected calendar ID. News publishing
remains editorial; the calendar worker does not scrape or auto-publish articles.
No third-party photographs or complete source articles are bundled. Push
notifications and automated score reversals are outside this change.


## Verification

Run the fast suite with `python -m pytest -q -p no:cacheprovider` and lint with
`python -m ruff check app tests --no-cache`.

The `--postgresql` test option creates a uniquely named temporary database using
`DATABASE_URL` credentials. The role needs permission to create databases. Tests
apply every migration, downgrade the calendar/Paddock revisions, and upgrade them
again before running against the migrated schema. Cleanup drops only that generated
database. Redis integration tests use and delete a unique test stream; they never
flush Redis or consume the application stream.

```powershell
python -m pytest -q -p no:cacheprovider --postgresql tests/integration tests/api/test_calendar_api.py tests/api/test_paddock_api.py tests/api/test_fantasy_api.py tests/api/test_weekends_api.py tests/services/test_calendar_deadlines.py tests/services/test_calendar_sync_service.py tests/services/test_durable_job_service.py tests/services/test_weekend_download_service.py
```

The backend GitHub Actions workflow runs both suites with PostgreSQL and Redis.
Provider responses in tests are fixtures; no live F1 import is required.

Fantasy replays schedule revision publication times to determine deadlines.
A delay published before the prior deadline moves that deadline even when nobody
reads Fantasy until later. A deadline that elapsed before the change stays locked,
including when no question snapshot existed or a session was suspended/removed.
Suspended sessions remain unavailable until a subsequent schedule restores them.

Legacy editorial stories with no context appear under GENERAL. Public lists and
individual reads hide future publications; the editor management list includes them.
A one-shot calendar synchronization exits with status 1 for FAILED or PARTIAL runs,
so deployment checks can detect incomplete synchronization. The season lock is held
until sync status has been committed.

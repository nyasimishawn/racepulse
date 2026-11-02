# Trivia Race

Solo, untimed practice races against three virtual opponents. All scoring and
race state live in the API. Flutter animates confirmed progress only.

## Run

From the backend virtual environment, run `python -m alembic upgrade head` and
restart the FastAPI service. The migration installs 30 sourced questions:
10 Easy, 10 Medium, 10 Hard. No worker or Redis simulation is needed.

Open **Play → Play Trivia Race** in the Flutter app. A saved active race resumes
automatically. Each answer is persisted before the UI moves the cars. Correct
answers give 10 / 20 / 35 distance points depending on difficulty. Each track
requires eight correct answers to complete; opponents move on every submitted
answer. The race ends when someone finishes or after 10 questions. Finishing
time within a turn breaks simultaneous finish ties; otherwise distance decides.

## API

All paths are under `/api/v1/trivia` (or the configured API prefix).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/config` | Published availability and rewards |
| POST | `/guest-session` | Opaque guest key, valid for 365 days |
| POST | `/races` | Start with `difficulty` and UUID `start_key` |
| GET | `/races/active` | Latest active race belonging to this identity |
| GET | `/races/{id}` | Restore an owned race, including completed results |
| POST | `/races/{id}/answers` | Submit assigned `assignment_id` and `option_id` |
| GET | `/questions` | Editor/admin question bank |
| PUT | `/questions/{uuid}` | Create, revise, publish or retire a question |

Use a Keycloak Bearer token for account-owned practice races. Anonymous practice
uses `X-Trivia-Guest-Key`; only its SHA-256 hash is stored in the database. It is
independent of development Fantasy access. Guests cannot edit questions. Games
remain associated with the identity that created them; guest games are not
automatically transferred on sign-in. Anonymous session creation is rate limited.

### Content review

Only `editor` or `admin` roles can access the question endpoints. Create a new
question at a new UUID with four distinct `{id, label}` options, a `difficulty`,
`prompt`, `correct_option_id`, `explanation`, HTTP(S) `source_url`, and
`published: false`. Review the fact and date/era wording, then save with
`published: true` and `expected_version` equal to the latest returned version.
Set `published: false` to retire a question. At least ten published questions
are needed per difficulty. No scraping or generated questions run during play.

Rules, question versions, shuffled opaque option IDs and bot moves are copied
into a private race snapshot. Editing the bank never changes an in-flight race.
Public state includes only the current question; the answer and source are
revealed after that assignment is answered.

### Reliability

Race creation deduplicates `(owner, start_key)`. Answers deduplicate
`(race_id, assignment_id)`; the same answer returns its original response, and
changing an already submitted choice returns 409. An optimistic database
revision update and answer ledger commit together, so concurrent retries cannot
award movement twice. Question assignments cannot be skipped or forged.

## Verification

`python -m pytest tests/test_trivia.py -q` runs gameplay, ownership, validation,
editor and retry tests. Add `--postgresql` to exercise migrations and concurrent
requests using a new temporary database. That fixture requires the development
PostgreSQL role to have database creation permission and removes its test DB.

This release is solo practice. Multiplayer and ranked leaderboards are future
features; there is no live socket or continual simulation cost on the device.

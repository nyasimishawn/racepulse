# RacePulse API guide

The live OpenAPI document at `/openapi.json` is the canonical source for
request and response schemas. This guide explains stable route families,
security expectations, asynchronous job behavior, and compatibility rules.

For circuit history, team estimates and driver comparisons, see
[Dashboard and Head-to-Head](dashboard-head-to-head.md).

New clients should start with
[weekend selection and Keycloak sign-in](weekend-selection-and-login.md).
For driver/team browsing, bios, detail fields and 3D avatar integration, see
[driver and team profiles](driver-team-profiles.md).
`POST /weekends/select` replaces separate download actions in the normal
client flow; `POST /auth/session` mirrors verified login identity into the DB.

## Base URL and compatibility

All HTTP routes are versioned below `/api/v1`. Public race data is deliberately
read-only. Existing Fantasy V1 routes remain supported while V2 response fields
and dashboard routes are additive. Clients must ignore unknown response fields
so a backend metadata addition does not break Flutter releases.

Use ISO 8601 timestamps with UTC offsets. Server-side locks and job transition
times are evaluated in UTC, never on a device clock.

## Error response contract

HTTP errors use a consistent envelope.

```yaml
error:
  code: REQUEST_VALIDATION_FAILED
  message: One or more request values are invalid.
  details: []
```

`details` is optional. Typical status codes are:

| Status | Meaning |
| --- | --- |
| 400 or 422 | The request is syntactically valid enough to reach the API but violates a domain or validation rule. |
| 401 | A required Bearer token is missing or invalid. |
| 403 | The token is valid but the active profile or role policy rejects the request. |
| 404 | The requested public resource or job does not exist. |
| 409 | The request conflicts with a lock, revision, state transition, or completed resource. |
| 429 | A registration or expensive route rate limit was exceeded. Clients should honor the retry guidance. |
| 502 or 503 | A provider or required infrastructure dependency is unavailable. |

Clients must not infer a provider failure from a partial response. Inspect the
returned job state, quality flags, and coverage fields.

## Access policy

Keycloak supplies identity, realm/API-client roles and group membership.
Configure `admin`/`editor`/`fan` composite roles in Keycloak; RacePulse uses the
roles emitted in the access token without adding implied roles. A valid token is
also checked against the RacePulse active-profile record, so a disabled account
cannot use an unexpired token.

| Caller | Permitted work |
| --- | --- |
| Anonymous | Public read-only timing, analysis, schedule, replay read, and health routes. |
| Authenticated active user | Select a complete weekend download and synchronize the local identity snapshot. |
| Fan | Own account/profile and Fantasy entry, group, leaderboard, dashboard, and privacy-safe community routes. |
| Editor | Fan work plus provider previews, imports, import-job control, replay-room control, Fantasy scoring/finalization, resolutions, and editorial writes. |
| Admin | Editor work plus Keycloak-backed user status, role, synchronization, and account-administration actions. |

Do not put bearer tokens in query strings or WebSocket URLs. Protected HTTP
requests use the Authorization header. The Fantasy stream also requires the
same `Authorization: Bearer <access token>` header during its WebSocket
handshake and rejects inactive or non-fan profiles. Public replay streams do
not grant control over replay rooms or mutating operations.

## Public race-data families

The following families expose already imported information and do not trigger a
provider import.

- `/health` and `/health/live` report process liveness. `/health/ready`
  additionally checks PostgreSQL and Redis and returns `503` while either is
  unavailable.
- `/sessions` and `/meetings/{meeting_id}/sessions` expose imported schedule
  and session summaries.
- `/sessions/{session_id}` and `/sessions/{session_id}/timing-tower` expose
  normalized session results.
- `/sessions/{race_session_id}/laps`, telemetry read routes, analytics,
  qualifying, strategy, race context, and Head-to-Head routes return stored
  timing facts or explicitly labelled derived analysis.
- Replay manifest, snapshot, and public playback routes are read-only. Shared
  room creation and control are editor operations.

Limits, offsets, and filters are bounded by the OpenAPI schema. A public route
never causes a background import as a side effect.

## Imports and durable jobs

There is one durable-job architecture: PostgreSQL is the source of truth and a
Redis Stream delivers work to `python -m app.workers.job_worker`. Provider and
job-creation routes are editor-only and rate limited. The canonical creator
routes never run FastF1 work in the HTTP request.

The canonical creator routes are:

| Canonical request | Durable job type | Immediate response and polling |
| --- | --- | --- |
| `POST /import-jobs` | `SESSION_IMPORT` | An import record with `durable_job_id`; poll `GET /import-jobs/{import_job_id}` and `GET /jobs/{durable_job_id}`. |
| `POST /weekends/select` | `WEEKEND_IMPORT` | One shared complete-weekend download; poll `GET /weekends/{id}` for session IDs, progress and per-stage coverage. |
| `POST /sessions/{race_session_id}/laps/import-jobs` | `SESSION_LAPS_IMPORT` | A durable-job response; poll `GET /jobs/{job_id}` for the bulk-lap result. |
| `POST /sessions/{race_session_id}/telemetry-imports` | `SESSION_TELEMETRY_IMPORT` | A bounded telemetry-import record with `durable_job_id`; its domain detail is under `/telemetry-imports/{id}` and `/telemetry-imports/{id}/drivers`. |
| `POST /sessions/{race_session_id}/map-imports` | `SESSION_MAP_IMPORT` | A map-import record with `durable_job_id`; its domain detail is under `/map-imports/{id}` and `/map-imports/{id}/drivers`. |
| `POST /sessions/{race_session_id}/context/import-jobs` | `RACE_CONTEXT_IMPORT` | A durable-job response; poll `GET /jobs/{job_id}`. |
| `POST /fantasy/races/{race_session_id}/scoring-jobs` | `FANTASY_SCORE` | A durable-job response; poll `GET /jobs/{job_id}`. |
| `POST /fantasy/races/{race_session_id}/finalization-jobs` | `FANTASY_FINALIZE` | A durable-job response; poll `GET /jobs/{job_id}`. |

Every canonical creator accepts an optional `Idempotency-Key` header. Clients
should generate one key for one intended operation and reuse it only to retry
that same operation after a timeout. Reusing a key with changed input is not a
supported update mechanism. An import creator returns its domain record for
Flutter compatibility; the linked durable record is the authoritative queue
state.

Editors use `GET /jobs/{job_id}` to poll generic queue state and
`POST /jobs/{job_id}/cancel` to request cooperative cancellation. A response
identifies its type, target, status, progress, attempts, retry time, safe
failure reason, result, and lifecycle timestamps.

Job responses identify the job type and resource, state, progress, retry
metadata, timestamps, and a safe failure reason. The worker owns transitions:

```text
QUEUED -> RUNNING -> COMPLETED
  |         |  \-> RETRY_WAIT -> RUNNING
  |         \----> CANCEL_REQUESTED -> CANCELLED
  \-------------------------------> CANCELLED
```

An attached telemetry or map import may report a domain-level partial result
while its durable execution has completed successfully. Clients should inspect
both the durable job and the linked import resource.

Transient failures can move through a retry wait state before another worker
attempt. Queued or retry-wait work can be cancelled immediately. A running job
first enters `CANCEL_REQUESTED`; a worker stops it at the next safe boundary
implemented by that handler. Cancellation does not force-kill an in-flight
provider network call. Completed imports and re-scoring operations are
idempotent.

Poll the job resource until a terminal state; this release does not define a
job-progress WebSocket contract. Do not repeatedly call a compatibility route
to simulate a worker.

### Compatibility routes

The following endpoints remain to avoid silently breaking existing clients.
New integrations should use the canonical creator routes above.

| Compatibility request | Current behavior |
| --- | --- |
| `POST /import-jobs/{import_job_id}/run` | Re-enqueues or returns the linked durable session-import work; it does not synchronously call FastF1. |
| `POST /sessions/{race_session_id}/laps/import` | Legacy synchronous bulk-lap import. It is editor-only and rate limited; use `/laps/import-jobs` for new work. |
| `POST /telemetry-imports/{telemetry_import_id}/run` | Re-enqueues or returns the linked bounded telemetry work; it does not synchronously call FastF1. |
| `POST /map-imports/{map_import_id}/run` | Re-enqueues or returns the linked map-import work; it does not synchronously call FastF1. |
| `POST /sessions/{race_session_id}/context/import` | Legacy synchronous race-context import. It is editor-only and rate limited; use `/context/import-jobs` for new work. |
| `POST /fantasy/races/{race_session_id}/score` and `/finalize` | Legacy synchronous editor actions. Use `/scoring-jobs` and `/finalization-jobs` for new work. |

The compatibility routes are intentionally retained during the additive API
transition. They are not an invitation to add another queue or background-task
mechanism.

## Authentication and accounts

`/accounts/register` creates an account through the backend Keycloak service
account when public registration is enabled. It is deliberately rate limited.
The API never returns a password, confidential client secret, or Keycloak
access token.

`/users/me` returns the authenticated RacePulse profile. Profile display-name
updates are RacePulse-owned; credentials, enabled state, email verification,
and roles remain Keycloak-owned. Admin user routes are under `/users/admin`.

## Fantasy V1 compatibility and V2 workflow

The V1 entry contract remains at:

- `GET /fantasy/races`
- `GET /fantasy/races/{race_session_id}/prediction`
- `PUT /fantasy/races/{race_session_id}/prediction`
- existing group, leaderboard, scoring, finalization, and resolution routes

The additive V2 entry routes are `GET /fantasy/me/dashboard`,
`GET /fantasy/races/{race_session_id}/entry`, and
`PUT /fantasy/races/{race_session_id}/questions/{question_key}`. The question
route saves or clears only one answer, making an open-question save safe while
other questions remain untouched. The community route is
`GET /fantasy/races/{race_session_id}/questions/{question_key}/community`; it
returns percentages only after the caller answered that question or it has
locked.

One prediction is one Race Weekend Entry for an active fan and a race session.
The update endpoint accepts incremental answers or clears, so a client can save
each question as it is answered.

Questions are generated from imported sessions in the selected meeting:

- FP1, FP2, and FP3 fastest driver appear only when that session exists.
- Q1, Q2, and Q3 fastest-driver questions appear only when qualifying exists.
- Race questions cover P1, P2, P3, fastest lap, winning team, highest
  speed-trap driver, and up to two DNF drivers.
- Overtake predictions are not part of this contract.

Each question has an explicit state, lock time, answer state, and resolution
state. Once its server UTC lock time passes, only that question is immutable;
later questions remain editable. V2 adds completion progress, the next
deadline, personal score/rank, and `GET /fantasy/me/dashboard` without
changing the V1 prediction path.

Community percentages remain private until the fan has submitted that question
or that question has locked. Group membership is capped at 22. Weekend
eligibility is frozen at the first Fantasy question lock for that meeting;
joining later applies only to future weekends.

Public timing can automatically resolve supported questions when coverage is
sufficient. DNF and uncertain outcomes remain editor-verifiable and include a
source reference and data-quality flags. Resolution correction and re-scoring
are idempotent; finalized private-group podiums and season standings refresh
deterministically after a legitimate correction. Editors should queue ordinary
scoring and finalization through `/scoring-jobs` and `/finalization-jobs`; the
older synchronous routes remain only for compatibility.

## Curated content and profile/history data

The API separates imported timing facts from editor-curated content.

- Driver and team profile/history responses label biography, notable moments,
  source attribution, imported-season coverage, and derived statistics.
- Curated FIA, team, and Pirelli updates include a category such as upgrade,
  penalty, grid drop, or race-control editorial update; source URL, publisher,
  published time, confidence, and data-quality fields are required.
- Public reads are safe to cache according to their response metadata.
- Editor CRUD is role-protected and creates provenance rather than pretending
  a source was automatically scraped.

Profile families are `GET /drivers`, `GET /drivers/{driver_id}`, and
`GET /drivers/{driver_id}/history`, with matching `/teams` routes. Editors use
`PUT /drivers/{driver_id}/profile` (or the matching team route) and the
matching `notable-moments` POST, PATCH, and DELETE subroutes to maintain
sourced narrative content. Curated updates are read under
`/editorial/updates`; editor CRUD uses its POST, PATCH, and DELETE operations,
and `/editorial/updates/manage` includes drafts. Consult the final OpenAPI
document for query parameters and request shapes before hard-coding a
pre-release client contract. See the data-source rules for coverage and
attribution requirements.

## Real-time routes

Replay-room and Fantasy stream messages are event payloads, not a replacement
for an authoritative HTTP resource. On reconnect, fetch the room, entry, or
job state first, then resume the stream. Treat revision conflicts as a signal
to reload the current resource before sending another control command.

## Rate limits and client behavior

Registration and provider-intensive endpoints are protected by Redis-backed
abuse controls. A client that receives 429 must back off instead of immediately
retrying. A job creation retry should use the same idempotency key. Clients
must never log credentials, bearer tokens, full error payloads that may contain
operator detail, or FastF1 cache paths.

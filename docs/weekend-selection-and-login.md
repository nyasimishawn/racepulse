# Weekend selection and Keycloak sign-in

## Apply the backend update

Run `alembic upgrade head` once before restarting the API and worker. The new
revision is `f9d0e1a2b3c4`. Keep `python -m app.workers.job_worker` running: the
API queues downloads, and the worker performs them. API reads use PostgreSQL.

## One selection downloads the weekend

After sign-in, the client sends this request when the selected circuit or year
changes:

```http
POST /api/v1/weekends/select
Authorization: Bearer <Keycloak access token>
Content-Type: application/json

{"year": 2023, "event_name": "Monza"}
```

The event name can be the exact circuit location, official event name, event
name, or round number as a string. The schedule resolves the canonical round;
aliases such as `Monza` and `Italian Grand Prix` share one download. Unknown or
ambiguous selections return 422 instead of guessing a different event.

The response is 202 while queued/running and supplies a `Location` header.
Poll `GET /api/v1/weekends/{id}` for progress and per-session stage results.
Selection is available to authenticated active users and is rate limited;
polling and imported race-data reads remain public.

The worker imports the sessions listed by the provider schedule. For a normal
weekend this includes FP1, FP2, FP3, qualifying and the race; sprint weekends use
their actual sprint qualifying/shootout and sprint sessions. Qualifying results
include Q1/Q2/Q3 times when available.

Each session is loaded once with all FastF1 channels enabled. The same loaded
session supplies these persisted stages:

| Stage | Stored data |
| --- | --- |
| `results` | Meeting, session, drivers, teams, classifications and qualifying times |
| `laps` | Every provider lap, sectors, compounds, stints, pit markers and quality flags |
| `telemetry` | All available laps for all drivers, including pit/deleted/incomplete laps; no 6/25-lap cap |
| `map` | Every usable provider position sample, with no import downsampling |
| `context` | Weather and race-control messages; existing APIs derive pit events from stored laps |
| `metadata` | Session information, session/track status feeds and circuit corners/marshal locations |

Use `sessions[].race_session_id` with the existing session, lap, telemetry,
qualifying, analysis and replay APIs. Read additional downloaded circuit and
status information at `GET /api/v1/sessions/{session_id}/source-metadata`.
Derived analyses are computed from these stored facts; selecting analysis or
replay views does not require separate data downloads. Creating and controlling
a replay room remains a separate action.

Subsequent selections reuse the stored weekend and job without reloading the
provider. `cached` describes whether this selection reused the weekend record;
check `status` for readiness. `READY` means all stages completed, while `PARTIAL`
identifies missing provider coverage in the individual stage details. Download
success does not imply that the provider offers complete real-world telemetry.

Completed stages are checkpointed. Transient failures are retried by the durable
worker; successful stages and sessions are skipped. Lease heartbeats continue
during slow provider calls. Editors can use `POST /api/v1/weekends/{id}/retry`
to reattempt missing/failed stages after retry exhaustion. An already complete
weekend is reused. Existing `/jobs/{job_id}/cancel` supports cancellation by
editors; the next safe work boundary stops the import. Data already committed
remains readable.

Future sessions are reported as `UNAVAILABLE` with `SESSION_NOT_STARTED` and
are checked again on a selection after their scheduled start. Use an editor
retry to refresh partial coverage when later provider data becomes available.
The download includes public data exposed by the implemented FastF1 pipeline;
curated editorial content and private team data are not provider downloads.

Raw position storage uses `sample_interval_ms=0`. Replay presentation uses a
250ms display cadence for those imports. Legacy per-session endpoints remain
available for administration, but the client should use weekend selection for
the normal browsing flow.

## Keycloak owns login, roles and groups

1. Read `GET /api/v1/auth/config` for the public client ID and issuer discovery
   URL. Configure `KEYCLOAK_PUBLIC_CLIENT_ID` for the Flutter/web public client.
2. Use Keycloak's browser authorization-code flow with PKCE S256. Keycloak
   handles registration, sign-in, passwords, verification and sessions. The
   client validates OAuth state and OIDC nonce and exchanges the code using its
   PKCE verifier and registered redirect URI.
3. After login or token refresh, call `POST /api/v1/auth/session` with the
   Keycloak access token in the `Authorization: Bearer ...` header.
4. Use the returned local `profile_id`, identity, roles and groups. The backend
   also synchronizes verified identity when an active protected endpoint is used,
   so a missed login callback does not leave the local profile absent.

RacePulse stores the immutable Keycloak `sub`, username, email and verification
state, first/last name, realm-role snapshot, API-client-role snapshot, group
paths and sync timestamps. It keeps one profile per subject. The local display
name is a RacePulse preference and survives subsequent identity syncs.
Passwords, access tokens and refresh tokens are never stored in the local DB.
Group paths preserve case and hierarchy, such as `/Fans/Monza`.

Authorization uses verified Keycloak token roles. Group names and database
snapshots do not grant permissions. Roles belonging to other clients in
`resource_access` are ignored. Keycloak must expand composite/inherited roles:
configure `editor` to include `fan` and `admin` to include `editor` if that
hierarchy is desired. RacePulse no longer invents those implied roles itself.

Configure a **Group Membership** protocol mapper in a **default** client scope
or the public client's dedicated scope:

- Token claim name: `groups`
- Full group path: on
- Add to access token: on

The backend's `KEYCLOAK_GROUPS_CLAIM` defaults to `groups`. An explicit empty
group array clears the local snapshot; an omitted claim preserves a richer
previous snapshot. Enable the mapper to ensure group removals synchronize.
The profile/email and realm/API-client role mappers must also include the
required claims in access tokens. See the
[Keycloak administration guide](https://www.keycloak.org/docs/latest/server_admin/).

Changes made in Keycloak appear locally on the next fresh-token sync. Admin
`/users/admin/synchronize` and per-user synchronization also pull effective
realm roles and paginated group memberships from Keycloak. Existing protected
admin write routes apply changes in Keycloak first and then mirror them locally.
Disabled local profiles remain blocked even if an older access token is valid.
Keycloak groups are identity memberships; Fantasy competition groups retain
their existing domain model.

`POST /accounts/register` is deprecated and retained for older clients. New
clients should use Keycloak-hosted registration, followed by `/auth/session`.

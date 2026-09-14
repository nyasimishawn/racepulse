# RacePulse backend

RacePulse imports public Formula 1 timing data, stores normalized race facts,
and exposes analysis, replay, Fantasy, and curated editorial APIs.

This repository is an application baseline, not a claim of production
readiness. Production operation still needs reviewed infrastructure, a managed
Keycloak deployment, real secret management, backups, monitoring, and a data
licensing review.

## What runs here

- FastAPI serves the versioned HTTP and WebSocket API.
- PostgreSQL is the authoritative store for normalized timing facts, user
  profiles, jobs, Fantasy state, and curated content.
- Redis carries ephemeral replay state, real-time events, and durable worker
  delivery. A Redis message is never the only record of an import or score.
- A separate worker process executes queued imports and Fantasy jobs.
- FastF1 is used for permitted public timing and telemetry imports. Its cache
  is a local named Docker volume, never an image layer or repository asset.
- Keycloak is the credential and role authority. The default Compose stack
  treats it as an external service; an explicitly enabled local-only profile
  is available for development.

See the [race calendar and Paddock guide](docs/race-calendar.md),
[API guide](docs/api-guide.md),
[architecture](docs/architecture.md),
[data-source rules](docs/data-sources.md), and
[Keycloak operational checklist](docs/keycloak-user-backend-setup.md).

For the normal client flow, see
[weekend selection and Keycloak sign-in](docs/weekend-selection-and-login.md).
Selecting a circuit and year now queues one complete weekend download. Login
through Keycloak then call `/api/v1/auth/session` to mirror identity, roles and
groups in PostgreSQL. Apply migration `f9d0e1a2b3c4` before running this version.

## Prerequisites

- Python 3.13 for a host-based run.
- PostgreSQL and Redis for a host-based run.
- Docker Desktop with Docker Compose v2 for the container workflow.
- A Keycloak realm only when authenticated features are enabled.

Do not commit `.env`, Keycloak client secrets, database passwords, access
tokens, or FastF1 cache files. Start from the repository environment example,
create an untracked local `.env`, and use a secret manager or deployment
platform variables outside development.

## Local development without Docker

Create a virtual environment, install the existing dependency set, configure
an untracked environment file for PostgreSQL and Redis, and run migrations
before starting an API or worker process.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

In another terminal, start the durable worker after migrations have completed.

```powershell
python -m app.workers.job_worker
```

The development server listens on the local API port configured by the
application. `run.py` is also suitable for a local reload workflow, but it is
not the production process command.

## Docker Compose workflow

The Compose file is at `infra/docker-compose.yml`. It runs PostgreSQL, Redis,
the API, and the worker. PostgreSQL and Redis are internal-only; the API is
published on loopback by default. The FastF1 cache, PostgreSQL data, and Redis
persistence use named volumes.

Before the first run, create an untracked environment file containing at
least a PostgreSQL password. Compose derives a database URL using the
`postgres` host and the `POSTGRES_*` values; set `DATABASE_URL` only when you
need to override that generated URL. Use URL encoding if an override password
has reserved URL characters.

```text
POSTGRES_PASSWORD=<local-development-password>
# Optional override:
# DATABASE_URL=postgresql+psycopg://<user>:<url-encoded-password>@postgres:5432/<database>
```

Build and start the data services, apply migrations exactly once, then start
the API and worker.

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build postgres redis
docker compose --env-file .env -f infra/docker-compose.yml --profile migrate run --rm migrate
docker compose --env-file .env -f infra/docker-compose.yml up -d api worker calendar-sync
docker compose --env-file .env -f infra/docker-compose.yml ps
```

Check the API liveness endpoint from the host.

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v1/health
```

`/api/v1/health` and `/api/v1/health/live` are liveness checks. Use
`/api/v1/health/ready` for dependency readiness: it returns `503` until both
PostgreSQL and Redis can be reached. The API container health check uses the
liveness endpoint; a production traffic policy should use readiness as well.
PostgreSQL and Redis have native Compose health checks.

To stop the local stack while retaining named volumes:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml down
```

`docker compose down --volumes` removes local database, Redis, and FastF1
cache volumes. Use it only when deliberately discarding local development
state.

### Optional local Keycloak

Keycloak is external by default. To launch the convenience development
profile, set an untracked bootstrap-admin password and enable the profile.

```powershell
docker compose --env-file .env -f infra/docker-compose.yml --profile keycloak up -d keycloak
```

That service uses Keycloak development mode and is not a production topology.
Create the realm, public PKCE client, backend service-account client, roles,
audience mapper, and email setup using the Keycloak guide before turning on
authenticated API features.

## Environment and runtime configuration

Use [`.env.example`](.env.example) as a field inventory and keep the real
`.env` untracked. The separate
[Keycloak account example](docs/keycloak-user-accounts.env.example) lists the
server-only account-administration settings. Neither example is a production
secret source.

For a host run, configure `DATABASE_URL`, `REDIS_URL`, and
`FASTF1_CACHE_PATH`. For Compose, set `POSTGRES_DB`, `POSTGRES_USER`, and
`POSTGRES_PASSWORD`; it derives a URL whose host is `postgres`, or accepts a
`DATABASE_URL` override. The API and worker share the remaining application
configuration. Keep the FastF1 cache on the named volume or another private
runtime mount, never in Git or an image layer.

Set these operational values explicitly outside local development:

- `KEYCLOAK_ENABLED`, `KEYCLOAK_ISSUER_URL`, and `KEYCLOAK_AUDIENCE` for JWT
  validation. Keep `KEYCLOAK_ADMIN_CLIENT_SECRET` only in a server-side secret
  store when account administration or registration is enabled.
- `KEYCLOAK_PUBLIC_REGISTRATION_ENABLED` and
  `KEYCLOAK_SEND_VERIFICATION_EMAIL`; leave both false until the Keycloak
  realm, SMTP, rate limit, and recovery flow have been tested.
- `TRUST_PROXY_HEADERS=true` only behind a trusted proxy that strips and sets
  forwarding headers. Leaving it false makes rate limits key directly from the
  connection address.
- `REGISTRATION_RATE_LIMIT`, `REGISTRATION_RATE_WINDOW_SECONDS`,
  `EXPENSIVE_REQUEST_RATE_LIMIT`, and
  `EXPENSIVE_REQUEST_RATE_WINDOW_SECONDS` for the expected traffic profile.
- `JOB_STREAM_KEY`, `JOB_CONSUMER_GROUP`, `JOB_MAX_ATTEMPTS`, and
  `JOB_LEASE_SECONDS` consistently for every API and worker replica. Change a
  stream or consumer-group name only as a planned queue migration, not on one
  replica at a time.

`LOG_LEVEL` controls application logging. Request logs intentionally contain
only safe request metadata; do not enable debug logging in a way that captures
credentials, authorization headers, provider payloads, or cache paths.

## Migration lifecycle

Alembic is the only schema migration mechanism. Do not use
`Base.metadata.create_all` against a shared environment.

1. Back up a production database before a schema change.
2. Deploy the image containing the migration and application code.
3. Run `alembic upgrade head` once as a controlled migration job.
4. Confirm `alembic current` reports the expected revision.
5. Start or roll the API and worker only after the migration succeeds.

Avoid automatic migrations in every API replica: concurrent startup migrations
make failure handling and rollback less predictable. Downgrades require an
explicit operational decision and a verified backup.

For a deployment, build the application image, run the controlled migration
job, start API replicas without `--reload`, and start one or more independent
`python -m app.workers.job_worker` processes with the same database and Redis
configuration. Do not start a worker against a schema revision older than the
API that enqueues its jobs.

## Security and access policy

Public race data remains read-only. Fan features require an authenticated
active profile. Any active authenticated user can select a shared complete
weekend download; selection is rate limited and reuses stored data.
Expensive provider previews, individual imports, manual retries, replay-room controls,
Fantasy scoring/finalization, and editorial writes require editor or admin
authority. Account and role administration requires admin authority.

Keycloak validates identity and realm roles; RacePulse keeps an active-profile
record to reject a disabled user even while a previously issued access token
has not expired. Never put a confidential Keycloak client secret in Flutter,
browser storage, Docker images, logs, or source control.

Rate limits protect registration and expensive work. API logs must record
request metadata without recording Authorization headers, access tokens,
passwords, client secrets, or unredacted provider payloads.

## Verification commands

Run these from the repository root after setting up dependencies and the
database expected by the command.

```powershell
pytest -q -p no:cacheprovider
ruff check app tests --no-cache
alembic upgrade head
```

For a local API route smoke check, start the API and inspect the OpenAPI
document.

```powershell
Invoke-WebRequest http://127.0.0.1:8000/openapi.json
```

The CI workflow should run equivalent tests against PostgreSQL for migrations
and row-locking behavior. The lightweight unit suite alone does not prove a
production deployment is safe.

## Operations notes

- Keep production PostgreSQL and Redis on private networks with encrypted
  transport and backups appropriate to the recovery objectives.
- Run more than one worker only after verifying the queue consumer-group and
  lease settings for the deployment. Workers must be independently restartable.
- Treat Redis as delivery and replay infrastructure, not as the sole record
  of a user-facing job.
- Monitor failed jobs, retry exhaustion, worker lease recovery, migration
  failures, provider errors, database saturation, and API rate-limit events.
- Rotate service-account secrets, database credentials, and Keycloak signing
  keys according to the deployment policy.
- Keep public data provenance and partial-coverage disclaimers visible in API
  clients. Do not present imported seasons as complete historical coverage.

## Repository boundaries

The Flutter application is outside this backend implementation scope. Backend
contract changes are additive or versioned where possible so existing clients
continue to work. The live OpenAPI document is the source of truth for exact
schemas and available routes.

# Personalized race weekend alerts

Apply migrations with `python -m alembic upgrade head` and run the existing
durable job worker (`python -m app.workers.job_worker`). Its recovery tick checks
PostgreSQL for due alerts every `JOB_RECOVERY_INTERVAL_SECONDS` (30 seconds by
default). Redis remains the transport for other durable jobs. Alert rows in
PostgreSQL are the source of truth for scheduled and delivered alerts, so a
worker restart resumes pending deliveries.

All routes below use `/api/v1`, require an active Keycloak Bearer token, and
operate only on the authenticated user's profile.

| Route | Purpose |
| --- | --- |
| `GET /alerts/preferences` | List followed calendar weekends and selected alert types. |
| `PUT /alerts/preferences/{calendar_weekend_id}` | Replace the alert choices for one calendar weekend. |
| `DELETE /alerts/preferences/{calendar_weekend_id}` | Unfollow the weekend and cancel pending alerts. |
| `GET /alerts?limit=50` | List up to 100 delivered alerts, newest first. |
| `POST /alerts/{alert_id}/read` | Mark one owned, delivered alert read; repeat calls are safe. |

The `PUT` body has three booleans, all defaulting to `false`:

```json
{
  "session_soon": true,
  "fantasy_deadline": true,
  "schedule_change": true
}
```

`session_soon` fires 30 minutes before each scheduled session. The Fantasy
reminder fires 60 minutes before a linked meeting's prediction lock, using
the existing Fantasy calendar deadline rules. An unlinked calendar weekend
has no Fantasy reminder. Subscribing inside a reminder window schedules it
for the next worker tick if the session or deadline is still in the future.
The worker also picks up Fantasy deadlines when sessions finish importing
after a user follows a weekend.

Schedule changes are generated only for changes to weekend status or a
session's status, start time, or presence. An unchanged source check does not
generate an alert. Pending reminders are cancelled and replaced after a
reschedule, postponement, or cancellation. Delivered alerts remain in the
in-app feed. Each alert has a stable idempotency key, and a worker locks its
row before delivery. Failed delivery remains pending for a later tick.

The worker accepts a `PushDelivery` implementation through its
`push_delivery` constructor argument. Its `send(alert)` method should use
`alert.id` as a provider idempotency key and raise on failure. The default
`NoopPushDelivery` allows local development and in-app delivery without
Firebase credentials. Real device push still needs a provider adapter,
credential configuration, device token registration, and token lifecycle
handling. The in-app API does not depend on those pieces.

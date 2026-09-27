# Fantasy guest testing

In the development environment, Fantasy fans can make predictions without
Keycloak login. Guest sessions are isolated by an opaque key; each client must
store its own key and send it on subsequent Fantasy requests. The guest key is
a credential for that guest's predictions and private groups, so keep it out
of logs and source control.

1. `POST /api/v1/fantasy/guest-session` with no body. Save `guest_key` from the
   `201` response in device-local storage.
2. Send `X-Fantasy-Guest-Key: <guest_key>` on Fantasy prediction, dashboard,
   leaderboard, community, group, and race-list requests.
3. A Keycloak Bearer token still works for a signed-in fan and takes precedence
   over a guest key. Guest data is separate from signed-in account data.

The guest mode is enabled only when `ENVIRONMENT=development` and
`FANTASY_GUEST_MODE=true` (the default). Set `FANTASY_GUEST_MODE=false` to
restore fan login during development. Production and staging never accept
guest keys. Editor scoring, resolution, and finalization routes still require
the editor role. The Fantasy event stream is public in development guest mode.

Guest sessions currently have no account recovery or automatic conversion to a
Keycloak profile. Losing the stored key loses access to that guest's private
predictions and groups. This temporary flow can be retired when login returns.

## Create an open weekend for picks

Imported historical weekends have already passed their UTC question deadlines,
so their picks are locked. After migrations, seed a fictional future weekend in
the **same development database** used by the API:

```powershell
python -m scripts.seed_fantasy_simulation
```

For Docker Compose, rebuild the API image after pulling this command, then run:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build api
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m scripts.seed_fantasy_simulation
```

The command prints a race session ID and prediction path. It reuses the open
simulation weekend on subsequent runs. Once it nears its first deadline, it
creates a new weekend. The seeded FP1, qualifying, and race questions start
open; the six fictional drivers and three teams have no scored results.

With the API running locally, this PowerShell example makes one guest pick:

```powershell
$simulation = python -m scripts.seed_fantasy_simulation | ConvertFrom-Json
$guest = Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/fantasy/guest-session
$headers = @{ 'X-Fantasy-Guest-Key' = $guest.guest_key }
$base = "http://127.0.0.1:8000/api/v1/fantasy/races/$($simulation.race_session_id)"
$prediction = Invoke-RestMethod -Headers $headers "$base/prediction"
$body = @{ driver_id = $prediction.drivers[0].id } | ConvertTo-Json
Invoke-RestMethod -Method Put -Headers $headers -ContentType 'application/json' -Body $body "$base/questions/RACE_P1"
```

If the API runs in Docker, use the race ID printed by the Docker command in
`$simulation.race_session_id` instead of running the host seed command. The
guest key belongs to this local guest; keep it to view the saved entry later.

## Run a timed Fantasy simulation

Use the accelerated simulation to test locking and scoring in a few minutes.
Run it against the same development database as the API. It creates a new
fictional weekend each time, with known outcomes held back until each session's
UTC start time. Production and staging cannot run this command.

```powershell
$simulation = python -m scripts.simulate_fantasy start --minutes 2 | ConvertFrom-Json
$simulation
```

The output gives the race ID, prediction path, and three UTC lock times. With
the default interval, FP1 locks in two minutes, qualifying in four, and the
race in six. Make guest picks before each lock using the API example above,
substituting `$simulation.race_session_id`. At or after each lock, run:

```powershell
python -m scripts.simulate_fantasy release $simulation.race_session_id
```

The release command does nothing early and can be repeated. It publishes only
the outcomes whose deadline has passed, runs the normal Fantasy scoring
service, and after the race resolves the DNF question and finalizes group
rankings. The API itself rejects late picks automatically at each UTC deadline;
you do not need to call release for locking to happen.

Expected outcomes are FP1 Apex One; Q1 Apex One, Q2 Vector One, Q3 Orbit One;
race podium Vector One, Apex One, Orbit One; fastest lap Apex Two; top speed
Vector Two; retirement Orbit Two. Apex Racing and Vector Racing are team options.
The predictions endpoint shows the saved answers and points after release.
This fixture uses fictional results to test the flow; it does not alter an
imported historical weekend.

For Docker Compose, rebuild the API image, then run:

```powershell
$simulation = docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m scripts.simulate_fantasy start --minutes 2 | ConvertFrom-Json
docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m scripts.simulate_fantasy release $simulation.race_session_id
```

Use the printed race ID for later release calls. Run release again after the
next lock time if you want to inspect each scoring stage.

## Import historical weekends for Fantasy replay

With PostgreSQL, Redis and the durable job worker running, queue the complete
2025 season and 2026 rounds 1–13 (ending at Italy/Monza):

```powershell
.venv\Scripts\python.exe scripts\queue_fantasy_history.py
```

The command can be rerun. It reuses existing weekend downloads and queues only
missing weekends. Each weekend imports every scheduled practice, sprint,
qualifying and race session through the normal results, laps, telemetry, map,
context and metadata stages. The worker processes the queue in the background;
wait until a weekend has imported its session results before selecting it for
replay. Later 2026 rounds are not queued by this command.

To make picks testable while telemetry downloads continue, import session
classifications first:

```powershell
.venv\Scripts\python.exe scripts\import_fantasy_results.py --year 2026 --through-round 13
.venv\Scripts\python.exe scripts\import_fantasy_results.py --year 2025
```

This pass skips sessions that already have results. Restart a replay after its
race laps are imported to include lap-based questions.

To score fastest-lap and speed-trap picks sooner, import race laps separately:

```powershell
.venv\Scripts\python.exe scripts\import_fantasy_race_laps.py --year 2026 --through-round 13
.venv\Scripts\python.exe scripts\import_fantasy_race_laps.py --year 2025
```

These commands also skip races whose laps are already imported.

Check the number of classified sessions and completed full weekend jobs with:

```powershell
.venv\Scripts\python.exe scripts\fantasy_history_status.py
```

## Replay an imported weekend in the app

In development, the Fantasy screen has a **TEST REPLAY** bar above the tabs.
Select an imported race in the weekend picker, then choose **Start before FP1**.
Make your picks, select 60×, 600×, 3600×, or 14400×, and press **Play**. Pause
at any time. **Restart weekend** creates a fresh private replay. At 3600×, one
real second advances the clock one hour. Each session locks its picks at its
scheduled start and reveals its imported results after it ends. The replay
copies race entrants and scoring data into a separate meeting, leaving the
imported weekend unchanged. Replays belong to the fan who started them and are
excluded from the normal race list and season leaderboard.

The older Barcelona-only replay remains available for development databases
that have only its race classification. New replays of imported weekends use
their actual practice, qualifying, race, and lap data.

# Circuit dashboard and Head-to-Head

Both repositories use branch `Dashboard-Head-to-Head`. The dashboard is a
public, read-only analysis feature; it does not import data or write Fantasy
state. No additional database migration is required beyond the existing
profile migration on the parent feature.

## Dashboard API

- `GET /api/v1/dashboard/tracks`: imported circuit groups and available years.
- `GET /api/v1/dashboard/tracks/{meeting_id}?season_year=2026`: circuit history
  and season form. Optional `as_of` is a timezone-aware ISO timestamp, clamped
  to the present and the end of the requested season.

Only dated Race/Qualifying results before the cutoff are included. Sprints are
excluded. Track identity uses source, country and location; meeting name is the
fallback when location is absent. Different providers are not combined, and
layout changes or aliases cannot be resolved from the existing schema.

Driver totals distinguish race wins from grid P1, wins from other known grid
slots (including pit-lane grid 0), wins with unknown grid, podiums, stored race
points and qualifying poles. Missing points are explicitly flagged. Teammate
qualifying advantage is the mean of teammates' classified positions minus the
driver's position in the same session; positive means ahead. This does not
measure underlying car strength.

Team win estimates are **uncalibrated heuristic percentages**, not fitted or
backtested probabilities. The components are 60% selected-season race points,
25% circuit race wins, and 15% circuit qualifying poles of drivers in each
team's latest imported lineup. Each component adds one pseudo-count per team
and normalizes across teams in the latest imported race. Rounded percentages
may sum to slightly more or less than 100%.

Estimates are withheld until at least three season races have at least ten
result rows each, exactly one stored winner per race, and known team and point
data. The latest race must contain at least two teams. This minimum does not
prove full historical coverage. The latest imported date, sample counts, model
weights and coverage notes are returned so the client can display the limits.
Current form uses the latest imported lineup, not a verified live entry list.

## Head-to-Head API

`GET /api/v1/sessions/{race_session_id}/head-to-head?driver_a=44&driver_b=16`
requires two distinct, explicitly selected drivers in a Race session.

The existing response is extended additively:

- `race_result.positions_gained`: grid minus classified finishing position;
  null for unknown/pit-lane grid or missing finish.
- `qualifying_comparison`: latest Q3/Q2/Q1 segment with positive times for
  both drivers. Its gap is B minus A; qualifying positions remain separate.
- `sector_comparisons`: median clean sector times, sample sizes and winners;
  at least three samples per driver are needed to declare a sector winner.
- `pit_events`: paired/unpaired events inferred from lap timestamps. Pit-lane
  duration is not stationary service duration.
- `race_context`: up to 100 relevant global/selected-driver race-control
  messages, weather sample count, rainfall and missing-context flags.
- `telemetry_coverage`: clean laps with at least 50 complete, non-interpolated
  car telemetry samples (distance, time, speed, throttle and brake). Both lists
  must be nonempty before the UI offers comparison. The existing
  `/api/v1/lap-comparison/telemetry-overlay` then checks selected-pair distance
  and time coverage; the UI only renders traces when `eligible` is true.

The existing V1 comparison score remains backward compatible: race finish 3,
qualifying position 2, official **race** points 2, relative compound execution 2,
and consistency 1. Missing or tied categories award neither driver points.
`score_type=DRIVER_COMPARISON` and `fantasy_points_awarded=0` make the separation
explicit. This endpoint never changes Fantasy predictions, rewards or rankings.

## Flutter flow

Open **Dashboard** in desktop navigation or phone **More**. Select a circuit
and season, inspect history, and expand the estimate explanation. Head-to-Head
is a separate navigation destination and is linked from Dashboard. Choose a
Race through Sessions, return to Head-to-Head, select A and B, and compare.
Selections are cleared on a race change. Telemetry has separate lap selectors
and a coverage-gated load action.

Backend API tests generate the checked-in Flutter response fixtures, covering
the actual serialization contract. Production screens do not use these test
fixtures. Regression tests cover time cutoffs, missing points, qualifying
segments, pit-lane starts, sector samples, explicit selection, mobile layout,
and unavailable/insufficient telemetry.

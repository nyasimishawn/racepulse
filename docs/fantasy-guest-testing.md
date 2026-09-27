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

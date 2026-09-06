# RacePulse User Backend: Keycloak Setup

## 1. Keep the public desktop client public

Use the existing `racepulse-desktop` client for Flutter:

- Client authentication: off
- Standard flow: on
- Require PKCE: on, challenge method S256
- Valid redirect URI: `http://localhost:14100/callback`
- Valid post logout redirect URI: `http://localhost:14100/callback`
- Web origins: `http://localhost:14100`

Do not add a client secret to Flutter.

## 2. Create a server-only Keycloak client

Create a second OpenID Connect client named `racepulse-backend`.

- Client authentication: on
- Service account roles: on
- Standard flow: off
- Direct access grants: off
- Redirect URIs: empty

Copy its secret from Credentials into `KEYCLOAK_ADMIN_CLIENT_SECRET`.
This secret belongs only in the FastAPI `.env` file.

Set `KEYCLOAK_ADMIN_REALM_URL` to the realm's Admin REST base, for example
`https://identity.example/admin/realms/RacePulse`. This is distinct from
`KEYCLOAK_ISSUER_URL`, which is the OIDC issuer under `/realms/RacePulse`.
Set `KEYCLOAK_ADMIN_TIMEOUT_SECONDS` to a bounded value appropriate to the
network. The API obtains a client-credentials token server-side; Flutter never
uses this client or secret.

## 3. Give the service account the minimum admin roles

Open **Clients -> racepulse-backend -> Service account roles**.
Choose the `realm-management` client roles and assign:

- `query-users`
- `view-users`
- `manage-users`
- `view-realm`

Those roles let FastAPI create, find, update, disable, role-map, and
email Keycloak users. Do not give this client `realm-admin`.

## 4. Ensure realm roles exist

Under Realm roles, create the lowercase roles:

- `fan`
- `editor`
- `admin`

Make `fan` a default Keycloak role. Configure `editor` as a composite that
includes `fan`, and `admin` as a composite that includes `editor`. Keycloak
expands the hierarchy into access-token claims; RacePulse uses only the roles
actually present in a validated token. Configure group membership mappers and
the client login handshake using
[the sign-in guide](weekend-selection-and-login.md#keycloak-owns-login-roles-and-groups).

## 5. Fix the existing RacePulse API audience

The Flutter token must contain `RacePulse` in its `aud` claim.
Create the Audience mapper under:

`racepulse-desktop -> Client scopes -> racepulse-desktop-dedicated -> Mappers`

Set Included Client Audience to `RacePulse`, enable Add to access token,
save it, then sign out and sign in again. This is separate from the
new user backend and keeps API token validation secure.

Set `KEYCLOAK_AUDIENCE=RacePulse` to that exact audience value. Do not use the
public client ID as a substitute unless it is deliberately the API audience and
the mapper and backend validation have been changed together. The issuer URL,
audience, and access-token realm roles must be verified in a real token before
enabling protected endpoints.

## 6. Configure SMTP before email actions

Verification and password reset emails only work after SMTP is configured
in the Keycloak realm. Keep `KEYCLOAK_SEND_VERIFICATION_EMAIL=false`
during local development until that is ready.

Before enabling public registration, configure SMTP TLS, sender identity,
email templates, action-token lifetimes, and exact redirect URIs. Test both a
verification email and a password-reset email. A successful registration can
still report `verification_email_sent=false` when Keycloak cannot deliver the
email, so clients must surface that state rather than assuming delivery.

## 7. Production operational checklist

Use this checklist before enabling Keycloak-backed behavior outside local
development. It is intentionally more restrictive than the local Compose
profile.

### Realm, issuer, and token validation

- Serve the realm through HTTPS with a stable public issuer URL. The issuer
  configured in RacePulse must exactly match the issuer claim in access tokens.
- Set `KEYCLOAK_ENABLED=true` only when issuer and audience configuration is
  complete. Configure `KEYCLOAK_AUDIENCE` to the exact API audience and verify
  every client token has that value in `aud`.
- Confirm access tokens contain the `sub`, `iss`, `aud`, expiry, issued-at,
  Bearer token type, and required realm-role claims. Test against the actual
  OpenID Connect discovery and JWKS endpoints through the production network.
- Keep clocks synchronized across Keycloak, API, worker, and proxy hosts. Use
  a small, reviewed clock-skew tolerance rather than disabling time checks.
- Keep access-token lifetimes short enough that a Keycloak disable or role
  change converges promptly. Use Keycloak session revocation or logout for an
  urgent response; RacePulse also rejects a disabled active profile while an
  older token remains valid.

### Clients and redirect policy

- Keep the Flutter or desktop client public with authorization code flow and
  PKCE S256. Client authentication must remain off for that public client.
- Register only exact production redirect and post-logout redirect URIs. Do
  not use broad wildcard redirects or production localhost origins.
- Configure exact allowed web origins for each deployed client. Review them
  whenever a new frontend host is introduced.
- Verify the audience mapper and realm-role mapper are added to access tokens,
  not only ID tokens. Reauthenticate after mapper changes before testing.
- The Swagger client ID is optional and must be a public PKCE-capable client;
  it is not the backend service-account client.

### Backend service account

- Keep `racepulse-backend` confidential, service-account enabled, and unable
  to use browser redirects or direct password grants.
- Assign only the documented minimum `realm-management` roles. Review grants
  regularly and do not substitute `realm-admin` for a missing configuration.
- Store the backend client secret in a deployment secret manager or protected
  runtime variable. A local untracked `.env` is acceptable only for local
  development. Never put it in Flutter, source control, an image layer,
  container logs, tickets, or API responses.
- Rotate the secret on a schedule and after suspected exposure. Update one
  deployment instance at a time and verify service-account calls before
  revoking the old credential.
- Restrict network access to Keycloak admin endpoints to the backend and
  approved operations paths.

### Roles and account lifecycle

- Create the lowercase application roles `fan`, `editor`, and `admin` and
  configure their composites in Keycloak. Both direct and inherited grants
  must be present in access-token claims; the backend does not expand roles.
- Registration grants `fan`. Role grants, removals, disable, enable, password
  reset, and verification-email operations must be performed through protected
  admin workflows or approved Keycloak administration.
- When disabling a user, disable the Keycloak account and synchronize the
  RacePulse profile in the same operational action. Validate that a previously
  issued token is rejected by an active-profile check.
- Prefer the protected RacePulse admin status endpoint for routine changes so
  Keycloak and the local active-profile record update together. If a user is
  disabled directly in Keycloak, promptly run the corresponding controlled
  profile synchronization; otherwise a pre-existing local profile cannot
  reflect that external change until synchronization.
- Run a controlled reconciliation for Keycloak users when an external admin
  action may have changed enabled state or roles. Do not let a stale database
  role snapshot grant a permission beyond the current Keycloak token.

### Email verification and recovery

- Configure Keycloak SMTP with a real sender identity, TLS, and deliverability
  monitoring before setting `KEYCLOAK_SEND_VERIFICATION_EMAIL=true`.
- Test verification and password-reset links for each public client redirect
  URI. Set appropriate action-token lifetimes and ensure expired links have a
  safe recovery path.
- Decide whether email verification is required before fan activity and apply
  that policy consistently in Keycloak and RacePulse authorization rules.

### Transport, logging, and operations

- Terminate TLS at a trusted proxy or Keycloak itself and configure forwarded
  headers only from trusted proxy networks.
- Restrict database, Redis, and Keycloak administration networks. Encrypt
  managed-service connections where available and back up Keycloak realm
  configuration before material client or mapper changes.
- Monitor authentication failures, JWKS availability, service-account failures,
  verification-email delivery, disabled-account rejections, rate-limit events,
  and unexpected role changes.
- Never log Authorization headers, access or refresh tokens, passwords,
  confidential client secrets, or full Keycloak request bodies.
- Configure `TRUST_PROXY_HEADERS=true` only when the API is reachable solely
  through a trusted proxy that overwrites forwarding headers. Set explicit
  registration and expensive-work rate limits in the runtime environment and
  monitor `429` responses.
- Run the durable worker with the same database, Redis URL, `JOB_STREAM_KEY`,
  and `JOB_CONSUMER_GROUP` as the API. A Keycloak outage should not be hidden
  by a worker retry loop; account-administration actions must return a safe
  dependency failure until Keycloak recovers.

### Release verification

1. Sign in as a fan, editor, and admin and verify the expected hierarchy.
2. Verify a fan is denied an editor import or editorial action.
3. Disable a test profile and confirm its still-valid token is denied.
4. Confirm public race-data reads remain public and mutating endpoints are not.
5. Test registration rate limiting and a verification-email flow.
6. Test backend account administration with the minimum service-account roles.
7. Confirm the production OpenAPI document advertises the expected OAuth flow
   without exposing a confidential client secret.


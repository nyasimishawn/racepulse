# Driver and team profiles

Apply `alembic upgrade head` before starting the updated API. Revision
`a1b2c3d4e5f6` adds nullable short biography, avatar and detail fields to the
existing curated profiles. Existing imported data and biographies are retained.

## Browsing and details

All read routes are public, beneath `/api/v1`:

- `GET /drivers?query=...&limit=100&offset=0`
- `GET /teams?query=...&limit=100&offset=0`
- `GET /drivers/{id}` and `GET /teams/{id}`
- `GET /drivers/{id}/history` and `GET /teams/{id}/history`

Lists include identity fields, `short_bio` and `avatar`. Fetch subsequent pages
by increasing `offset` by `limit` until a page is shorter than the limit.
The maximum limit is 250. Search and ordering are applied before pagination.

Detail responses include the full `biography`, typed `details`, attribution,
and sourced notable moments. Driver profiles include `recorded_teams`; team
profiles include `recorded_drivers`. These associations come from imported
session results across all years; they are not a current-season roster.

Driver details support date/place of birth, nationality, debut year and official
website. Team details support full name, base, team principal, technical director,
chassis, power unit, first entry year and official website. Time-sensitive facts
are editor-maintained and share the profile's publication date and attribution.

History responses provide race entries, wins, podiums, points, best finish,
recorded laps led, and yearly totals. Use their `coverage` metadata when showing
statistics: imported results do not imply complete career totals. Team race
entries count driver result rows, not distinct Grands Prix.

## Editing content and 3D avatars

An authenticated editor can send `PUT /drivers/{id}/profile` or
`PUT /teams/{id}/profile`. Example driver payload (illustrative content and
asset URLs; replace with actual sourced content and hosted assets):

```json
{
  "biography": "A longer, source-backed biography of the driver.",
  "short_bio": "A brief introduction for the driver's profile card.",
  "details": {
    "nationality": "Example nationality",
    "debut_year": 2020,
    "official_website": "https://example.com/driver"
  },
  "avatar": {
    "model_url": "https://assets.example.com/driver.glb",
    "poster_url": "https://assets.example.com/driver.webp",
    "alt_text": "Driver wearing a racing suit and helmet",
    "credit": "Model by the commissioned artist",
    "auto_rotate": true
  },
  "source_url": "https://example.com/driver",
  "publisher": "Example Racing",
  "published_at": "2026-04-01T12:00:00Z",
  "confidence": "HIGH"
}
```

Short bios are limited to 500 characters. Avatar models must use an absolute
HTTP(S) URL with a `.glb` path; signed query strings are supported. The API stores
metadata and does not fetch, create, host or inspect model files. The asset
host must provide any CORS permissions needed by the client. Supply actual
self-contained GLB assets and appropriate artist credits.

The client should render the GLB in a 3D viewer with rotate/zoom controls, use
`poster_url` while loading or if rendering fails, and fall back to initials or
a team badge when no avatar exists. Use `alt_text` for accessibility and disable
automatic rotation when the user's reduced-motion preference is enabled.

For backward compatibility, omitting `short_bio`, `avatar` or `details` from a
profile update preserves its existing value. Explicit `null` clears that field.
A supplied avatar/details object replaces that entire object. Required biography
and attribution fields retain the existing PUT contract. Imported entities with
no curated profile return null content; no biographies or career facts are
invented. This backend checkout does not contain the Flutter screens or a 3D
renderer; `flutter_app` is empty.

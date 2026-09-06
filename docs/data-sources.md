# RacePulse data-source rules

RacePulse distinguishes imported public timing facts, derived analysis, and
editor-curated content. A response must make that distinction clear rather
than presenting every field as equally authoritative.

## Allowed source categories

| Category | Permitted use | Required provenance |
| --- | --- | --- |
| FastF1 and supported public timing providers | Import public session schedules, results, laps, timing, telemetry where exposed by the provider, weather, and race context. | Provider name, source identifiers, import time, session coverage, and quality flags. |
| Official FIA publications and race-control material | Editor-curated penalties, grid drops, regulatory, and race-control-related updates. | Official URL, publisher, published time if known, editor confidence, and data-quality metadata. |
| Official team publications | Editor-curated upgrade, driver, team, and operational updates. | Source URL, publisher, published time, confidence, and quality metadata. |
| Official Pirelli motorsport publications | Editor-curated tyre allocation, compound, and tyre-related updates. | Source URL, publisher, published time, confidence, and quality metadata. |
| Reputable public sources supplied by an editor | Contextual editorial updates only when the source can be cited and the confidence is appropriate. | Source URL, publisher, published time, editor, confidence, and quality metadata. |

RacePulse does not scrape F1 TV, private APIs, authenticated feeds, leaked
telemetry, paid-only content, or any endpoint that disallows the intended use.
It does not fabricate news, results, biographies, historical statistics, or
source links.

## Imported timing facts

Imported provider data is normalized into meetings, sessions, results, laps,
telemetry, map samples, weather, and race-control records. Import completion
does not mean every provider field was present.

Every import-aware response should expose or link to:

- provider and source identity;
- source session or meeting identifiers;
- imported-at time and import job state where relevant;
- coverage counts and partial or unavailable data;
- quality flags for generated, deleted, inaccurate, inferred, or missing
  timing samples;
- a clear distinction between observed timing and a calculated value.

FastF1 cache files are implementation artifacts, not a source of record. They
must not be downloaded by clients, committed, copied into a Docker image, or
used as a substitute for provenance.

## Derived analysis

Pace, tyre, attack, lift-and-coast, push/manage, replay alignment, and similar
features are computed from stored facts. A derived response must state or
expose enough metadata to show:

- the source session and eligible sample set;
- the filtering assumptions and time window where material;
- coverage or quality limitations;
- whether the conclusion is inference rather than an official classification.

For example, a speed-trap winner derived from imported lap data can be labelled
as a timing-data result or proxy depending on source coverage. It must not be
presented as an official record if the underlying source cannot establish that.

## Curated updates

FIA, team, and Pirelli updates are structured editorial records, not scraped
news articles. Editors create and correct them through protected CRUD routes.
Each published record needs:

- a meaningful category such as upgrade, penalty, grid drop, or race-control
  editorial update;
- concise editorial content that does not reproduce protected material;
- source URL and publisher;
- published time when supplied by the source;
- confidence and data-quality fields;
- creation and update attribution in the application audit trail.

If a fact is uncertain, the record should say so through its confidence and
quality fields. Linking to an original source is preferred over copying it.

## Driver and team profiles and history

Biography, notable moments, and editorial narrative belong in curated profile
records with per-field or per-record source attribution. Career and season
statistics are derived from imported RacePulse data only when the imported
coverage supports them.

Profile/history responses must include a coverage statement such as imported
seasons, session types, provider scope, and known gaps. They must not describe
partial imported data as complete career history. A statistic such as wins,
podiums, or laps led is only shown as a timing-derived value when its data
coverage is explicitly sufficient for the claim.

## Fantasy resolution provenance

Fantasy automatic resolution can use public imported timing when a question is
verifiable. The stored resolution identifies the source reference and quality
flags. DNF and uncertain classifications require editor verification before
scoring, even when a provider shows a tentative result.

Resolution corrections preserve the editor source reference and trigger an
idempotent re-score. Private-group podiums and standings are recalculated from
the corrected durable records rather than from an untracked manual adjustment.

## Quality and correction rules

Use explicit, machine-readable quality flags rather than hiding caveats in
prose. Common conditions include:

- partial provider coverage;
- public timing result;
- generated or inferred timing;
- missing or unavailable telemetry;
- editor verified;
- provisional or uncertain classification;
- source time unavailable;
- historical coverage incomplete.

Flags are descriptive, not a substitute for source provenance. Corrections
should retain the newer source reference, update the confidence or flags, and
recompute only the dependent derived data or Fantasy score. Never silently
rewrite a curated claim or imported timing fact without an attributable update.

## Retention and licensing

Operators are responsible for complying with provider terms, publisher rights,
privacy requirements, and applicable law. Keep only the data needed for the
product purpose, protect editorial account access, and review source terms
before broadening an importer. This document is an engineering policy, not
legal advice.

# W1-D architecture decision record (AUTHORED current design)

Status: AUTHORED current design, NOT acceptance or qualification. No full
W1 exit, provider qualification or independent acceptance is claimed.

## 1. Decision
Adopt an append-only, bitemporal instrument registry in
`python/instrument_master.py` beside the legacy crosswalk. Observations are
immutable with explicit validity and observation times; queries rebuild
state from known rows. The provenance in
`governance/instrument-master-snapshots.json` is the single source of byte
pins and reference paths.

## 2. Identity dimensions
The canonical ID derives from identity version, exchange, segment, ISIN and
trading series. Symbols, names and provider tokens are never identity
inputs. Provider keys are aliases carried as observations, so consumers
resolve tokens without adopting them as identity. Identity, conformance,
status and eligibility stay separate; one never implies another.

## 3. Valid time and observation time
Each observation carries a half-open validity interval
`[valid_from, valid_to)`: start inclusive, end exclusive. A null bound means
UNKNOWN, never a date inferred from the earliest snapshot. The legacy
crosswalk keeps its snapshot-derived `effective_from` field for backwards
compatibility; that value is an observation date, not a validity start.
Observation time is `observed_at` (source report) plus `ingested_at` (when
QUANT saw it). A correction is a new row; earlier queries still return the
original fact.

```text
market_date in [valid_from, valid_to) AND knowledge_time <= as_of
```

## 4. Provider coverage and conformance
Coverage is its own dimension: SINGLE_SOURCE or MULTI_SOURCE. Agreement
inside one provider is not cross-provider PASS. A single-source conformance
report is INCOMPLETE even when its fields match; coverage is reported on
the report, canonical instrument and registry state. Conflicts are never
hidden by a missing provider value.

## 5. Status separation and fail-closed eligibility
Provider trading status (ACTIVE/INACTIVE/UNKNOWN), exchange status
(OPEN/PREOPEN/SUSPENDED/UNKNOWN), exchange eligibility
(ALLOWED/DISALLOWED/UNKNOWN), data freshness
(FRESH/STALE/INVALID/UNKNOWN) and QUANT eligibility
(ELIGIBLE/NOT_ELIGIBLE) are independent fields. Eligibility requires an
affirmative value in every dimension: VERIFIED identity, MULTI_SOURCE
coverage, PASS conformance, ACTIVE provider trading, OPEN exchange,
ALLOWED exchange eligibility and FRESH data. Unknown, stale, conflicting
or single-source evidence returns NOT_ELIGIBLE.

## 6. Consumer API
```python
registry = im.build_registry(observations)
states = registry.as_of("2026-11-26", "2026-11-27T14:00:00Z")
state = registry.resolve(canonical_id_or_provider_alias, as_of)
coverage = registry.provider_coverage(canonical_id)
```
`resolve` accepts a stable canonical ID or provider alias and returns the
state known at that timestamp. `as_of` returns the whole universe at one
market date and observation time, including unknown effective dates.
Module-level forms of all three calls delegate to the registry.

## 7. Evidence hygiene
The report from `scripts/fetch_instrument_snapshots.py` uses tracked
reference-data bytes when present, stores only relative paths and never
embeds machine-local directories. `scripts/release_bundle.py` stages one
canonical `evidence/verification-report.json`; an assurance copy is a
pre-bundle historical input, excluded from staging and rejected by verify.
Tests in `tests/test_instrument_master.py` and `tests/test_release_bundle.py`
cover both behaviours.

## 8. Explicit limitations
No captured source establishes a validity start or end, so effective dates
are unknown and snapshots cover one market date only. No source feeds
exchange status, exchange eligibility or data freshness; those stay UNKNOWN
and nothing is eligible. Live consistency, historical backfill, durable
storage, cross-language consumers and full W1 exit are NOT IMPLEMENTED.
Nothing here authorises trading.

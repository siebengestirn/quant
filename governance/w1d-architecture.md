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
`[valid_from, valid_to)`: start inclusive, end exclusive. A null bound is
never inferred from a snapshot or from an observation timestamp. The legacy
crosswalk keeps its snapshot-derived `effective_from` field for backwards
compatibility; that value is an observation date, not a validity start.
Observation time is `observed_at` (source report) plus `ingested_at` (when
QUANT saw it). A correction is a new row; earlier queries still return the
original fact.

```text
market_date in [valid_from, valid_to) AND knowledge_time <= observed_at
```

Every selected value carries an explicit validity assessment. A known
interval requires `valid_to > valid_from`.

| State | Meaning | Proven |
| --- | --- | --- |
| `KNOWN_INTERVAL` | Both bounds attested, date inside interval. | Yes |
| `OPEN_ENDED` | Start attested, no end; date on or after start. | Yes |
| `UNKNOWN_INTERVAL` | Start absent; no date inferred. | No |

An observation whose known bounds exclude the market date is not selected at
all. An unknown-interval observation is selected with `UNKNOWN_INTERVAL` and
`proven_valid == False`; a state's `attribute_validity` reports the assessment
per attribute. `as_of(..., proven_valid_only=True)` excludes unknown-interval
rows. Reversed or equal bounds, naive timestamps and missing or malformed
provenance are rejected before any row is stored.

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
or single-source evidence returns NOT_ELIGIBLE. A `UNKNOWN_INTERVAL` state is
never proven-valid: consumers that require attested bounds use
`proven_valid_only` or reject `proven_valid == False`. No alias status,
knowledge time or ID collision can substitute for an affirmative gate.

## 6. Consumer API
```python
registry = im.build_registry(observations)
states = registry.as_of("2026-11-26", "2026-11-27T14:00:00Z")
proven = registry.as_of("2026-11-26", "2026-11-27T14:00:00Z",
                        proven_valid_only=True)
state = registry.resolve(canonical_id_or_provider_alias,
                         market_date="2026-11-26",
                         observed_at="2026-11-27T14:00:00Z")
resolution = registry.resolve_alias(alias, "2026-11-26",
                                    "2026-11-27T14:00:00Z")
coverage = registry.provider_coverage(canonical_id)
```
`resolve` takes market date and observation time as independent axes; the
market date is never derived from the observation timestamp. A lone timestamp
call is a legacy wrapper that derives the market date from it and is not a
bitemporal query. `as_of` returns the whole universe with each value's
validity assessment. Module-level forms of the queries delegate to the
registry.

Alias resolution is fail-closed and reports `RESOLVED`, `NOT_FOUND`,
`NOT_YET_VALID`, `EXPIRED`, `AMBIGUOUS` or `TOKEN_REUSE` with the claimant IDs.
Only `RESOLVED` yields a canonical ID:

- an expired or not-yet-valid alias cannot resolve at that market date;
- overlapping claimants return `AMBIGUOUS` and no arbitrary selection;
- sequential claimants return `TOKEN_REUSE`, also listed by
  `detect_token_reuse()`;
- unknown validity is preserved as `UNKNOWN_INTERVAL` and never converted
  into an asserted bound.

## 7. Snapshot conversion
`observations_from_snapshots()` parses the digest-pinned reference snapshots
into append-only observations. The snapshot `market_date` is recorded as a
provider-scoped observation attribute (evidence of what was observed when),
not as `valid_from`: no source establishes an effective date, so both validity
bounds remain unknown. Every row carries the snapshot digest, version and
observation timestamp; unknown effective dates stay unknown in the registry.

## 8. Evidence hygiene
The report from `scripts/fetch_instrument_snapshots.py` uses tracked
reference-data bytes when present, stores only relative paths and never
embeds machine-local directories. `scripts/release_bundle.py` stages one
canonical `evidence/verification-report.json`; an assurance copy is a
pre-bundle historical input, excluded from staging and rejected by verify.
Tests in `tests/test_instrument_master.py` and `tests/test_release_bundle.py`
cover both behaviours.

## 9. Explicit limitations
No captured source establishes a validity start or end, so converted
effective dates are unknown and snapshots cover one market date only. No
source feeds exchange status, exchange eligibility or data freshness; those
stay UNKNOWN and nothing is eligible. Live consistency, historical backfill,
durable storage, cross-language consumers and full W1 exit are NOT
IMPLEMENTED. Nothing here authorises trading.

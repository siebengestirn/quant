# Data authenticity vocabulary and quarantine state

This is the governed vocabulary for describing where QUANT research input
data came from. A classification is a declaration, not evidence: it must be
substantiated with an artifact digest before any real-source research
registration. A digest proves integrity, not origin. A matching digest shows
only that bytes are identical to an earlier copy. Digest-pinned public
snapshots are NOT independently authenticated, and no live qualification is
claimed here. Facts not observed remain `UNKNOWN` rather than inferred.

## Classification vocabulary

| Class | Meaning | Permitted use |
|---|---|---|
| `SYNTHETIC_TEST_FIXTURE` | Authored generated data for parser and schema tests | Tests only; never market evidence |
| `SIMULATED_MARKET_DATA` | Model-generated prices and series; no real orders | Diagnostics only; never real-source research |
| `OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT` | Digest-pinned public reference snapshot | Reference only; origin not independently authenticated |
| `PROVIDER_API_CAPTURE` | Response captured from a provider HTTP API | Research only with complete provenance and digest |
| `PROVIDER_STREAM_CAPTURE` | Messages captured from a provider stream | Research only with gap and recovery evidence |
| `EXCHANGE_OFFICIAL_CAPTURE` | Data obtained from an exchange official channel | Research only with complete provenance and digest |
| `DERIVED_DATASET` | Computed from other classes | Must inherit the weakest parent class; record the chain, do not auto-promote |
| `RESEARCH_OUTPUT` | Statistics, scores and registries computed from data | Reporting only; never input evidence |
| `UNVERIFIED` | Missing or unsubstantiated provenance | Never real-source research evidence |

Simulated and synthetic classes are not real sources. A reference snapshot is
reference-only and cannot be promoted to real-source evidence by a digest.
A derived dataset inherits the weakest classification of its parents.

## Mandatory provenance fields

Every capture record declares all of these fields. A field that was not
observed is recorded as `UNKNOWN`; it is never guessed from context.

| Field | Purpose |
|---|---|
| `artifact_id` | Stable identity derived from provider, date and digests |
| `data_classification` | One vocabulary class from the table above |
| `provider_or_exchange` | Named origin, or `UNKNOWN` |
| `source_url` | Exact source location, or `UNKNOWN` |
| `capture_timestamp_utc` | When the bytes were captured |
| `provider_updated_timestamp` | Provider update time, or `UNKNOWN` |
| `market_date` | Session or reference date covered by the artifact |
| `request_parameters` | Exact request inputs used for the capture |
| `http_status` | Observed response status, or `UNKNOWN` |
| `content_type` | Declared response content type |
| `content_length` | Observed response byte count |
| `etag` | Provider entity tag, or `UNKNOWN` |
| `last_modified` | Provider last-modified value, or `UNKNOWN` |
| `raw_sha256` | SHA-256 of the exact captured bytes |
| `decoded_sha256` | SHA-256 of the decoded artifact bytes |
| `schema_version` | Version of the capture record schema |
| `fetcher_commit` | Source revision of the fetcher, or `UNKNOWN` |
| `transformation_version` | Version of any decoding transformation |
| `parent_artifact_digests` | Digests of parent artifacts, or an empty list |
| `licensing_and_retention_status` | Entitlement and retention declaration |

Stream captures additionally record connection identity, subscription set,
provider sequence when available, receive sequence, first and last event
timestamps, connection gaps and recovery status.

## Four authenticity questions

Each record must answer all four questions. A digest answers only the third.

1. Origin: who produced these bytes and from which named source?
2. Capture: how and when were they obtained, with which exact request?
3. Integrity: do the exact bytes match the recorded SHA-256 digests?
4. Authority and fitness: is the intended use licensed, entitled and
   appropriate for the claim being made?

An unanswered question stays visible as `UNKNOWN` or `UNVERIFIED`. It is not
resolved by a matching digest, a passing parser test or a green CI gate.

## Historical experiment registry

The generated registry `data/assurance/experiments.jsonl` is HISTORICAL
evidence only. It was produced by synthetic self-tests and test contamination
and its records carry no dataset digests. It is QUARANTINED and
NOT_ACCEPTED_AS_RESEARCH. Every record is retained in the labelled quarantine
document written by `scripts/quarantine_experiments.py`; no record is deleted.
The release bundle stages that labelled document, never the raw registry.

## Honest limits

- Provider golden fixtures are SYNTHETIC_TEST_FIXTURE values for parser tests
  only; they are not market observations.
- Instrument masters are digest-pinned OFFICIAL_PUBLIC_REFERENCE_SNAPSHOT
  values; their origin is NOT independently authenticated.
- The historical price dataset is NOT supplied.
- Live provider evidence is NOT supplied.
- The research registry is QUARANTINED / NOT_ACCEPTED_AS_RESEARCH.
- No alpha acceptance is claimed.
- Decision: NO TRADE.

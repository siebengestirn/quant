# QUANT dataset contract v2 — language-neutral specification

This document is the single authoritative definition of the dataset snapshot
contract shared by the C++ and Python implementations. It deliberately avoids
relying on either language's JSON serialiser for digest inputs.

## 1. Files

A dataset directory contains:

| File | Purpose |
|---|---|
| `contract.json` | Frozen expectations (the authoritative snapshot contract) |
| `manifest.json` | Per-file identity and realised coverage, derived from `contract.json` |
| `<symbol>.csv` | One canonical OHLCV series per expected symbol |

Both JSON files are UTF-8, object-only, and contain no duplicate keys.
Consumers must reject unknown keys rather than ignore them.

## 2. Canonical expectations

The expectations object has exactly these fields:

| Field | Type | Rule |
|---|---|---|
| `schema_version` | unsigned integer | must be `2` |
| `snapshot_id` | string | non-empty, `[A-Za-z0-9._:-]+` |
| `calendar_id` | string | non-empty, `[A-Za-z0-9._:-]+` |
| `generated_at_ms` | unsigned integer | ingestion time, epoch milliseconds |
| `provenance` | string | non-empty, `[A-Za-z0-9._:-]+` |
| `symbols` | array of strings | sorted, unique, `[A-Z0-9 .&/_-]+` |
| `sessions` | array of strings | sorted, unique, canonical `YYYY-MM-DD` |
| `first_date` | string | canonical date |
| `last_date` | string | canonical date, `>= first_date` |
| `min_rows` | unsigned integer | `>= 1` |
| `instrument_ids` | object | exactly the `symbols` keys; values non-empty `[A-Za-z0-9._:-]+` |
| `storage_keys` | object | exactly the `symbols` keys; values `s[0-9a-f]{31}` derived from permanent identity |

The canonical byte string for hashing is built in this exact order, with a
single trailing newline after every line and no spaces around `=` or `,`:

```text
QUANT-DATASET-CONTRACT-V2
schema_version=2
digest_algorithm=sha256
snapshot_id=<snapshot_id>
calendar_id=<calendar_id>
generated_at_ms=<generated_at_ms>
provenance=<provenance>
symbols=<comma-joined sorted symbols>
sessions=<comma-joined sorted sessions>
first_date=<first_date>
last_date=<last_date>
min_rows=<min_rows>
instrument_ids=<symbol>:<id>,... sorted by symbol
storage_keys=<symbol>:<key>,... sorted by symbol
```

`contract_digest` is the lowercase hex SHA-256 of those exact bytes. SHA-256
is the only accepted digest algorithm; `digest_algorithm` in `manifest.json`
must equal the literal string `sha256`, and an unknown value is a hard failure.

The storage key is the sole physical filename. It is derived from the permanent
instrument identity, never from the provider symbol:

```text
key = "s" + first 31 hex characters of SHA256(
        "QUANT-DATASET-STORAGE-V1\n" + instrument_id)
```

Symbols and provider identifiers are logical aliases only. No external
identifier may appear in a filesystem path. Both languages reject any contract
whose `storage_keys` do not match this derivation exactly.

## 3. Manifest

`manifest.json` has exactly these top-level fields:

| Field | Type |
|---|---|
| `schema_version` | unsigned integer `2` |
| `digest_algorithm` | string `"sha256"` |
| `contract_digest` | string, lowercase hex SHA-256 |
| `contract_sha256` | string, lowercase hex SHA-256 of the exact `contract.json` bytes |
| `expectations` | object, identical to `contract.json` |
| `files` | object keyed by exactly the expected symbols |

Each entry in `files` has exactly:

| Field | Type | Rule |
|---|---|---|
| `digest` | string | SHA-256 of the exact CSV bytes |
| `rows` | unsigned integer | data rows, excluding the header |
| `first_date` | string | first observed session date |
| `last_date` | string | last observed session date |
| `instrument_id` | string | must equal `expectations.instrument_ids[symbol]` |
| `raw_digest` | string | SHA-256 of the archived provider response, or `""` |

## 4. Verification outcomes

`COMPLETE` requires **all** of the following; any violation is an error:

1. `contract.json` and `manifest.json` parse and contain only known fields.
2. `schema_version == 2`; `digest_algorithm == "sha256"`.
3. The manifest's `expectations` equal `contract.json` byte-for-byte in
   canonical form and both recompute to the declared `contract_digest`.
4. `contract_sha256` matches the exact `contract.json` bytes.
5. `files` keys equal `symbols` exactly — no missing, extra or unknown entries.
6. For every symbol, the CSV digest and row count match the manifest.
7. `rows >= min_rows` and `rows > 0` (a header-only file is never COMPLETE).
8. The CSV session dates are exactly the declared `sessions`; missing or extra
   sessions indicate a stale or mixed generation.
9. `first_date == sessions[0]` and `last_date == sessions[-1]`, and both match
   the manifest file identity.
10. All JSON objects are free of duplicate keys; arrays are already sorted and
    unique; `manifest.expectations` equals `contract.json` field for field.

`PARTIAL` means some files verified but at least one symbol failed.
`FAILED` means no file verified. `BLOCKED` means the contract itself could not
be trusted (missing, malformed, mismatched, unknown schema or algorithm).
A decision-support path must require `COMPLETE`; `PARTIAL` is available only as
an explicitly labelled diagnostic mode.

## 5. Limits

This contract proves snapshot identity and completeness relative to its frozen
expectations. It does **not** prove exchange-calendar correctness, permanent
instrument identity across symbol changes, provider streaming correctness, or
physical power-loss durability. Those remain separate, unclaimed gates.

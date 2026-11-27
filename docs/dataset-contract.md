# QUANT dataset contract v2 — language-neutral specification

This document is the single authoritative definition of the dataset snapshot
contract shared by the C++ and Python implementations. It avoids
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

`PARTIAL` means at least one file verified and at least one symbol failed.
`FAILED` means no file verified. `BLOCKED` means the contract itself could not
be trusted (missing, malformed, mismatched, unknown schema or algorithm).
A decision-support path must require `COMPLETE`; `PARTIAL` is available only as
an explicitly labelled diagnostic mode.

## 5. Limits

This contract proves snapshot identity and completeness relative to its frozen
expectations. It does **not** prove exchange-calendar correctness, permanent
instrument identity across symbol changes, provider streaming correctness, or
physical power-loss durability. Those remain separate, unclaimed gates.

## CSV schema version

The expectations object carries `csv_schema`:

- `v2`: `date,open,high,low,close,volume` (legacy, open interest unavailable).
- `v3`: `date,open,high,low,close,volume,open_interest`.

An absent field means `v2` for backward compatibility; new provider snapshots
must declare `v3`. The canonical contract digest includes `csv_schema`.

The canonical open-interest contract is identical for the C++ and Python
consumers:

```text
open_interest >= 0 : available value
open_interest == -1: unavailable
open_interest < -1 : invalid
```

Every value must be integral and strictly less than `2^63`. Genuine zero is
distinct from unavailable, and a missing `v2` column is not equivalent to a
`v3` zero. Provider JSON integer tokens are preserved exactly: signed and
unsigned integer tokens are converted only after the `int64_t` bound check,
and a floating-point token is accepted only when its integral value is inside
the exactly representable `2^53` range. A larger floating-point token is
rejected instead of being silently rounded. Canonical CSV readers in both
languages parse the seventh field as a bounded decimal integer.

Volume is a non-negative integral count. It is carried as a double for
analytics and is therefore exact only up to `2^53`; fractional volumes and
counts above that bound are rejected by the provider adapter and both
canonical CSV readers. Derived depth calculations must respect the same
bound before converting a count to an integer.

## Provider timestamp policy

Upstox `timestamp` is ISO-8601 and `last_trade_time` may be a millisecond
value. Both are parsed according to their documented type. The accepted ISO
grammar is:

```text
YYYY-MM-DDTHH:MM:SS[.1-to-3-digits](Z|z|+HH:MM|-HH:MM)
```

Every field is checked for exact digits and range before any calendar table
is indexed or `timegm` is called: month is validated before the days-in-month
lookup, the day against the actual month and leap year, hours 0-23, minutes
0-59, seconds 0-59. Leap seconds are rejected, not normalised into the next
minute. A decimal point requires one to three digits; greater precision is
rejected rather than silently truncated. A numeric offset requires the colon
and ranges 0-23 hours and 0-59 minutes. Trailing characters are rejected.

A timestamp is accepted when it is positive, within five minutes of future
skew and no more than ten years in the past. Invalid dates, invalid timezone
offsets, empty strings, nulls and unparseable values are rejected.

Fail-closed rule: if either the provider update timestamp or the last-trade
timestamp is invalid, both are zeroed and the quote is marked
`invalid_timestamp`. Freshness is then UNKNOWN, never inferred from local
time. This is an explicit policy, not an omission. A provider update without
a trade is not treated as equivalent to a malformed trade timestamp once the
quote is accepted as `invalid_timestamp`; the rule applies to validation
failure only.

Five distinct times are recognised: provider update, exchange trade, last
trade, local wall-clock receipt and local monotonic receipt. `Quote` stores
`receive_timestamp_ms` from the steady monotonic clock for elapsed-time
measurement and `receive_wall_clock_ms` from the system clock for operational
audit chronology. The monotonic value is never used as a UTC event timestamp.
Both receipt values are written to the durable journal. The q `quotes` table
records the monotonic receipt as `recvNs` in nanoseconds for latency analysis;
that field is not a UTC timestamp and must never be interpreted as one.

# Provider timestamp policy

Upstox V3 `timestamp` is ISO-8601; `last_trade_time` may be a millisecond
value. Both are parsed according to their documented type. Provider update
time, exchange trade time, local wall-clock receive time and local monotonic
receive time remain distinct.

A timestamp is accepted when it is positive, within five minutes of future
skew and no more than ten years in the past. Fractional seconds beyond
milliseconds are truncated. Invalid dates, invalid timezone offsets, empty
strings, nulls and unparseable values are rejected.

Fail-closed rule: if either the provider update timestamp or the last-trade
timestamp is invalid, both are zeroed and the quote is marked
`invalid_timestamp`. Freshness is then UNKNOWN, never inferred from local
time. This is an explicit policy, not an omission. A provider update without
a trade is not treated as equivalent to a malformed trade timestamp once the
quote is accepted as `invalid_timestamp`; the rule applies to validation
failure only.

# Risk authorization capability model

`RiskAuthorization` is an opaque, private-constructible capability issued by
`RiskEngine`. It binds account, permanent instrument identity, venue, listing
identity, order identity, side, quantity, limit price, maximum notional,
risk-policy version, risk-state revision, market-data source and sequence,
expiry and a one-time consumption identity.

The signature is SHA-256 over a canonical field string with an
implementation-unit constant. This provides an **in-process capability**: it
prevents accidental caller construction and accidental field mutation. It is
not a cryptographic MAC against a hostile process or plugin able to inspect
the binary. If authorization crosses a process or machine trust boundary, a
keyed MAC or digital signature with protected key custody is required.

Risk-state verification, one-time consumption and the order transition are
serialized under a shared recursive mutex in `main`; the same lock is used by
the risk engine when it changes risk state. This prevents a kill-switch change
from interleaving with approval in the current single-process design.
Distributed authorization requires a transactional or lease/fencing protocol
and is not claimed here.

# Live-execution compliance gate — 26 November 2026

## Decision

Live Zerodha order placement remains **DISABLED**. No API key, static IP,
order endpoint, fund transfer or broker configuration change has been made.
This document records why a working order endpoint would not by itself be
sufficient authority to enable live trading.

## Current regulatory position (verified 2026-11-26)

NSE Circular **NSE/INSP/77559**, Circular No. **58/2026**, dated
**20 November 2026**, "Compliance with the guidelines on 'Safer participation
of Retail Investors in Algorithmic trading'", took effect immediately. It
states that the retail algo framework has been in force since 1 April 2026
and addresses misuse of the static-IP facility by empanelled algo providers
purporting to act for "tech-savvy" retail clients.

The circular's substance, as published in the NSE archives and summarised by
multiple independent trackers, is:

- Static-IP access under the tech-savvy provision is intended only for retail
  investors who develop their own algorithms using programming knowledge.
- Trading Members must obtain a written undertaking from such clients,
  including acknowledgement of responsibility for losses arising from the
  client's own algorithm logic.
- Static IPs may not be supplied to ordinary retail clients by an empanelled
  algo provider as a means of claiming the tech-savvy exemption.

Sources checked:

- NSE archives PDF: `https://nsearchives.nseindia.in/content/circulars/INSP77559.pdf`
- NSE exchange communications: `https://www.nseindia.com/resources/exchange-communication-circulars`
- Independent summaries were cross-checked via web search on 2026-11-26.

Zerodha's published API support material also documents a static-IP
requirement for API-based order placement. That is a connectivity
requirement, not a regulatory eligibility determination.

## Required before any live route is designed

Written confirmation from Zerodha compliance is required for:

1. Which retail-algo category applies to this system.
2. Whether QUANT qualifies as a client-developed algorithm.
3. Where strategy logic must execute.
4. Where order messages must originate.
5. Static-IP and API-key mapping.
6. Required undertakings, registration and tagging.
7. Applicable order-rate limits and surveillance obligations.

A cloud server, self-hosted strategy and third-party algo-provider
arrangement are **not** interchangeable with a genuine tech-savvy client
arrangement. None is assumed here.

## Cost-model scope

The Zerodha equity schedule used by the cost engine matches the published
headline rates for brokerage, STT, NSE transaction charges, SEBI charges, GST
and buy-side stamp duty. The ₹15.34 per-scrip DP charge is published, but the
schedule documents account-specific variations (including a demat discount
for eligible female first holders). DP is therefore reported as
`ESTIMATED` and contract-note reconciliation remains **NOT IMPLEMENTED**.

## State

| Item | Status |
|---|---|
| Live order placement | DISABLED |
| Order endpoint | LOCKED (`ZerodhaGateway::Submit` returns `LOCKED`) |
| Regulatory eligibility | NOT ESTABLISHED |
| Static IP / API mapping | NOT CONFIGURED |
| Contract-note cost reconciliation | NOT IMPLEMENTED |
| Trading decision | NO TRADE |

## Integrity checksum qualification

The journal and checkpoint digests in QUANT are unkeyed 64-bit FNV-style
hashes. They detect accidental corruption and inconsistent checkpoint state.
They are **not** cryptographic MACs or signatures: an actor able to rewrite
the files can recompute a consistent-looking digest. Do not describe the
audit trail as tamper-proof or adversarially authenticated. If malicious
local modification enters the threat model, a keyed MAC with documented key
custody or a signed append-only audit chain is required.

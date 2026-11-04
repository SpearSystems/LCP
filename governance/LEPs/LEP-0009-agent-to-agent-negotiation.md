# LEP-0009 — Agent-to-agent negotiation: the `quote` and `counter` message types

- **Title:** Agent-to-agent negotiation: bounded `quote`/`counter` price discovery
  as core message types
- **Author(s):** LCP maintainers
- **Date:** 2026-11-04
- **Status:** **Accepted** — accepted 2026-11-04 by the maintainer (D8 of
  [`docs/DECISIONS-v1.2.md`](../../docs/DECISIONS-v1.2.md)); built under the D1
  trigger waiver, so **no adopter evidence is claimed** (see "Problem statement
  and evidence")
- **Target version:** v1.2.0 (MINOR). Contingency **v1.3.0**, recorded in
  `docs/DECISIONS-v1.2.md` "Release consequence": if this wire surface is not
  complete when v1.2.0 ships, it moves to v1.3.0 — the LEP record, not the
  release note, decides that outcome
- **Decision record:** `docs/DECISIONS-v1.2.md` D8; registry row
  [`governance/LEP-REGISTRY.md`](../LEP-REGISTRY.md); delivery lane
  `docs/V1.2-EXECUTION-PLAN.md` W4-08 + W5-01…W5-04
- **Re-evaluation date:** 2027-05-04 (D1 compensating constraint 3)
- **Discussion:** none recorded. The decision is the dated D8 entry; this
  record is the durable rationale. No issue number is invented here.
- **Implementation status:** none. No schema, platform route, SDK model, MCP
  tool, or vector exists for this surface as of this record.

## Decision (maintainer, 2026-11-04)

**Accepted** as a MINOR, additive, capability-gated surface, on the terms of
D8:

| # | Contract element | Normative rule (this record) |
|---|---|---|
| C1 | New types | `quote` and `counter` join the **closed** `message.type` enum (SPEC.md §6). Absence of a type is unchanged; unknown types remain `LCP-006`. |
| C2 | Capability gating | A sender MUST NOT emit either type, nor `bid.negotiation_binding_token`, nor `ack.negotiation`, unless the receiver's `capabilities().features` advertises `negotiation` and its parsed-semver `lcp_versions` opens the v1.2 gate (Compatibility, below). |
| C3 | Off by default | `LCP_FEATURE_NEGOTIATION` defaults to `off` in the reference platform: no advertisement, no intake, no state written. CI exercises the surface with the flag on. |
| C4 | Ping-safe | Integer minor units, ISO 4217 currency, ids, timestamps, round counters, closed reason values. No consumer PII, no contact hash, no free text. Strict allowlist (`additionalProperties: false`). |
| C5 | Bounded rounds | `max_rounds` default **3**, hard cap **5**; `rounds_remaining` counts buyer counters; a counter at zero is refused with `round_limit_exceeded`. |
| C6 | Monotone | Every `counter.price_cents` MUST be ≥ the sender's own most recent price in that negotiation (strictly greater for the buyer), so no message ever moves the price against the platform. Never below the floor; below floor is **refused, never clamped**. |
| C7 | Binding token | An `accept` produces a single-use `binding_token` that replaces `bid_price_cents` on the buyer's subsequent `bid`. Replay of the same redemption returns the original outcome; no second bid, no second payable. |
| C8 | Absolute expiry | `expires_at` is absolute, restated on every message, and MUST NOT exceed the ping's TTL. Past expiry the negotiation is `EXPIRED`; late messages are refused `negotiation_expired`. |

Conditions of acceptance (the "Traceability and implementation gate" of
`governance/LEP.md`):

1. Every wire artifact lands with the D1 evidence stand-in: the vector suite
   `test-vectors/negotiation.json` (`NEG-001`…) plus the platform tests named
   under "Tests". A benchmark is not required because no throughput claim is
   made.
2. `schemas/` stays the only normative source: both payload schemas, the
   `envelope.json` enum addition, the `bid.json` token field, and the
   `ack.json` `negotiation` object are edited there first, then generated or
   verified downstream (10 SDKs, OpenAPI, MCP, vectors).
3. Any deviation from the wire shape in this record returns it to Review
   before merge.
4. The registry row keeps the 2027-05-04 re-evaluation date until adopter
   evidence replaces the waiver.

## Problem statement and evidence

LCP has exactly one price-exchange round. A ping advertises a lead to
prospective buyers with `floor_price_cents` and `expires_at`
(`schemas/ping.json`), a buyer answers once with `bid{decision,
bid_price_cents}` (`schemas/bid.json`), and the platform selects the winner
("The platform collects bids within the ping expiry window"; SPEC.md §4).
There is no second round, no seller counter, and no way for a machine buyer to
converge on a price it could not state in one shot.

That is a structural gap for automated buyers, and it shows up in three
concrete ways today:

1. **A buyer cannot improve a losing bid.** A bid that loses on price produces
   no further message ("losers receive no further messages" — the same
   paragraph states the property as a privacy feature). A pricing agent that
   would have closed at an intermediate price has no protocol turn in which to
   say so.
2. **The only price signal is binary.** `decision` is `accept | reject |
   pass`, and `reject_reason` is prose-shaped and open. A platform cannot
   distinguish "no interest" from "close on price" without reading a free
   string, which is exactly the kind of field a PII audit removes rather than
   extends.
3. **Buyer-side automation has no auditable price history.** Every negotiated
   price that happens off-protocol (email, chat, a sales rep's call) leaves no
   trail LCP can reconcile against `post.pricing`. Disputes then compare a
   delivered price with an e-mail thread.

**Evidence status — stated plainly.** This is built under the **D1 trigger
waiver**. The v1.1 trigger for this item
(`docs/V1.1-ROADMAP.md`, deferred-trigger table: "Agent-to-agent price
negotiation | Agent bidding volume large enough that static floor pricing is
suboptimal") was **not met**: no adopter reported agent bidding volume at that
scale, no interoperability test demanded a counter message, and no second
independent adopter asked for the surface. `governance/LEP.md` decision rules
require, before a new universal message type, "evidence from at least two
independent adopters or a documented interoperability necessity, plus an
extension feasibility analysis"; **neither the two-adopter evidence nor a
documented necessity exists**, and the extension feasibility analysis
(Alternatives, A and C) does not produce one. The maintainer waived the
trigger: D1 says the waiving record supersedes a Deferral "by maintainer
decision, not by new adopter evidence".

What this record does instead of inventing adoption: it names the
**deterministic evidence** that stands in for it — the executable vector suite
and the platform tests enumerated under "Tests", which fail loudly if rounds,
monotonicity, floor refusal, expiry, or token idempotency regress — plus the
dated re-evaluation at 2027-05-04, where a reader may challenge the decision
with actual usage data. The honest summary is: **the mechanism is built
because the maintainer chose to fund it, not because the need was proven.**

## Scope

- **Core protocol (additive MINOR):** two values added to the closed
  `message.type` enum (`quote`, `counter`); two new strict-allowlist payload
  schemas with integer-cent pricing and no PII; one optional `bid` field
  (`negotiation_binding_token`); one optional `ack` object (`negotiation`);
  one advertisement key in `capabilities().features` (`negotiation`). No
  envelope shape change, no new required field, no vertical-schema change, no
  error-code addition (refusals ride the existing open `rejection_reason`).
- **Reference platform:** negotiation state machine with audit, round and
  floor enforcement at one decision point, single-use token storage (hash
  only), expiry sweep, tenant authorization, metrics, and the
  `LCP_FEATURE_NEGOTIATION` switch — W4-08 and W5-01…W5-03.
- **SDKs, OpenAPI, MCP, conformance:** typed models and helpers in all ten
  SDKs, `POST /quotes` + `POST /counters` + redemption on `POST /bids`,
  `send_quote` / `send_counter` MCP tools, a new vector suite, coverage-matrix
  rows — W5-04 and the impact section below.
- **Not in scope:** pricing *strategy* (no normative guidance on how a party
  should choose a price — that is agent policy, deliberately not protocol);
  seller-side concessions (C6 forbids a platform counter below its standing
  ask; a cheaper price is a **new** negotiation, not a counter); ceiling
  semantics beyond the ping's floor (offer-side `max_allowed_price_cents`
  already exists for post-time guardrails and is unchanged); negotiation over
  non-ping surfaces (`post` reprice, resale, subscriptions); multi-currency
  conversion (D7 applies unchanged — a negotiation is single-currency and
  refuses a mismatch rather than converting); any change to `post.pricing`,
  payability, disputes, or refunds; a read/list HTTP surface (Deferred question
  Q6).

## Proposed wire shape

### 1. Envelope

| Element | Requirement |
|---|---|
| `message.type` | `"quote"` or `"counter"`. The enum stays **closed**; an unknown value is still `LCP-006 UNKNOWN_MESSAGE_TYPE` (SPEC.md §6: "Adding a message type is a MINOR version bump"). |
| `lcp.version` | `1.2.0` or a later minor. A receiver advertising `1.0.x`/`1.1.x` only is never a legal recipient (C2). |
| `message.correlation_id` | `quote`: the `message.id` of the ping being priced (required, non-null). `counter`: the `message.id` of the `quote`/`counter` it answers (required, non-null). The chain is the negotiation's audit spine. |
| `message.idempotency_key` | Required, exactly as for every other type. Same key + same bytes → the original ack; same key + different bytes → `LCP-005` (SPEC.md §4 idempotency rule). |
| `message.sender_id` / `receiver_id` | The platform and the pinged buyer, in one of the two directions. A third party is `LCP-002 UNKNOWN_SENDER` (authorization axis). |
| `message.security` | HMAC-SHA256 per SPEC.md §9, or the HTTP binding's `X-LCP-Timestamp`/`X-LCP-Signature`. Negotiation messages are never unsigned. |
| `message.test` | Honored; test negotiation traffic follows `LCP-013` and never mints a billable bid. |
| `payload` | Strict allowlist (`additionalProperties: false`) on both types. No free-text field of any kind is defined (no `note`, no `message`, no `buyer_reference`), because a free string is the PII-smuggling shape this surface must not carry. |

Both payloads are **ping-class**: they inherit the prohibition a ping carries
(§4 "PII: None"), so the ping PII code applies to them, and the D9 taxonomy
classifies every property `internal` (commercial terms) — never `contact`,
`restricted`, or `prohibited`.

### 2. `quote` — the platform opens a negotiation

Sent by the platform (seller) to a buyer it has already pinged. It states the
ask for a named ping and opens the bounded round structure.

| Field | Type | Req | Rule |
|---|---|---|---|
| `negotiation_id` | string | yes | Opaque platform-assigned id, `^neg_[A-Za-z0-9_-]{8,64}$`, unique per negotiation. The only lookup key a later message may use. |
| `ping_id` | string | yes | The ping this negotiation prices. The ping supplies the floor, the currency, and the TTL ceiling. A `ping_id` the receiver never sent that buyer is `LCP-002`. |
| `offer_id` | string | yes | The buyer offer being priced; MUST equal the ping's `offer_id` (`LCP-100` on mismatch). |
| `price_cents` | integer `minimum: 0` | yes | The ask in minor units of `currency`. MUST be ≥ `floor_price_cents`; below floor is refused with `rejection_reason: "below_floor_price"` and **never clamped**. |
| `currency` | ISO 4217 alpha-3 | yes | MUST equal the ping's `currency` (D7: no cross-currency comparison, no FX in a negotiation). Mismatch → `LCP-100`. |
| `floor_price_cents` | integer `minimum: 0` | yes | Restated from the ping so the buyer can verify the floor and the monotone rule without re-reading the ping. MUST equal the ping's value; mismatch → `LCP-100` (`field: "payload.floor_price_cents"`). |
| `max_rounds` | integer `1..5`, default `3` | no | Rounds this negotiation may run. Absent = `3` (D8 default). `> 5` → `LCP-100` (hard cap; not a deployment knob). |
| `rounds_remaining` | integer `0..5` | yes | Buyer counters still available at the moment this message is sent. On the opening quote this equals the effective `max_rounds` (a quote consumes no round). |
| `expires_at` | RFC 3339 UTC | yes | Absolute end of the negotiation. MUST NOT be later than the ping's `expires_at`, nor later than the ping's `timestamp` plus its TTL when only a TTL was expressed; either failure is `LCP-100` and no negotiation is created. |
| `rejection_reason` | string (open enum) | cond. | Present only on a **terminal** refusal message (no negotiation opened): `below_floor_price`, `round_limit_exceeded`, `negotiation_expired`, `unknown_negotiation`, `negotiation_closed`. When present, `price_cents` MUST be absent and the ack for the triggering message carries the same reason. |

### 3. `counter` — either party answers the standing message

Sent by the buyer or the platform, strictly alternating (no two consecutive
`counter` messages from one sender in one negotiation; a violation is
`LCP-004 INVALID_STATUS_TRANSITION` at the receiver, not a new code).

| Field | Type | Req | Rule |
|---|---|---|---|
| `negotiation_id` | string | yes | MUST name an existing negotiation for this (tenant, buyer, ping) triple; otherwise `unknown_negotiation` (or `LCP-002` when the negotiation belongs to another buyer/tenant). |
| `ping_id` | string | yes | MUST equal the negotiation's ping; mismatch → `LCP-100`. |
| `offer_id` | string | yes | MUST equal the negotiation's offer; mismatch → `LCP-100`. |
| `decision` | string, closed enum | yes | `counter` (move the price), `accept` (close at the price of the message being answered), `refuse` (close with a reason). |
| `price_cents` | integer `minimum: 0` | cond. | Required unless `decision = refuse`. `counter`: subject to the monotone rule below and ≥ `floor_price_cents`. `accept`: MUST equal the `price_cents` of the message it answers; any other value is `LCP-100`. |
| `currency` | ISO 4217 alpha-3 | yes | MUST equal the negotiation currency; mismatch → `LCP-100`. Carried on every message so a refusal is self-describing without a lookup. |
| `floor_price_cents` | integer `minimum: 0` | yes | Restated and checked against the negotiation's floor; mismatch → `LCP-100`. |
| `rounds_remaining` | integer `0..5` | yes | The sender's view of the buyer counters still available **at the moment this message is sent**, i.e. before it is applied. A stale value (not equal to the platform's stored value) is `LCP-100` — it is the cheap ordering/replay guard that makes out-of-order delivery fail instead of corrupting the count. |
| `expires_at` | RFC 3339 UTC | yes | The negotiation's absolute end, unchanged. MUST NOT exceed the value set by the opening quote; later → `LCP-100`. A message whose `message.timestamp` is at or after `expires_at` is refused `negotiation_expired`. |
| `rejection_reason` | string (open enum) | cond. | Required when `decision = refuse`; absent otherwise. Known values (open enumeration on the ack side already exists, `schemas/ack.json`): `below_floor_price`, `non_monotone_counter`, `round_limit_exceeded`, `negotiation_expired`, `unknown_negotiation`, `negotiation_closed`. |

**Monotone rule (normative, C6).** For one negotiation, let `p_sender` be the
sender's own most recent `price_cents` in that negotiation, or `null` when the
sender has not stated a price yet (the platform's opening quote is its
`p_sender` from then on):

- `p_sender` is `null` — the counter is bounded by the floor alone:
  `price_cents ≥ floor_price_cents`, so a buyer's opening counter may sit
  exactly on the floor (the floor rule below is what refuses anything lower);
- a buyer `counter` with `p_sender` present MUST satisfy
  `price_cents > p_sender` and `price_cents ≥ floor_price_cents`;
- a platform `counter` with `p_sender` present MUST satisfy
  `price_cents ≥ p_sender` (it may restate the ask, never lower it) and
  `price_cents ≥ floor_price_cents`;
- `accept` is not a counter and is not constrained by the monotone rule — a
  platform MAY accept any buyer price at or above the floor, which is how a
  concession is expressed (a concession is an `accept`, never a lower
  counter).

Two consequences, stated because they are the design and not an accident:

1. **The price path of a negotiation never decreases.** The buyer climbs; the
   platform restates. A buyer counter at or above the standing ask terminates
   the negotiation accepted at the **counter** price (the platform receives at
   least its ask — never silently downgrade the winner's price).
2. **A platform that wants to sell cheaper must open a new negotiation.**
   Seller-side price discovery downward is a pricing decision with its own
   audit trail; smuggling it in as a lower counter is what C6 forbids.

### 4. Round accounting and termination

- `max_rounds` is per negotiation; the effective value is the opening quote's
  `max_rounds` or `3` when absent, and never exceeds `5`.
- Only a buyer message with `decision: "counter"` consumes a round:
  `rounds_remaining` decrements by one when the platform accepts the counter
  into state. `accept` and `refuse` are terminal and consume nothing, and a
  refused message never moves the price or burns a round — the only mutation a
  refusal causes is the terminal `REFUSED` state itself.
- A buyer counter received when `rounds_remaining` is `0` is refused
  `round_limit_exceeded`; the negotiation moves to `REFUSED` and is never
  reopened. A new price discussion is a new `negotiation_id`.
- `accept` from either side is terminal-accepted: the platform records
  `ACCEPTED` at the accepted price and mints the binding token below.
- `refuse`, a floor violation, a monotone violation, an expiry, or round
  exhaustion all terminate `REFUSED` (or `EXPIRED`) with exactly one
  `rejection_reason`. There is no "retry the same negotiation" state: a
  refused negotiation id is permanently closed, and a later message naming it
  is refused `negotiation_closed`.

### 5. Binding token (C7)

An `accept` is agreement; it is not yet a bid. The platform answers the
accepting message with an ack carrying:

```json
{
  "negotiation": {
    "negotiation_id": "neg_7c1f0a9b2e4d",
    "outcome": "accepted",
    "price_cents": 10000,
    "currency": "USD",
    "rounds_remaining": 2,
    "binding_token": "tok_9fQ2x8Lm4Vb7Kd1Rt6Yp3Wc5Nz0Hs2Ja",
    "binding_expires_at": "2026-11-04T12:10:00Z"
  }
}
```

| Field | Type | Rule |
|---|---|---|
| `negotiation_id` | string | The negotiation this outcome belongs to. |
| `outcome` | string, closed enum | `opened`, `countered`, `accepted`, `refused`, `expired`, `redeemed`. Present on **every** ack for a `quote`/`counter` (and on the bid ack of a successful redemption), so a buyer never has to infer state from prose. |
| `price_cents` | integer | The accepted, countered, or refused price (absent on `refused` when the refusal carried no price). |
| `currency` | ISO 4217 | The negotiation currency. |
| `rounds_remaining` | integer | The post-message value. |
| `binding_token` | string, `^tok_[A-Za-z0-9_-]{32,128}$` | Present **only** when `outcome = "accepted"`. Single-use. Returned exactly once (the ack to the accepting message); the platform stores only a hash, mirroring `api_key_hash`, and never logs or echoes the token again. |
| `binding_expires_at` | RFC 3339 UTC | ≤ the negotiation `expires_at`. A token is unusable at or after this instant. |

**Redemption.** The buyer presents the token in a `bid` (new optional field,
`schemas/bid.json`): `negotiation_binding_token`, with `ping_id` equal to the
negotiation's ping, `decision: "accept"`, and `bid_price_cents` either absent
or exactly the token's price. Rules:

1. First redemption creates the bid at the token price and moves the
   negotiation to `REDEEMED`. The bid is then an ordinary bid for every
   downstream purpose (auction, post, payability) — the token is a price
   carrier, never a money shortcut. The acknowledgment for the redemption is
   the bid's normal ack with `negotiation.outcome: "redeemed"`, the redeemed
   price, and no `binding_token`.
2. A replay of the **same** redemption (same `idempotency_key`, same bytes)
   returns the original ack byte for byte, creates no second bid, and cannot
   produce a second payable.
3. A token presented after `REDEEMED`, or after `binding_expires_at`, is
   refused `negotiation_closed` — except the same-sender same-key replay in
   (2), which is "replay returns the original outcome", not a reuse.
4. A token presented by any other sender or tenant is `LCP-002`.
5. Token format or checksum failure is `LCP-100`; an unknown hash is
   `negotiation_closed` (never "invalid token" — that would confirm token
   existence).

### Valid example 1 — opening `quote` (platform → buyer)

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "a10e8400-0000-4000-8000-000000000901",
      "type": "quote",
      "timestamp": "2026-11-04T12:00:00Z",
      "sender_id": "platform_001",
      "receiver_id": "buyer_001",
      "correlation_id": "d70e8400-0000-4000-8000-000000000021",
      "idempotency_key": "platform-001-quote-neg_7c1f0a9b2e4d-1",
      "test": false
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "price_cents": 10000,
      "currency": "USD",
      "floor_price_cents": 6000,
      "max_rounds": 3,
      "rounds_remaining": 3,
      "expires_at": "2026-11-04T12:10:00Z"
    }
  }
}
```

*Valid because:* the ask is above the restated floor, `expires_at` equals the
ping's expiry (not later), `max_rounds` is within the hard cap, and the payload
carries no field outside the allowlist. `correlation_id` names the ping.

### Valid example 2 — buyer `counter` inside the bounds

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "a20e8400-0000-4000-8000-000000000902",
      "type": "counter",
      "timestamp": "2026-11-04T12:00:40Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": "a10e8400-0000-4000-8000-000000000901",
      "idempotency_key": "buyer-001-counter-neg_7c1f0a9b2e4d-1",
      "test": false
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "decision": "counter",
      "price_cents": 8000,
      "currency": "USD",
      "floor_price_cents": 6000,
      "rounds_remaining": 3,
      "expires_at": "2026-11-04T12:10:00Z"
    }
  }
}
```

*Valid because:* 8000 is above the buyer's baseline (the floor, 6000 for a
first buyer message), above the floor, and below the standing ask; the echoed
`rounds_remaining` is the pre-message value (3); the platform's ack says
`outcome: "countered"` with the post-message `rounds_remaining: 2`.

### Valid example 3 — `accept` and the binding-token ack

The buyer closes at the platform's standing ask (10 000) instead of spending
its last round:

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "a30e8400-0000-4000-8000-000000000903",
      "type": "counter",
      "timestamp": "2026-11-04T12:02:00Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": "a25e8400-0000-4000-8000-000000000905",
      "idempotency_key": "buyer-001-accept-neg_7c1f0a9b2e4d-1",
      "test": false
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "decision": "accept",
      "price_cents": 10000,
      "currency": "USD",
      "floor_price_cents": 6000,
      "rounds_remaining": 2,
      "expires_at": "2026-11-04T12:10:00Z"
    }
  }
}
```

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "a40e8400-0000-4000-8000-000000000904",
      "type": "ack",
      "timestamp": "2026-11-04T12:02:00Z",
      "sender_id": "platform_001",
      "receiver_id": "buyer_001",
      "correlation_id": "a30e8400-0000-4000-8000-000000000903",
      "idempotency_key": "platform-001-ack-a30e8400-1",
      "test": false
    },
    "payload": {
      "original_message_id": "a30e8400-0000-4000-8000-000000000903",
      "status": "ACCEPTED",
      "negotiation": {
        "negotiation_id": "neg_7c1f0a9b2e4d",
        "outcome": "accepted",
        "price_cents": 10000,
        "currency": "USD",
        "rounds_remaining": 2,
        "binding_token": "tok_9fQ2x8Lm4Vb7Kd1Rt6Yp3Wc5Nz0Hs2Ja",
        "binding_expires_at": "2026-11-04T12:10:00Z"
      }
    }
  }
}
```

*Valid because:* the accepted price equals the answered message's price, the
ack carries exactly one token, and `binding_expires_at` does not exceed the
negotiation expiry.

### Invalid example A — price below the floor (refused, never clamped)

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "b10e8400-0000-4000-8000-000000000911",
      "type": "counter",
      "timestamp": "2026-11-04T12:01:00Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": "a10e8400-0000-4000-8000-000000000901",
      "idempotency_key": "buyer-001-counter-below-floor-1",
      "test": false
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "decision": "counter",
      "price_cents": 4500,
      "currency": "USD",
      "floor_price_cents": 6000,
      "rounds_remaining": 3,
      "expires_at": "2026-11-04T12:10:00Z"
    }
  }
}
```

*Rejected:* 4500 < 6000. The platform answers `ack.status: "REJECTED"` with
`rejection_reason: "below_floor_price"` and one `errors[]` entry
`{code: "LCP-100", field: "payload.price_cents"}`, moves the negotiation to
`REFUSED`, creates no bid and no post, and **does not rewrite the price to the
floor** — a clamped price would be a seller-side fabrication with a money
consequence, and the buyer could not tell it happened. No token is minted.

### Invalid example B — round exhaustion

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "b20e8400-0000-4000-8000-000000000912",
      "type": "counter",
      "timestamp": "2026-11-04T12:04:00Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": "a28e8400-0000-4000-8000-000000000905",
      "idempotency_key": "buyer-001-counter-round-4-1",
      "test": false
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "decision": "counter",
      "price_cents": 9200,
      "currency": "USD",
      "floor_price_cents": 6000,
      "rounds_remaining": 0,
      "expires_at": "2026-11-04T12:10:00Z"
    }
  }
}
```

*Rejected:* `max_rounds` was 3 and the buyer has already consumed all three
(`rounds_remaining: 0`). The message is refused `round_limit_exceeded`, the
negotiation moves to `REFUSED`, and the id closes permanently — an unbounded
negotiation loop is the abuse surface C5 exists to remove. The price itself is
monotone-valid, which is the point: the cap binds independently of the price
rules.

### Invalid example C — expired negotiation

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "b30e8400-0000-4000-8000-000000000913",
      "type": "counter",
      "timestamp": "2026-11-04T12:10:01Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": "a29e8400-0000-4000-8000-000000000906",
      "idempotency_key": "buyer-001-counter-late-1",
      "test": false
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "decision": "counter",
      "price_cents": 9600,
      "currency": "USD",
      "floor_price_cents": 6000,
      "rounds_remaining": 1,
      "expires_at": "2026-11-04T12:10:00Z"
    }
  }
}
```

*Rejected:* `message.timestamp` (12:10:01Z) is at/after `expires_at`
(12:10:00Z). Refused `negotiation_expired`; the negotiation is `EXPIRED` and
`sweep`-visible. Expiry is absolute — wall-clock appeal, a fresher `timestamp`,
or a later quote cannot extend it, and the value may never exceed the ping's
TTL (that attempt is `LCP-100` at quote intake, before any state exists).

### Invalid example D — non-monotone counter

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "b40e8400-0000-4000-8000-000000000914",
      "type": "counter",
      "timestamp": "2026-11-04T12:03:00Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": "a25e8400-0000-4000-8000-000000000905",
      "idempotency_key": "buyer-001-counter-backwards-1",
      "test": false
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "decision": "counter",
      "price_cents": 7000,
      "currency": "USD",
      "floor_price_cents": 6000,
      "rounds_remaining": 1,
      "expires_at": "2026-11-04T12:10:00Z"
    }
  }
}
```

*Rejected:* the buyer's previous price in this negotiation was 8000, so 7000
walks the price backwards. Refused `non_monotone_counter`; no round is
consumed and no state changes on a rejected message, but the negotiation is
terminal `REFUSED` (C6/C8: the id does not become reusable). The platform's
mirror case — a `counter` below its own standing ask — is refused the same
way; it is a concession dressed as a counter.

### Invalid example E — PII attempt

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "b50e8400-0000-4000-8000-000000000915",
      "type": "quote",
      "timestamp": "2026-11-04T12:00:00Z",
      "sender_id": "platform_001",
      "receiver_id": "buyer_001",
      "correlation_id": "d70e8400-0000-4000-8000-000000000021",
      "idempotency_key": "platform-001-quote-pii-1",
      "test": true
    },
    "payload": {
      "negotiation_id": "neg_7c1f0a9b2e4d",
      "ping_id": "ping_9f31c2",
      "offer_id": "offer_mortgage_nsw",
      "price_cents": 10000,
      "currency": "USD",
      "floor_price_cents": 6000,
      "max_rounds": 3,
      "rounds_remaining": 3,
      "expires_at": "2026-11-04T12:10:00Z",
      "consumer_phone": "+15125550147",
      "note": "call Jordan after 6pm, prefers SMS"
    }
  }
}
```

*Rejected twice over:* `consumer_phone` and `note` are outside the strict
allowlist, so the message fails `LCP-100` naming `payload.consumer_phone`;
because the field name is a consumer identifier (and a phone number is a
contact value, not a price term), the ping-class prohibition applies and
`LCP-008 PII_IN_PING` is returned with it. The code is reused rather than
renamed — a new code would be a receiver-visible change a v1.0 peer cannot
anticipate (SPEC.md §5). Both the value and the reason are recorded as a
security finding, never stored as a payload.

## Privacy, security, and universal-core audit

**Ping-safe proof (field by field).** Every field of every message in this
surface is one of: an opaque platform id (`negotiation_id`, `ping_id`,
`offer_id`, `sender_id`, `receiver_id`, `correlation_id`, `original_message_id`,
`binding_token`), an integer in minor units (`price_cents`,
`floor_price_cents`, `rounds_remaining`, `max_rounds`), an ISO 4217 code, a
UTC timestamp, or a value from a closed/known reason enumeration. None of them
is derived from a consumer, and — unlike the contacted-hash surface — **no
consumer identifier is hashed into this surface at all**: negotiation happens
before delivery, so there is nothing to dedup and a hash would only create a
per-pair oracle nobody needs. The allowlist (`additionalProperties: false`) is
the enforcement, not prose: unknown fields fail validation, and a
consumer-shaped name fails the ping-class PII rule (invalid example E). The
conformance runner gains the same three checks it applies to pings
(allowlist, no PII block, no contact digest) for both new types, and the D9
`x-lcp-pii` annotation is required on every property — all `internal`.

**No free text (stated because it is the design constraint, not an
oversight).** The surface defines no `note`, `comment`, `description`,
`reason_text`, or `buyer_reference`. Refusal reasons are enumeration values, so
an operator cannot explain a refusal in the field where a consumer name would
otherwise be typed. Anything a party wants to say in prose belongs in its own
systems (LEP-0007 takes the same position for `details`).

**Authorization and tenancy.** A negotiation is scoped to one `(tenant,
buyer_id, ping_id, offer_id)` tuple. The buyer must be one the platform pinged
for that offer; the platform must be the `receiver_id` of the ping the buyer
bids against. A `negotiation_id` presented by any other principal is
`LCP-002` — the id is not a bearer credential, and the response is
indistinguishable from "no such negotiation" so it cannot be used to probe
existence. Token redemption is bound to `(negotiation_id, buyer_id, price,
expiry)`; a foreign or expired token is `negotiation_closed`, never "invalid".
Every route carries the tenant predicate (`tenant_id`) and the existing
`test_tenant_isolation` gate (execution-plan invariant I6).

**Replay and duplication.** Envelope HMAC plus the five-minute replay window
(SPEC §9, `LCP-012`) apply unchanged. Idempotency keys are mandatory; same
key/different bytes is `LCP-005`. A duplicate counter returns the original ack
and does not consume a second round. A duplicate redemption returns the
original ack and creates no second bid. Tokens are unguessable, single-use,
stored as hashes, and never logged (the existing operational rule: "audit rows,
metrics, and logs never carry token bytes").

**Money and price integrity.** All arithmetic is on integer minor units
(D7 unchanged; no FX, no float, no rounding in this surface). A mismatch
between the restated `floor_price_cents`/`currency` and the platform's stored
values is `LCP-100`, never a silent correction. Below-floor prices are
refused, never clamped, so the platform never claims a price the buyer did not
state. An accepted price is the bid price: the token carries it, redemption
cannot restate it, and the ordinary auction/post payability path is the only
thing that can create money. Negotiation messages never create a payable, never
touch `post.pricing`, and never re-open a payable (D2's rule generalizes).

**Abuse surfaces, bounded.** Negotiation-id enumeration is infeasible (opaque
random ids, authenticated pair scope, non-oracular error). Price probing is
bounded by `max_rounds` (≤ 5) and by the existing per-sender rate limits
(`LCP-011`). Floor mining discloses nothing new: the floor is already in the
ping. Round-loop DoS is capped by C5. Forgotten negotiations are swept by
expiry, so an unbounded id population cannot accumulate.

**Retention and erasure.** Negotiation rows contain no consumer data, so they
are non-PII routing/audit records and are retained under the platform's
non-PII class (W4-06), not the envelope class. `privacy erase-lead` redacts
persisted envelopes and cancels pending deliveries; because a negotiation holds
only ids, prices, and timestamps, erasure has nothing to redact here and MUST
NOT delete the negotiation's audit trail — but it MUST also not be usable as a
re-identification path, and the blocking test asserts there is no such field to
find. Tokens are hashed; erasure leaves a hash that grants nothing after
`REDEEMED`/expiry.

**Universal-core audit (SPEC §12 items).** No market vocabulary (no vertical,
country, channel, or product term appears). No executable matching rule, no
expression language, no callback. No hidden PII flow (the whole point of C4).
No new dependency (stdlib only). No change to any existing required field, no
change to a v1.0/v1.1 payload, and no change to an existing error code. One
stable enunciation per semantic (a price is `price_cents` + `currency`, exactly
as in `bid`/`post`/`ping` — no parallel spelling).

## Compatibility, versioning, and deprecation impact

- **Closed-enum addition → MINOR.** SPEC.md §6 states `message.type` is closed
  and "adding a message type is a MINOR version bump". v1.2.0 is that bump.
  The enum stays closed, so a receiver that has not implemented the types
  rejects them `LCP-006` loudly rather than passing them through.
- **A 1.0/1.1 peer must never receive these types unsolicited.** Emission is
  gated on two independent facts: (a) the receiver's parsed-semver
  `lcp_versions` contains `major == 1 and minor >= 2` (SPEC §8 membership
  rule — parsed elements, never string prefixes), **and** (b) the receiver's
  `capabilities().features` map contains `negotiation` (W0-03 spine,
  `{"batch": "1.2.0", "negotiation": "1.2.0", …}`). Both are required because
  a deployment may run the code with the feature off. Capability absence means
  the sender must not emit; emitting anyway is a conformance failure, and the
  receiver answers `LCP-006` (documented, not a crash).
- **`ack.negotiation` and `bid.negotiation_binding_token` are additive
  optionals** on closed objects, gated by the same advertisement. A v1.1
  receiver never sees them; a v1.2 receiver tolerates absence and treats their
  absence as "no negotiation" (the pre-existing behaviour).
- **The other direction is untouched.** A 1.2 receiver accepting a 1.0 or 1.1
  sender behaves exactly as it did: no new required field, no new rejection on
  an old message, no changed default. Adding a minor to `lcp_versions` never
  invalidates a lower-minor sender (SPEC §2/§8 rule).
- **Deprecation.** Nothing is deprecated by this record. If the surface is
  superseded later, the N+2 compatibility window and two-year grace period of
  `governance/LEP.md` step 7 apply to two message types that no non-advertising
  peer can receive — the smallest possible deprecation surface.
- **Contingency.** If implementation does not complete, `docs/DECISIONS-v1.2.md`
  moves the wire surface to v1.3.0 while the LEP record stays Accepted with a
  Design-Complete/not-implemented status in the registry. The registry status
  must never claim `Implemented` without the vector and platform evidence
  links.

## SDK, OpenAPI, MCP, conformance, and operational impact

**Schemas (normative source, edited first):** `schemas/envelope.json`
(`message.type` +2 values), new `schemas/quote.json` and
`schemas/counter.json` (strict allowlist, `x-lcp-pii: internal` on every
property, `max_rounds`/`rounds_remaining` bounds, conditional `price_cents`),
`schemas/bid.json` (+`negotiation_binding_token`),
`schemas/ack.json` (+`negotiation` object, closed `outcome` enum).
`$defs.currency` is reused from `core.json`; no new core definition.

**Ten SDKs.** Each ships: `Quote`/`Counter` payload models (closed-enum
discriminated message unions where the language has them), a negotiation helper
(`send_quote`, `send_counter`, `accept_negotiation`, `refuse_negotiation`),
token redemption on the existing bid call (`submit_bid(..., negotiation_binding_token=…)`),
`negotiation` awareness in the capabilities model, and updated
schema manifests. Per language — `typescript`, `python`, `go`, `java`,
`kotlin`, `rust`, `csharp`, `php`, `ruby`, `swift`
(`implementations/sdk/<lang>`) — the acceptance is: `tools/check_schema_sync.py`
green for all ten, per-SDK compile/test green in `sdk-interop.yml`, and the
closed-enum corpus (`test-vectors/sdk/enum-passthrough-corpus.json`) still
refusing unknown `message.type` values while accepting `quote`/`counter` only
when advertised.

**OpenAPI (`api/lcp-openapi.yaml`):**
`POST /quotes` (`operationId: submitQuote`), `POST /counters`
(`submitCounter`), redemption on the existing `POST /bids` (`submitBid`, with
`negotiation_binding_token` on `BidPayload`), `Capabilities.features` as an
additional-properties string map, `Ack.negotiation` and `NegotiationOutcome`
schemas, and two tags (`Negotiations`). The change is additive: no operation,
parameter, or response shape changes for a client that does not use it. The
`check-openapi` gate asserts pattern equivalence between the OpenAPI enums and
the JSON Schemas.

**MCP (`implementations/mcp-server/lcp_mcp_server/server.py`, currently
`submit_lead`, `submit_call`, `query_lead_status`, `get_schema`,
`get_capabilities`, `get_request_history`, `list_offers`, `submit_bid`):**
- `send_quote` — platform-role tool; arguments mirror the payload table.
- `send_counter` — buyer- or platform-role tool; `decision` explicit, so an
  agent cannot "counter" without stating a price.
- `redeem_negotiation_token` — a thin wrapper over `submit_bid` with the token
  argument, present so an agent does not have to know the bid field name.
- `get_capabilities` gains `features` pass-through verbatim (no filtering, so
  an agent sees the same advertisement a wire peer sees).

**Conformance.** New suite `test-vectors/negotiation.json` (ids `NEG-001`…),
run by `test-vectors/conformance.py`, covering at minimum: valid
quote/counter/accept round trip; below-floor refusal with no clamping; round
exhaustion; expired message; non-monotone (buyer and platform directions);
`max_rounds > 5`; `expires_at` beyond the ping TTL; currency/floor mismatch;
duplicate counter (no round burned); token redemption and same-key replay;
foreign-sender token; a quote/counter carrying a consumer field or a
48–64-hex contact digest; and an unadvertised receiver. The runner treats a
receiver that does not advertise `negotiation` as **skip, not fail** (the
advertisement is the switch), and the new coverage-matrix rows cite this LEP
for every emission and refusal rule. Vector IDs are stable and never reused.

**Platform state machine (W4-08/W5-03).** One table per negotiation, one audit
row per message, one hashed-token table:

| State | Entered by | Terminal | Notes |
|---|---|---|---|
| `OPEN` | `quote` accepted at intake and persisted | no | The ask is recorded; `rounds_remaining = max_rounds`. |
| `COUNTERED` | buyer `decision: "counter"` accepted into state | no | `rounds_remaining` decremented by one; new standing price recorded. |
| `ACCEPTED` | `decision: "accept"` from either side, at an allowed price | no (redeemable) | Price frozen; one token hash minted; `binding_expires_at` recorded. |
| `REDEEMED` | first valid token redemption in a bid | yes | The bid row exists; all downstream money semantics are the bid's. |
| `REFUSED` | any terminal refusal (`below_floor_price`, `non_monotone_counter`, `round_limit_exceeded`, `negotiation_expired`, `unknown_negotiation`) | yes | Exactly one reason stored; the id never reopens. A later message naming a closed id is answered `negotiation_closed` with **no** state change. |
| `EXPIRED` | expiry sweep past `expires_at` | yes | Sweep is idempotent; a late message is `negotiation_expired`, not a state change. |

Illegal transitions return `LCP-004 INVALID_STATUS_TRANSITION` (`REDEEMED` →
anything; `ACCEPTED` → `COUNTERED`; a second accept from the same
negotiation). Audit event names follow the platform's dotted convention
(`negotiation.opened`, `negotiation.countered`, `negotiation.accepted`,
`negotiation.refused`, `negotiation.expired`, `negotiation.redeemed`) and
record actor, before/after state, price, round, and request id — never token
bytes.

**Operations.** `GET /v1/lcp/metrics` gains
`negotiation.{opened,countered,accepted,refused,expired,redeemed}` counters,
`negotiation.rounds_used` (histogram 0–5), `negotiation.refusals_by_reason`,
and `negotiation.token_redemption_failures`. Operator guidance (docs update):
a nonzero sustained `token_redemption_failures` is a client bug or a token
leak; a `round_limit_exceeded` share above half means the cap is miscalibrated
or the ask is unrealistic; refusals must never alert as errors (they are the
designed refusal path). `LCP_FEATURE_NEGOTIATION` is documented beside
`LCP_MAX_DELIVERY_ATTEMPTS` and the other `LCP_*` switches in the deployment
guide; it is the only new switch for this surface (no per-tenant sub-flags, no
per-peer toggles).

## Alternatives considered

**A. Reuse `ping`/`bid` with extension fields (rejected).** The obvious
cheapest route: add `counter_price_cents`, `round`, `negotiation_id` to `bid`
and let the platform "counter" by sending another ping. It fails on four
measured properties of the current core:
1. `ping` and `bid` are both strict allowlists
   (`schemas/ping.json` and `schemas/bid.json`, `additionalProperties:
   false`), so a v1.1 receiver rejects the new fields `LCP-100`/`LCP-008` —
   the same compatibility wall D8 exists to avoid, only spread across two
   existing types instead of two new ones.
2. A ping is a **broadcast** to many buyers and carries `lead_age_minutes`,
   quality signals, and slot state. Re-pinging to counter would either
   re-broadcast the counter (leaking one buyer's price to its competitors —
   a commercial-information breach) or require a private ping variant, which
   is a new type in disguise.
3. `bid.decision` is a three-value closed enum with no round semantics; adding
   `counter` to it makes `decision: counter` mean "a message that will be
   answered", which is a state machine living in a field that has none.
4. `bid` has no floor echo and no round counter, so a receiver cannot verify
   the monotone rule or detect replay — the exact ambiguity this record
   removes.
The extension route was therefore rejected as **more** wire surface, not less:
a new type with a strict allowlist is smaller than five fields of state spread
across two closed objects.

**B. Out-of-band negotiation (rejected).** Keep prices in email/chat/sales
calls and send a single final `bid`. Rejected because: the agreed price is not
signed, not idempotent, and not replay-safe; it cannot be reconciled with
`post.pricing` during a dispute (the audit is a mail archive); it gives an
automated buyer no machine path, which is the entire requirement; and it
carries consumer context into a channel with no PII boundary. The one thing it
has — zero wire cost — is not worth an unattributable price.

**C. Namespaced extension event values for the negotiation rounds
(rejected).** LEP-0007's route (open `event` values + `details`) is the right
shape for a *report*, and it is the wrong shape for a **typed price
response**: `event.details` is an open object whose content cannot be
schema-constrained, so integer minor units, the floor echo, the round counter,
and the allowlist could not be enforced — and an open `details` object is
exactly the PII-smuggling shape this surface forbids. It would also make a
state machine out of an event log, where "who may answer" is not expressible.

**D. New `ack.status` values instead of `rejection_reason` (rejected).**
`ack.status` is a closed schema enum (`schemas/ack.json`) with a defined
lifecycle; adding `COUNTERED`/`EXPIRED_FOR_NEGOTIATION` there is a
receiver-visible closed-enum change on the one object every implementer
switches on, and it would re-open money semantics (D2's whole argument is that
status values near payability are dangerous). The existing **open**
`rejection_reason` enumeration plus the new `ack.negotiation.outcome` closed
enum (six values, scoped to negotiation acks only) carry the same information
without touching the shared status axis.

**E. Keep the behavior deployment-specific (rejected).** A buyer agent that
must implement each platform's bespoke counter endpoint has no interoperability
value and no incentive to build it; the surface would fragment into N
incompatible dialects, which is the failure mode LCP exists to prevent. D8
accepted the core cost for exactly this reason — while recording that the
adoption evidence for the choice was waived, not produced.

## Rollout, migration, observability, and rollback

**Rollout.**

1. Schemas, vectors, OpenAPI, and SDK models land together (change gate I2);
   the feature is **off**.
2. `LCP_FEATURE_NEGOTIATION` (default `off`, parsed in
   `config.py` alongside the other `LCP_*` switches) gates three things only:
   the `features.negotiation` advertisement, intake of the two message types,
   and token minting/redemption. With the flag off, the platform advertises
   `message_types` without the new values, answers `LCP-006` for them, and
   writes no negotiation rows.
3. Tables (`negotiation`, `negotiation_round`, `negotiation_token`) are
   created by `initialize()` unconditionally — a schema that depends on an env
   var is a migration hazard, and empty tables cost nothing. There is no
   backfill: nothing pre-existing maps onto a negotiation.
4. Enable per-tenant/per-deployment only after the vectors and the platform
   suite are green (`W5` gate row). Enabling is the advertisement change; no
   client upgrade is required on the peer side.
5. A deployment that enables negotiation MUST advertise a `lcp_versions`
   element of `1.2.0` or higher, because `features` alone does not satisfy a
   strictly-conforming 1.2 sender's semver check.

**Observability.** The metrics above, plus the audit event stream; no consumer
data and no token bytes in either. A dashboard per deployment should show
rounds used (the cap's real cost), refusals by reason (the refusal path is the
designed path, so it needs visibility, not paging), accepted→redeemed
conversion (a low rate means the token TTL or ask is wrong), and expiry
volume.

**Rollback.** Rollback is **disabling the advertisement** (the same
mechanism OPERATIONS.md documents for the v1.1 slot surface):
`LCP_FEATURE_NEGOTIATION=off` (or dropping the `negotiation` key and the
`1.2.0` element from the advertisement) makes the surface unreachable for every
peer, with no data-format migration.

- In-flight negotiations become unusable: a redemption against them returns
  `negotiation_closed`. No money moves, because nothing was delivered — a
  negotiation is pre-delivery by construction.
- Rows and audit trail are retained (non-PII, required for dispute
  reconstruction); they can be archived under W4-06 like any other
  non-PII history.
- Already-redeemed tokens are ordinary bids and are unaffected by rollback.
- Re-enabling is the same switch. There is no data epoch, no dual-write, and
  no SDK version pin, because the surface is opt-in in both directions.

## Tests

Blocking. Each line names the smallest test that fails if the rule breaks; all
run in `implementations/reference-platform/tests` with the feature flag on,
plus the new conformance suite for the vector-side assertions (W5 gate).

| # | Test | Asserts |
|---|---|---|
| T1 | `test_negotiation_round_cap` | `max_rounds` absent ⇒ 3 rounds accepted and a 4th buyer counter refused `round_limit_exceeded`; `max_rounds: 5` allows exactly 5; `max_rounds: 6` is `LCP-100` at quote intake; the cap is enforced in state, not by the client echo (`rounds_remaining` is checked, then ignored for counting). |
| T2 | `test_negotiation_monotone_enforced` | A buyer counter at or below its own previous price is refused `non_monotone_counter`; a platform counter below its standing ask is refused the same way; a legitimate climb is accepted and moves `COUNTERED`; a refused message consumes no round and leaves the stored standing price unchanged; both directions are covered by vectors `NEG-005`/`NEG-006`. |
| T3 | `test_negotiation_floor_refusal_never_clamps` | A price below the restated floor is refused `below_floor_price` with `LCP-100` naming `payload.price_cents`; the stored price is unchanged; no bid, post, or token exists afterwards; the refusal response contains no price the sender did not state. |
| T4 | `test_negotiation_expiry_absolute` | A message at/after `expires_at` is refused `negotiation_expired` and the negotiation is `EXPIRED`; a quote whose `expires_at` exceeds the ping's expiry (or its timestamp+TTL) is `LCP-100` before any row is written; a fresh `message.timestamp` inside the replay window does not extend anything; the sweep is idempotent. |
| T5 | `test_negotiation_token_single_use_idempotent` | First redemption creates exactly one bid at the token price and `REDEEMED`; the same envelope with the same `idempotency_key` returns the original ack byte for byte with no second bid and no second payable; the same token from a different sender is `LCP-002`; the same token from the same sender with a different key after `REDEEMED` is `negotiation_closed`; only a hash is stored and the plaintext token never appears in a log, audit row, or metrics label. |
| T6 | `test_negotiation_capability_absence` | With `LCP_FEATURE_NEGOTIATION` off, `capabilities()` has no `negotiation` key and no new `message_types`, and intake of `quote`/`counter` is `LCP-006`; with the flag on but a receiver advertising only `1.1.0`, the sender helper refuses to emit (client-side gate) and the platform does not advertise the types to that peer; `features` present with a lower `lcp_versions` element does not open the gate. |
| T7 | `test_negotiation_tenant_authorization` | Buyer B presenting buyer A's `negotiation_id` receives `LCP-002` (indistinguishable from a nonexistent id); a buyer that was not pinged for that offer cannot open or join a negotiation; a cross-tenant read attempt returns no price, round, or state; the row is never visible outside its tenant predicate. |
| T8 | `test_negotiation_retention_erasure` | `privacy erase-lead` on a lead with a negotiation leaves the negotiation's audit rows addressable by opaque ids and containing **no** consumer field (there is none to find); tokens remain hashed and unredeemable after erasure/expiry; retention expiry (W4-06) makes expired negotiation rows unreachable, archives included; erasure does not delete the price/audit trail needed for dispute reconstruction and does not resurrect any envelope data. |

Two additional non-blocking-but-required gates ride the standard matrix rather
than this table: `tools/check_schema_sync.py` (schemas ↔ ten SDK bundles ↔
manifests) and the coverage matrix's zero-unexplained-gap check for the new
SPEC clauses.

## Deferred questions

Each carries the trigger that reopens it. None of these blocks acceptance; all
are recorded so a later change does not have to re-litigate this one.

| # | Question | Why deferred | Trigger to revisit |
|---|---|---|---|
| Q1 | Should the protocol offer normative *strategy* (how many rounds to use, how to size a first counter)? | Strategy is agent policy; a normative default would encode a commercial position into the core and make the platform's reserve guessable. | Two adopters ship their own strategy and both ask for a shared default, with measured closed rates. |
| Q2 | May a platform concede downward inside a negotiation (a real seller counter below its ask)? | C6 forbids it: a lower counter is a price concession with no audit distinction from a mistake. Implementing it needs a monotone-rule revision (a signed delta, a concession flag, and a refusal reason for an unearned delta). | Adopter evidence that restating the ask loses closable sales, plus a security review of the concession surface. |
| Q3 | Should the buyer's ceiling travel on the wire (from `offer.max_allowed_price_cents`)? | The ceiling is already offer-side configuration; putting it in a negotiation discloses the buyer's walk-away price to the seller. | An adopter needs a wire-visible ceiling for cross-vendor automation, with a comparison rule and a refusal reason. |
| Q4 | Negotiation over non-ping surfaces (`post` reprice, resale, subscription repricing). | Different footing: post-delivery repricing touches payability, disputes, and already-delivered PII. | Two independent adopters, plus a money-semantics review that starts from `post.pricing`, not from this record. |
| Q5 | Multi-currency negotiation with FX comparison. | D7 requires a non-stale, consistently-sourced rate before two prices may be compared; a negotiation would need that same rule plus a refusal reason for a stale rate. | D7 has ≥2 adopters using `fx_rate`, and the negotiation refusal vocabulary gains a `stale_rate` value. |
| Q6 | A read/list surface (`GET /v1/lcp/negotiations`, history parity with LEP-0005). | No adopter has asked; it adds an authorization + pagination surface with a PII-adjacent audit read, and the state table is already queryable operationally. | An operator needs self-service history, with a security review of the tenant predicate and a retention rule for the read. |
| Q7 | `max_rounds` above the hard cap of 5. | The cap is the abuse bound, not a tuning knob; raising it needs a measured benefit at the boundary. | A deployment shows a closed-rate gain at 5 rounds that continues to 8, with cost data. |
| Q8 | Negotiation for `call` leads (telephony pricing, per-minute terms). | The same surface prices a lead; call pricing has its own unit (`durations`, `payable_rules`) and no evidence yet. | Two adopters negotiating call pricing, plus a unit definition that is not minor units. |

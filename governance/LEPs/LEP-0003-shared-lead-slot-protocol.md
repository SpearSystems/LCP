# LEP-0003 — Shared-lead slot allocation and contacted-party dedup exchange

- **Title:** Shared-lead slot allocation and contacted-party dedup exchange
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-02
- **Status:** **Draft** — not accepted, not implemented
- **Target version:** v1.1.0 (MINOR; see Compatibility and versioning)
- **Evidence corrections applied:** valid post-example `exclusivity` block
  fixed per ARB retro-audit at `dee5584` (no status change; remains Draft)
- **Discussion:** [#124](https://github.com/SpearSystems/LCP/issues/124)
  (`[LEP] Shared-lead slot allocation and contacted-party dedup exchange`)
- **Roadmap trigger:** `docs/V1.1-ROADMAP.md`, Phase 6, "Shared-lead slot
  protocol" (high priority)

## Problem statement and evidence

LCP v1.0 models shared leads statically but not operationally. SPEC.md §3
defines the `exclusivity` block — `exclusivity: "exclusive" | "shared"`
(default `shared`) with `max_buyers` for shared leads — and the `ping`
message carries an `exclusivity` hint and a `dedup_window_hours` sender
hint. But nothing in the runtime contract answers the two questions a
shared lead actually creates:

1. **How many copies are left?** A buyer evaluating a `shared` ping cannot
   tell whether it is competing for one of five slots or the last one.
   Bid pricing, routing, and the publisher's own revenue depend on that
   scarcity signal, and `max_buyers` on the lead is a total, not a
   remaining count.
2. **Who has already been contacted?** Once buyer A and buyer B both hold
   the lead, B needs to know which parties A (or the publisher) has
   already attempted, without B receiving A's data and without any
   plaintext contact identifier crossing the exchange. SPEC.md §9 states
   plainly that `phone_hash` "is not a cross-buyer dedup mechanism
   (different buyers see different hashes)" — dedup within one buyer's
   stream is solved; dedup across slot holders is undefined.

**External evidence.** A comparison against a major US home-services lead
network's ping/post API (`docs/V1.1-ROADMAP.md` Phase 6, 2026-08-18)
found three live mechanisms LCP lacks: `slots_available` on the ping,
`slots_claimed` in the ping response, and a hashed already-contacted
contact exchange on the post, whose stated purpose is "so no vendor
receives the same lead twice." That network sells shared leads at
production scale today, which is direct evidence that the operational gap
is real for buyers and publishers, not theoretical.

**Internal evidence.** The v1.0 review log (SPEC.md §14) added
`exclusivity` + `max_buyers` in §3 as the fix for "No exclusivity model,"
but the same review classifies the runtime pieces as unresolved work; the
roadmap lists this item as high priority because "once multiple buyers can
see the same lead, buyers need to know what has already been contacted
without seeing plaintext PII." The mechanism already exists in the
protocol in adjacent form: the `CONSENT_WITHDRAWN` and `ERASURE_REQUEST`
events exchange `phone_hash`/`email_hash` values keyed per pair
(SPEC.md §4), and L3 conformance already covers "dedup fingerprints"
(SPEC.md §7). This LEP completes the pattern rather than inventing one.

## Scope

- **Core protocol (additive MINOR):** three optional fields on existing
  closed message payloads — `slots_available` on `ping`, `slots_claimed`
  on `bid`, `contacted_hashes` on `post` — plus a recommended `CONTACTED`
  event value whose `details` carry the per-pair keyed hashes back to the
  platform. No new message type (the closed `message.type` enum is
  unchanged), no new envelope shape, no vertical-schema change.
- **SPEC text:** §3 (slot semantics for `exclusivity`/`max_buyers`), §4
  (field tables + `CONTACTED` guidance), §6 (closed-object versioning
  clarification; see Compatibility), §9 (contact-hash paragraph mirroring
  `phone_hash`).
- **Reference implementation:** platform-enforced slot accounting
  (remaining = `max_buyers` − delivered posts; never oversell) and
  contacted-hash propagation on post; `CONTACTED` event ingestion.
- **REST/OpenAPI, MCP, SDKs, vectors:** change-gated at implementation
  time — listed in the impact section, **not** authorized by this Draft.
- **Not in scope:** slot *pricing* policy (per-buyer bidding strategy
  stays in offers/extensions), cross-pair contact visibility (a buyer
  never sees another buyer's keyed hashes), refund accounting rules
  (Deferred questions), per-market commercial defaults (Deferred
  questions), and any change to the `exclusivity` default value (see the
  reasoned position below).

## Proposed wire shape

### 1. Ping — `slots_available`

Additive optional field on the existing ping allowlist (SPEC.md §4,
`schemas/ping.json`):

| Field | Type | Notes |
|---|---|---|
| `slots_available` | integer | Remaining copies of the lead that can still be sold at ping time, **including** the slot this ping's recipient may win. `minimum: 1`. Absent = the platform does not advertise slot state (v1.0 behavior). |

Semantics:

- `exclusivity: "exclusive"` is exactly the one-slot special case: when
  present, `slots_available` MUST be `1` (an exclusive ping advertising
  more is a semantic error; see invalid example B).
- For `shared` leads, `slots_available ≤ max_buyers` (when the lead
  declares a `max_buyers`), and the platform MUST NOT send a ping with
  `slots_available: 0` — zero remaining means stop pinging, not ping with
  zero.
- The field is an integer count. It is non-PII and carries no consumer
  data, so it is compatible with the ping strict allowlist design
  (`LCP-008 PII_IN_PING` protection is preserved — the allowlist simply
  gains one reviewed non-PII field).

### 2. Bid — `slots_claimed`

Additive optional field on `bid` (`schemas/bid.json`):

| Field | Type | Notes |
|---|---|---|
| `slots_claimed` | integer | Slots this buyer claims if selected. `minimum: 1`. Only meaningful with `decision: accept`; absent = 1. |

- The claim is a request, not a reservation. The platform awards slots at
  **post** time (delivery is the claim event; bids are non-binding, per
  the existing auction flow where "losers receive no further messages").
- The platform MUST NOT award more slots than remain. If a winner's
  `slots_claimed` exceeds the remaining count, the platform either awards
  the remainder or re-routes (deployment choice) — under no conditions
  posts beyond the advertised budget (see invalid example C).
- A buyer claiming more than one slot is the agency/network case: one
  buyer, several downstream vendors. This keeps the reference network's
  `slots_claimed` mechanism without forcing single-vendor buyers to
  reason about it.

### 3. Post — `contacted_hashes`

Additive optional field on `post` (`schemas/post.json`):

| Field | Type | Notes |
|---|---|---|
| `contacted_hashes` | array\<string\> | Parties already contacted for this lead by the publisher or prior slot holders. Each entry MUST match `^[a-f0-9]{64}$` — the same lowercase-HMAC-SHA256 form as `ping.phone_hash`. Keyed with the **receiving buyer's per-pair secret**: `HMAC-SHA256(pair_secret, normalized_e164)` or `HMAC-SHA256(pair_secret, normalized_email)`. Absent = the platform has no contact reports; `[]` = the platform attests no prior contacts. |

- The buyer matches entries against the hashes it already holds — the
  `phone_hash`/`email_hash` values stored from earlier pings (the L3
  dedup fingerprint) and its suppression lists (the same lists
  `CONSENT_WITHDRAWN` requires buyers to maintain, SPEC.md §4). A match
  means "this party has already been contacted by an earlier slot holder
  (or the publisher)"; no match data is leaked because hashes computed
  under this pair's secret are unmatchable by anyone else.
- **The hash algorithm is inherited, not new.** This is the existing
  per-pair HMAC-SHA256 model of SPEC.md §9 (`phone_hash =
  HMAC-SHA256(shared_secret, normalized_e164)`, per-pair secrets, key
  distribution deployment-defined). The reference network's unsalted
  SHA-1 is explicitly NOT adopted: phone and email keyspaces are small
  enough to enumerate, so unsalted digests invert by lookup. LCP already
  rejected exactly that weakness for `phone_hash` (review-log item B3,
  SPEC.md §14: unsalted SHA-256 was a *blocker*, fixed by per-pair
  HMAC).
- Re-keying for the recipient (who holds the raw contacts and computes
  hashes under which pair secret) is a deployment concern, identical to
  §9's position on key distribution. The protocol only fixes the wire
  rule: every hash in a message is keyed with the secret shared with that
  message's receiver.

### 4. Event — `CONTACTED` (reporting direction)

The exchange is only closed-loop if the platform learns contacts as they
happen. `event` is an **open enum** and `details` is
`additionalProperties: true` (SPEC.md §6 table, `schemas/event.json`), so
this costs no schema change — it needs normative guidance, in the same
style as the existing `CONVERTED`/`DISPUTED` detail guidance:

> For `CONTACTED` events (a slot holder attempted contact with a party on
> the lead), `details` SHOULD include `contacted_hashes` (same per-pair
> rule as post), `contacted_at` (datetime), and `via`
> (`phone` | `email` | `sms` | `chat`; open enum). Buyers SHOULD report
> every first contact attempt on purchased leads.

The buyer sends the hashes it already computed from values that arrived
in its own pair-keyed form (or that it hashes from the post it received
under its pair secret); the platform stores them and re-keys per recipient
pair for subsequent posts. `submitEvent` (REST) already exists as the
transport.

### Valid example — ping with slots

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "770e8400-0000-4000-8000-000000000101",
      "type": "ping",
      "timestamp": "2026-11-02T10:20:05Z",
      "sender_id": "platform_001",
      "receiver_id": "buyer_002",
      "correlation_id": null,
      "idempotency_key": "platform-001-ping-ping_002-001",
      "test": true
    },
    "payload": {
      "ping_id": "ping_002",
      "lead_reference": "ref_qwe456",
      "phone_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
      "country_code": "US",
      "state_region": "TX",
      "postal_code": "78701",
      "vertical": "home_services",
      "attributes": { "service_category": "gutter_repair", "project_size_band": "1k_5k" },
      "floor_price_cents": 2400,
      "currency": "USD",
      "exclusivity": "shared",
      "slots_available": 2,
      "dedup_window_hours": 24,
      "expires_at": "2026-11-02T10:25:05Z"
    }
  }
}
```

### Valid example — bid claiming a slot

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "bb1e8400-0000-4000-8000-000000000102",
      "type": "bid",
      "timestamp": "2026-11-02T10:20:08Z",
      "sender_id": "buyer_002",
      "receiver_id": "platform_001",
      "correlation_id": "770e8400-0000-4000-8000-000000000101",
      "idempotency_key": "buyer-002-bid-ping_002-001",
      "test": true
    },
    "payload": {
      "ping_id": "ping_002",
      "decision": "accept",
      "bid_price_cents": 2900,
      "currency": "USD",
      "estimated_contact_seconds": 30,
      "slots_claimed": 1,
      "capacity_remaining": 12
    }
  }
}
```

### Valid example — post carrying per-pair contacted hashes

Two earlier parties were contacted (one phone, one email) by the first
slot holder; the platform re-keyed both digests under `buyer_002`'s pair
secret before posting. No plaintext contact identifiers appear in the
exchange.

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "c20e8400-0000-4000-8000-000000000103",
      "type": "post",
      "timestamp": "2026-11-02T10:21:00Z",
      "sender_id": "platform_001",
      "receiver_id": "buyer_002",
      "correlation_id": "bb1e8400-0000-4000-8000-000000000102",
      "idempotency_key": "platform-001-post-lead_def456",
      "test": true
    },
    "payload": {
      "lead_id": "lead_def456",
      "delivered_at": "2026-11-02T10:21:00Z",
      "price_cents": 2900,
      "currency": "USD",
      "buyer_id": "buyer_002",
      "consumer": { "full_name": "Jordan Reyes", "phone": "+15125550147" },
      "location": { "country_code": "US", "state_region": "TX", "postal_code": "78701" },
      "attributes": { "vertical": "home_services", "schema_version": "1.0.0" },
      "exclusivity": { "exclusivity": "shared", "max_buyers": 2 },
      "contacted_hashes": [
        "3f02a7c9d15e8b46a2c9f0d3b7e1a58c4d92f6b0a3c8e1d47f9b2c5a0d6e8f13",
        "9d4b17e0c26a8f53d1e7b94c0a26f8d35b1e7c9a4f2d60b8e1c3a5d7f9b2c4e6"
      ]
    }
  }
}
```

### Valid example — `CONTACTED` event report

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "ee0e8400-0000-4000-8000-000000000104",
      "type": "event",
      "timestamp": "2026-11-02T10:26:00Z",
      "sender_id": "buyer_002",
      "receiver_id": "platform_001",
      "correlation_id": "c20e8400-0000-4000-8000-000000000103",
      "idempotency_key": "buyer-002-event-lead_def456-contacted-1",
      "test": true
    },
    "payload": {
      "lead_id": "lead_def456",
      "event": "CONTACTED",
      "timestamp": "2026-11-02T10:25:41Z",
      "details": {
        "contacted_hashes": [
          "5a1c9e3b7d0f2a6c8e4b1d9f3a7c5e2b8d4f6a0c1e3b5d7f9a2c4e6b8d0f1a3"
        ],
        "contacted_at": "2026-11-02T10:25:41Z",
        "via": "phone"
      }
    }
  }
}
```

### Invalid example A — unsalted SHA-1 contacted hash

```json
"contacted_hashes": ["a94a8fe5ccb19ba61c4c0873d391e987982fbbd3"]
```

Rejected on two independent grounds: the entry fails the schema pattern
`^[a-f0-9]{64}$` (40 hex is not a SHA-256-family digest), and a 40-hex
SHA-1 over a phone or email is invertible by enumeration — the exact
weakness SPEC.md §14 blocker B3 removed from `phone_hash`. The roadmap
constraint is absolute: reuse the per-pair HMAC model, NEVER the
reference network's unsalted SHA-1.

### Invalid example B — exclusive lead advertising multiple slots

```json
"exclusivity": "exclusive",
"slots_available": 3
```

Contradiction: exclusive is defined as the single-slot special case.
A conformant platform rejects the ping construction at send time; a
receiver detecting it MUST treat the lead as non-slot-advertised (fall
back to `exclusivity` semantics) and MAY ack `REJECTED`.

### Invalid example C — slot oversell

A lead has `max_buyers: 2` and one post already delivered
(`slots_available: 1` on later pings). A winner bids `slots_claimed: 2`.
The platform MUST NOT deliver a second copy to this buyer and a copy to
another winner — total delivered posts MUST NOT exceed the slot budget.
Awarding one, or re-routing, is the platform's choice; posting two is a
conformance failure.

### Invalid example D — v1.1 field to a v1.0 receiver

`GET /v1/lcp/capabilities` for `buyer_009` returns
`"lcp_versions": ["1.0.0"]`, yet a platform pings `buyer_009` with
`slots_available: 2`. `buyer_009`'s L2 conformance validates the ping
against the v1.0 strict allowlist and rejects it (`LCP-008`/validation
error, per SPEC.md §4). This is the compatibility boundary the
capability-gating rule in the Compatibility section exists to prevent.

## Compatibility, versioning, and deprecation impact

- **Additive MINOR to v1.1.0** (semver per SPEC.md §6): four optional
  fields/values added; nothing existing changes type, name, requiredness,
  or semantics. Absent fields preserve v1.0 behavior exactly, including
  the absent-vs-empty distinction for `contacted_hashes`. Nothing is
  deprecated; the N+2 window is untouched.
- **The "v1 receivers ignore unknown optional fields" claim, verified
  against the spec.** SPEC.md §6 states: "Unknown optional fields ignored;
  unknown message types → structured error," and
  `governance/EXTENSION-REGISTRY.md` cites that same line as the
  forward-compatibility guarantee. **The claim is true only for receivers
  that do not schema-validate, and this proposal must not rely on it for
  the closed objects it touches.** The published schemas for `ping`,
  `bid`, `post`, `lead`, `ack`, and `core.json#/$defs/exclusivity` are
  closed (`additionalProperties: false`); SPEC.md §4 makes any field
  outside the ping allowlist a validation error (`LCP-008 PII_IN_PING`),
  §12 elevates the strict allowlist to a universal-core audit item, and
  §7 requires strict ping/post allowlist enforcement at L2. A
  schema-validating v1.0 receiver — which a conformant L2/L3 receiver is —
  therefore **rejects** `slots_available`/`slots_claimed`/
  `contacted_hashes` rather than ignoring them. (The analogous
  extension-ignore promise has the same hole: `extensions` is defined only
  in `offer.json`, so `payload.extensions` fails the closed ping/bid/post
  schemas today.)
- **Consequence (normative):** v1.1 slot fields are capability-gated, not
  ignore-gated. A sender MUST include `slots_available` only in pings to
  receivers whose §8 capability advertisement includes protocol version
  `1.1` in `lcp_versions` (and likewise gate `slots_claimed`,
  `contacted_hashes`, and `CONTACTED` on the receiver's declared v1.1
  support). §8 already exposes `lcp_versions`; this proposal consumes the
  existing negotiation surface and adds none.
- **Implementation-phase spec text:** SPEC.md §6 SHOULD gain one
  clarifying sentence recording that closed-object additions rely on
  version negotiation while the ignore-unknown rule covers open objects
  (`event.details`, vertical `attributes` extensions). This is
  documentation of existing behavior, not a new rule.
- **Semantics of the pre-existing default are frozen.** Flipping the
  `exclusivity` default (see the position below) would change what
  existing v1.0 bytes mean under a v1.1 receiver — a reinterpretation,
  which the additive-MINOR gate and the roadmap's "no roadmap item
  silently changes the v1.0 contract" rule forbid.

### Reasoned position: shared-vs-exclusive default

The roadmap flagged this decision explicitly: the reference network
defaults its ping to `exclusive: true`, while LCP's `exclusivity` block
defaults to `shared`, and the US home-services market norm is exclusive.
After analysis, this proposal **keeps the core default `shared`** and
records the reasoning here so the decision is conscious, not accidental:

1. **Flipping the default is a breaking change wearing a MINOR's
   clothes.** Omission today means `shared` (SPEC.md §3,
   `schemas/core.json`: `"default": "shared"`). Reinterpreting the same
   absent field as `exclusive` in v1.1 silently reverses deal economics
   for every existing publisher that omits the field — worse than a
   validation failure because both sides "succeed" with different facts.
   Rejected by the additive-MINOR constraint itself.
2. **A universal-core default should not encode one market's commercial
   norm.** §12 bans market-specific fields in core; a market-specific
   *default* has the same smell, and the neutral-position, anti-capture
   governance (§11) argues the core should express terms, not assume them
   for one geography.
3. **The slot mechanism removes the reason defaults mattered.** The
   default previously stood in for the missing scarcity signal ("am I the
   only buyer?"). With `slots_available`, that question is answered
   explicitly by a field: exclusive is exactly `slots_available: 1`, and
   the protocol no longer needs omission to carry commercial meaning.
4. **The market norm is served by explicitness, not by the default.**
   v1.1 slot-aware senders SHOULD send `exclusivity` and
   `slots_available` together on every slot-marketed ping (never rely on
   the default for a commercial term), and platforms whose US
   home-services deals are exclusive declare `exclusive` / one slot
   there — the same way per-deal pricing already lives in the
   publisher's, not the protocol's, decisions. If a deployment wants an
   exclusive-by-default *profile*, it belongs in offer/publisher
   configuration (or a registered `{org}.*` extension for buyer policy),
   which per-market defaults in Deferred questions will revisit with
   adopter evidence.

**Decision recorded:** default remains `shared`; exclusive = the 1-slot
special case; explicitness rule (SHOULD) on slot-bearing pings;
per-market default profiles deferred to deployment-level configuration,
with a revisit trigger.

## Privacy, security, and universal-core audit

- **No new PII on the ping.** `slots_available` is an integer count. The
  ping allowlist remains strict (§12); the added field is reviewed
  non-PII, and conformance keeps rejecting everything else.
- **Keyed hashes are the privacy primitive, inherited verbatim from
  §9.** Each `contacted_hashes` entry is
  `HMAC-SHA256(pair_secret, normalized_e164|normalized_email)` — per-pair,
  so (a) an interceptor without that pair's secret cannot verify a
  candidate phone/email: the phone and email keyspaces are small enough
  to enumerate, which is exactly why unsalted digests (the reference
  network's SHA-1, and the original unsalted SHA-256 rejected in review
  blocker B3) are unacceptable; (b) the same party produces a different
  hash per buyer pair, so a contacted list for buyer B is useless for
  linking against buyer A's data — cross-buyer correlation is
  structurally impossible, same property as §9 `phone_hash`.
- **Matchability is pair-scoped by design.** Both parties to the pair can
  test membership of a known contact (that is what dedup requires);
  neither can learn the preimages of hashes for contacts they do not
  already hold. The exchange reveals "you have met this party before"
  only to the party who can already compute the hash — it adds no
  identifier surface beyond what ping `phone_hash` delivery already
  established.
- **No plaintext contacts cross the wire.** The reporting direction
  (`CONTACTED` events) carries hashes only; raw contact data stays where
  v1.0 already puts it (full PII in post/lead over TLS 1.3, §9).
- **Authorization and integrity ride existing rails.** Pings/posts/
  events are HMAC-authenticated messages (§9 `X-LCP-Signature`); slot
  state and contacted hashes are platform-emitted (buyer-controlled
  fields are claims, which the platform must not trust blindly — see
  Invalid C). `idempotency_key` covers replay of contact reports.
- **Retention.** `contacted_hashes` are pseudonymized derived values
  bounded to one lead's contact history; they inherit the deployment's
  §9 PII retention policy (hashes of contact data used for matching are
  still personal data under most regimes — recorded in the issue for the
  privacy review). The `ERASURE_REQUEST` flow already keys suppression on
  these hashes, so erasure semantics extend unchanged.
- **Abuse cases considered:** slot oversell (MUST NOT + vector,
  invalid C); sold-out pinging (MUST NOT ping with zero slots); fabricated
  `CONTACTED` reports to pollute dedup lists (authenticated sender,
  per-lead scope, dispute via existing ack/rejection paths); hash
  enumeration by a pair insider (already insider to that pair — the same
  threat model §9 accepted for `phone_hash`, noted for reviewers);
  contacted-list growth as a storage side channel (bounded by lead
  contacts; no bulk suppression sync in scope).
- **Universal-core audit (§12):** adds zero vertical- or market-specific
  fields; slot accounting is the runtime half of the already-core
  `exclusivity`/`max_buyers` blocks; the reference network's wire
  mechanisms map 1:1 minus its unsafe hash (the "legacy production wire
  contract fields map 1:1" checklist item). The checklist runs again at
  implementation merge.

## SDK, OpenAPI, MCP, conformance, and operational impact

Draft status does **not** authorize any of the following; they are the
implementation-phase change gates (roadmap: "conformance vectors, SDK
helpers, and OpenAPI/MCP surfaces updated together"; `governance/LEP.md`
traceability gate; `docs/V1.1-ROADMAP.md` change gates 1–7).

- **Schemas:** additive property drafts — `ping.json`
  (`slots_available`, `minimum: 1`, integer), `bid.json`
  (`slots_claimed`, `minimum: 1`), `post.json` (`contacted_hashes`,
  `array` of `^[a-f0-9]{64}$`, min 0). No `$defs/exclusivity` change.
- **Examples:** new positive examples (this document's four) added to
  `examples/`; the four invalid shapes land in the negative corpus
  (`test-vectors/sdk/validation-corpus.json` and message-level vectors).
- **Conformance vectors:** valid ping-with-slots/bid-with-claim/
  post-with-hashes roundtrip; SHA-1-shaped and short-digest rejection;
  exclusive+multi-slot contradiction rejection; oversell attempt yields
  no second post (platform behavior vector, L2/L3); `CONTACTED` event
  accepted as open-enum value with pass-through storage (L1 rule
  regression); absent-vs-empty `contacted_hashes` distinction.
- **SDKs (all 10 languages: python, typescript, go, rust, java, kotlin,
  ruby, php, csharp, swift):** generated models for the three fields
  (closed-object regeneration, schema bundles + SHA-256 manifests re-sync
  via `tools/check_sdk_schema_sync.py`); helpers: a slot-claim/remaining
  accounting shim, and contacted-hash builders that **reuse the existing
  per-pair `phone_hash`/`email_hash` HMAC utilities** — no new hashing
  primitive, no SHA-1 anywhere (grep-gate: reject `sha1` adoption in SDK
  hash helpers).
- **OpenAPI:** `submitBid` request body gains `slots_claimed`; ping/post
  surfaces expose `slots_available`/`contacted_hashes` on existing
  schemas. No new endpoint — contact reporting rides `submitEvent`.
- **MCP:** `submit_bid` tool arguments gain `slots_claimed`; the
  server's pre-send schema validation (which loads the published
  schemas) automatically enforces the allowlist change; buyer-facing ping
  handling surfaces `slots_available` in tool responses.
- **Reference platform:** slot counters per lead (`remaining =
  max_buyers − delivered`, decremented atomically at post; ping-stop
  rule), `CONTACTED` ingestion + per-pair re-keying on subsequent posts,
  capability advertisement gains `1.1` in `lcp_versions` behind the
  release.
- **Operational:** metrics and alerting in the rollout section;
  documentation of the absent-vs-empty semantics for integrators; ops
  note that pair-secret rotation mid-lead-invalidates prior contacted
  hashes (deployment concern mirroring §9).

## Alternatives considered

- **Keep slot/dedup behavior deployment-specific or registry-extension
  only.** Required feasibility analysis per the decision rules, and it
  fails on the current tree: the extension registry places extension data
  at `payload.extensions` and promises unknown extensions are ignored by
  citing SPEC.md §6's unknown-fields rule — but every message schema that
  would carry this data (`ping`, `bid`, `post`) is
  `additionalProperties: false` and defines no `extensions` object
  (only `offer.json` does), so a conformant schema-validating receiver
  rejects extension-framed slot fields exactly as hard as protocol
  fields. A core-schema change to open those objects to extensions would
  have the same compatibility blast radius as this proposal while
  standardizing nothing (each deployment still invents its own slot
  accounting and hash semantics — the cross-platform buyer integrating
  N networks re-implements mapping every time, which is the pain this
  item exists to remove). Enforcement is platform behavior, not buyer
  data, so a private per-platform extension can carry a knob but never
  the contract. Extensions retain a real role for what is genuinely
  party-specific: buyer slot *policy* (e.g. `{org}.marketplace.slot_
  pricing`) on offers — and LEP-0001's registry precedent shows the
  process path if maintainers prefer that shape. Rejected as
  substitute; accepted as complement.
- **Flip the core default to `exclusive` (aligning with the US norm).**
  Rejected — changes the meaning of existing v1.0 bytes under a MINOR
  (not additive; §12/§11 market-neutrality too). See the reasoned
  position.
- **Adopt the reference network's unsalted SHA-1 contact hashes.**
  Rejected outright — enumerable keyspaces invert them; §14 blocker B3
  already condemned this design for `phone_hash`. Mechanism yes, hash
  never.
- **One global salt / cross-buyer-common hash so buyers compare directly.**
  Rejected — a digest every buyer can match is a digest every buyer (and
  anyone holding the salt) can enumerate, and it creates exactly the
  cross-buyer linkage §9's per-pair model exists to prevent. The
  platform re-keying per recipient pair keeps correlation pair-scoped.
- **New message types (`slot_update`, `contact_report`).** Rejected —
  `message.type` is closed, adding types demands the two-adopter evidence
  bar and a universal audit for capabilities the existing four fields and
  the open-enum `event` already deliver with a strictly smaller surface.
- **Rely on `dedup_window_hours` alone (status quo).** Rejected — it is a
  sender hint for dedup within one buyer's stream; it cannot tell a
  buyer what other slot holders did or how many copies remain, which is
  the gap that triggered this item.

## Rollout, migration, observability, and rollback

1. **Schema + vectors first** (implementation phase, gated on acceptance):
   additive fields land in schemas, examples, vectors, and the
   §6/§8/§9 spec-text clarification. Behavior for every message that
   omits the new fields is bit-for-bit v1.0.
2. **Capability negotiation is the switch.** The reference platform
   advertises `1.1` in `lcp_versions` and emits slot fields only to
   receivers advertising v1.1; buyers adopt in any order, no coordinated
   flag day. Platform-side accounting ships behind the release with the
   same gate (feature-flag-able per LEP-0001's rollout pattern).
3. **SDK/OpenAPI/MCP in the same release** as schemas + vectors (change
   gates 4–5); contacted-hash helpers documented as the only sanctioned
   builder (prevents ad-hoc SHA-1 drift in integrator code).
4. **Observability:** slot consumption histogram and time-to-sold-out per
   shared lead; oversell attempts (MUST run permanently zero — alert on
   any nonzero as a conformance bug); zero-slot pings (same rule);
   `CONTACTED` report rate and contacted-hash match rate per post (the
   dedup value signal); duplicate-contact dispute rate (before/after
   adoption evidence for the next marketplace items).
5. **Rollback:** stop emitting the fields (absent = v1.0 semantics is the
   designed idle state) and/or drop `1.1` from advertised `lcp_versions`;
   disable the platform accounting feature. No data-format migration, no
   stored-envelope rewrite: slot counters are platform-internal bookkeep-
   ing reconstructible from delivery history, and received contacted
   hashes are disposable derived data.

## Deferred questions

- **Slot refund on no-show / dispute / expiry.** When a delivered slot
  dies (`REJECTED`, `EXPIRED`, `DISPUTED → REFUNDED`, or the §9
  agent-timeout auto-`EXPIRED` re-offer path), does its slot return to
  the pool? Current draft: the budget is on *delivery*, refund is
  unspecified — a deliberate silence, not an oversight, because refunding
  changes publisher revenue and buyer competition economics. Trigger:
  first production shared-lead campaign with measured
  post-delivery-failure rates, plus evidence from a second independent
  platform; decide alongside the delivery-receipt (`PROCESSED`) deferred
  item in §14.
- **Per-market commercial defaults.** Whether vertical/marketplace
  profiles may declare recommended slot defaults per market (e.g.
  "US home services: prefer explicit exclusive") without touching the
  core `shared` default. Trigger: two or more independent US adopters
  requesting profiled defaults — evidence of an interop need worth
  standardizing, versus deployment config that already covers it.
- **`normalized_email` definition.** §9 hashes
  `normalized_email` but the spec does not norm the normalization
  (case-folding, local-part rules, plus-addressing). Contacted-hash
  interop fails silently if pairs normalize differently. Trigger: this
  LEP's implementation phase — pin the normalization in §9 (or its
  extension) before shipping vectors, since both this exchange and
  `email_hash` need it.
- **Non-phone/email contact channels in `via`.** Chat-app IDs and other
  identifiers outside the two hash domains. Trigger: first adopter
  marketing leads through a channel whose contact identifier is not an
  E.164 phone or email; until then `via` is an open enum and nothing
  breaks.
- **Array bounds and ordering for `contacted_hashes`.** No cap in this
  draft (a lead's contact attempts are naturally bounded); if a real
  deployment reports bulk suppression syncs through this field, cap it
  and route bulk to a dedicated mechanism. Trigger: observed array sizes
  in production posts.
- **Direct buyer→buyer exchange.** This design routes everything through
  the platform/publisher that holds the pair secrets. A peer-to-peer
  variant (buyers exchanging keyed hashes without the platform) changes
  the trust topology entirely. Trigger: a marketplace with no central
  platform that still needs slot coordination — not LCP's current model.

**Revisit trigger:** first v1.1 shared-lead production deployment, or
adopter evidence on refund economics and per-market defaults.

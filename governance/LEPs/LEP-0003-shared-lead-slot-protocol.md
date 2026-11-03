# LEP-0003 — Shared-lead slot allocation and contacted-party dedup exchange

- **Title:** Shared-lead slot allocation and contacted-party dedup exchange
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-02
- **Last revised:** 2026-11-03 (ARB L-Platform dossier findings F1–F10
  dispositioned; see "Revision 2026-11-03 (post-dossier)")
- **Status:** **Accepted** — accepted 2026-11-03; implementation authorized
  (post-dossier revision #157 + re-review #161 dispositions)

## Decision (maintainer, 2026-11-03)

**Accepted.** Recorded by the maintainer (randybeno) under the ARB
sign-off model. Basis: independent re-review of the revised record
(<https://github.com/SpearSystems/LCP/issues/124#issuecomment-6591860350>)
— all ten first-round findings verified resolved at `22258eb` by
execution (including byte-exact reproduction of the oversell race fix
design and both reference contact-hash digests), verdict FINDINGS with
only record-level items RF-1–RF-6, every one of which was applied in
PR #161 exactly as the re-review prescribed (RF-1 digest recomputed per
the F7 rule, not padded). Implementation proceeds under the named
phase-gates T1–T10 tracked at
[#160](https://github.com/SpearSystems/LCP/issues/160); the change-gates
of `docs/V1.1-ROADMAP.md` §Change gates apply as a unit (schemas,
examples, vectors, SDK bundles + manifests, OpenAPI/MCP, compat notes,
changelog). Any wire-shape deviation discovered during implementation
returns this record to Review before merge.
- **Target version:** v1.1.0 (MINOR; see Compatibility and versioning)
- **Evidence corrections applied:** valid post-example `exclusivity` block
  fixed per ARB retro-audit at `dee5584` (superseded by the 2026-11-03
  revision and acceptance above)
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
- **Reference implementation:** platform-enforced slot accounting — the
  budget counts **distinct awarded buyers** (`SPEC.md:312` defines
  `max_buyers` as "maximum number of **buyers** who may receive the
  lead"), delivery stays per `(lead, offer)`, and the rule is enforced
  at one write-time decision point with DB-backed uniqueness (see
  "Slot accounting (implementation-phase requirements)" and "Units"
  below; never oversell) — plus contacted-hash propagation on post and
  `CONTACTED` event ingestion.
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
| `slots_available` | integer | Remaining **buyer-award** slots of the lead that can still be sold at ping time (slots count buyers, not copies — see "Units" below), **including** the slot this ping's recipient may win. `minimum: 1`. Absent = the platform does not advertise slot state (v1.0 behavior). |

**Units (normative).** The slot budget and `slots_available` use the
unit SPEC already sets: `max_buyers` is the maximum number of
**buyers** who may receive a shared lead (`SPEC.md:312`). One awarded
buyer consumes exactly **one** slot regardless of how many matching
offers they hold; delivery remains per `(lead, offer)`, so the number
of delivered post **copies** for a lead is not the budget and can
legally exceed it (a single awarded buyer holding two matching offers
receives two posts inside their one slot). The reference platform's
current award path slices per-**candidate**
(`winners = candidates[:max_winners]`, `router.py:680-683`), which is
exactly the conflation the unit statement removes: one buyer with two
matching offers measured `posts=2, distinct_buyers=1` against a
`max_buyers: 2` budget while a second bidding buyer received nothing
(dossier F3, row 19). The implementation PR must state the unit in
SPEC §3 slot semantics and land #153 **T4**
(`plat: test_max_buyers_bounds_distinct_buyers`).

Semantics:

- `exclusivity: "exclusive"` is exactly the one-slot special case: when
  present, `slots_available` MUST be `1` (an exclusive ping advertising
  more is a semantic error; see invalid example B — rejected by the
  drafted schema conditional, not just by prose; see Schemas below).
- For `shared` leads, `slots_available ≤ max_buyers` (when the lead
  declares one) is a **platform-side send-time invariant only**: the
  ping allowlist carries the `exclusivity` hint string and no
  `max_buyers` (`schemas/ping.json:163-168`), so a pinged buyer can
  neither verify the relation nor know whether a `shared` ping has a
  declared budget at all — this proposal makes **no
  receiver-verifiability claim** for the inequality (dossier F10). The
  undeclared case is normative: a `shared` lead without `max_buyers`
  has a slot budget of **1**, matching the reference platform's
  implicit cap (`max(1, int(exclusivity.get("max_buyers", 1)))`,
  `router.py:680-683`); an advertised `slots_available: 5` on an
  uncapped `shared` lead is therefore a sender-conformance failure,
  not a freedom. The platform MUST NOT send a ping with
  `slots_available: 0` — zero remaining means stop pinging, not ping
  with zero.
- The field is an integer count. It is non-PII and carries no consumer
  data, so it is compatible with the ping strict allowlist design
  (`LCP-008 PII_IN_PING` protection is preserved — the allowlist simply
  gains one reviewed non-PII field).

### 2. Bid — `slots_claimed`

Additive optional field on `bid` (`schemas/bid.json`):

| Field | Type | Notes |
|---|---|---|
| `slots_claimed` | integer | Buyer slots this buyer claims if selected. **`minimum: 1, maximum: 1` at v1.1** — single-slot awards only; multi-slot agency claims have no delivery path today and are deferred (§2 bullets, Deferred questions). Only meaningful with `decision: accept`; absent = 1. |

- The claim is a request, not a reservation. The platform awards slots at
  **post** time (delivery is the claim event; bids are non-binding, per
  the existing auction flow where "losers receive no further messages").
- The platform MUST NOT award more buyer slots than remain. Because
  the v1.1 range is exactly 1, a claim can never itself exceed the
  remaining count — the oversell surface is the **award path**, not
  the claim value, and it is currently unenforced (see invalid example
  C and "Slot accounting (implementation-phase requirements)" below).
- **`slots_claimed > 1` (the agency/network case: one buyer, several
  downstream vendors) is deferred, not silently kept.** It has **no
  delivery path** on the current guard: the award skip is keyed per
  `(lead, offer)` — `has_delivery_for_offer` at `router.py:693`
  enforced by `storage.py:2183-2188` — so a second copy to the same
  buyer is refused (reproduced: `winner posts=1, after extra pass=1`,
  dossier F4 row 18), and no response surface (post or ack) ever
  tells the winner **how many slots it was awarded**, which is what
  "awards the remainder" would need to be observable. v1.1 caps the
  claim at 1; lifting the cap requires the awarded-count wire signal,
  the `(lead, buyer)`/slot-ordinal delivery shape, and #153 **T5**
  (`plat: test_slots_claimed_multi_award_within_budget`) — recorded
  as a Deferred question with that trigger.

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
- **The preimage is the wire value, byte-for-byte (normative).** The
  only hash-input behavior that exists anywhere in this repository is
  the reference platform's `phone_hash` builder: `hmac.new(
  buyer_secret.encode(), phone.encode(), hashlib.sha256)` over the
  **verbatim** `consumer.phone` string of the delivered lead — no
  folding, no stripping, no separator handling (`messages.py:89-93`).
  `contacted_hashes` inherits exactly that rule for phone entries: the
  preimage is the `consumer.phone` string the platform delivered,
  hashed under the receiving buyer's pair secret. Nothing is
  "canonicalized", because nothing else is implemented.
- **Email entries are blocked until §9 norms the normalization.**
  `normalized_email` appears only as a term — `SPEC.md:97, 375`
  use it, zero sites define it (the phone-side sibling
  `normalized_e164` is the term at `SPEC.md:743`); the schemas echo the description
  (`schemas/core.json:22`, `schemas/ping.json:41`); no published
  vector pins a preimage→digest pair (`test-vectors/sdk/hmac.json`
  covers message *signing* only); the reference platform never
  computes an email hash (`messages.py` hashes phone only); and no
  SDK contains a dedup-hash builder at all (re-verified by grep at
  `0ad98e85`). This is measured, not inferred: divergent-but-plausible
  inputs produce **different digests silently** —
  `+15125550147` vs `+1 (512) 555-0147`, `Jordan@Example.com` vs
  `jordan@example.com`, `jordan+crm@example.com` vs
  `jordan@example.com` all digest apart (dossier F7, row 26) — so two
  honest implementations can kill the dedup value with no error
  anywhere. Consequence: **no email-form `contacted_hashes` entry
  ships** until SPEC §9 pins `normalized_e164`/`normalized_email` and
  #153 **T9** publishes executed vectors
  (`lcp-sdk-dedup-hash-001..N`, run by all ten SDKs and the reference
  platform, closing ledger row `SEC-08`).
- **Reference digests for implementation conformance.** Proposed
  fixture form for the vector file `test-vectors/sdk/dedup-hash.json`:
  entries of
  `{"name": …, "secret": "<pair secret, UTF-8>", "domain": "phone|email", "preimage": "<canonical string>", "digest": "<lowercase hex HMAC-SHA256>"}`.
  Until that file publishes, these two locally computed digests are
  the conformance targets (python3 `hmac` at 2026-11-03 over the
  example pair secret bytes `pair-secret-for-vectors-0123456789`):
  - phone `+15125550147` →
    `c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12`
    (the byte-for-byte rule; equals what `messages.py:89-93` computes
    for that value and secret)
  - email `Jordan@example.com` →
    `d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4`
    (preimage under the **proposed** §9 canonical — surrounding
    whitespace trimmed, domain ASCII-lowercased, local-part bytes kept,
    plus-addresses distinct — so this row is a proposal fixture, not a
    published vector; the §9 pin may still move it)
- **Re-keying a reported digest requires the plaintext.** HMAC of a
  received digest under another pair's secret is **not** a re-key
  (measured: `re-HMAC-of-report ≠ required(C-keyed)`; a plaintext
  re-key is `True`, dossier row 27). The platform may therefore
  re-key only from the contact values it delivered itself — never
  from `CONTACTED` report digests — which is exactly what the valid
  post example below asserts.

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
under its pair secret); the platform stores them and re-keys — **only
from plaintext it holds** (the lead values it delivered; §3 on
re-keying) — per recipient pair for subsequent posts. `submitEvent`
(REST) already exists as the transport.

- **Zero schema cost, nonzero platform cost (normative).** The
  reporting direction as drafted is refused today by the reference
  platform for a properly scoped buyer: buyer-reportable event names
  are gated through the **closed** set `_BUYER_EVENT_TYPES`
  (`router.py:81-91`), enforced per sender at `router.py:940-949`, and
  `CONTACTED` is not in it — measured, the proposal's own §4 example
  from a delivered buyer with `scopes=["event:submit","bid:submit"]`
  returns `LCP-002 Sender is not authorized for this lead event`,
  while `DELIVERED` on the same transport and scopes returns
  `RECEIVED` (dossier F5, row 20). Acceptance today rides the default
  credential's wildcard scope (`storage.py:917` seeds `["*"]`;
  `auth.py:168-170` treats `*` as satisfying every scope check —
  routed observation O2, L-Security), which is not a conformance-grade
  path. The implementation PR **must**, as part of landing `CONTACTED`:
  add it to `_BUYER_EVENT_TYPES` (v1.1-gated), advertise it in
  `capabilities().events` (`router.py:1318` advertises twelve values
  and — measured — omits `CONTACTED`, contradicting the §8 surface
  the compatibility gate relies on), and land #153 **T6**
  (`plat: test_buyer_scoped_contacted_event_accepted_and_advertised`).
- **The report rule cannot be drafted; state what it is instead.**
  `event.details` is `additionalProperties: true`
  (`schemas/event.json:25-28`), so no schema change reaches into
  report payloads: a SHA-1-shaped string — or even a plaintext phone —
  inside `details` validates in both trees (dossier row 22). The
  "same per-pair rule as post" is therefore an **ingest-behavior
  requirement**, and the draft bounds it honestly: receivers MUST
  reject `CONTACTED` reports whose `details.contacted_hashes` contains
  an entry failing `^[a-f0-9]{64}$` or exceeds **32 entries per
  report**, and MUST treat oversized or malformed reports as a
  validation rejection, not a store. Beyond shape and count the trust
  model is explicit: the platform **cannot verify** that a reported
  digest matches a real contact (verification would require every
  pair's secret and normalization, §3), so entries are
  authenticated-sender attestations scoped to one `lead_id` — keyed
  HMACs are unmatchable by anyone outside the pair **and
  unverifiable-by-design for the receiver**; disputes ride the
  existing ack/rejection paths (abuse list below).
- **Replay and accumulation (corrects the earlier claim).** The event
  store dedupes by the event **`message.id`**, not by
  `idempotency_key`: `lifecycle_events.event_id TEXT PRIMARY KEY` and
  no idempotency column at all (`storage.py:626-632`; contrast
  `leads`/`attachments`), `insert_event` compares stored content by
  that id and raises `EventIdempotencyConflict` on reuse with
  different bytes (`storage.py:2226-2252`) — two reports sharing one
  `idempotency_key` with fresh message ids both persist (measured,
  dossier F9 row 21). Normative semantics: contact reports are
  **at-least-once**; exact re-sends of the same `message.id` collapse
  receiver-side, retried reports with new ids are **merged by digest
  set** (the contacted set per lead accumulates as a set union — the
  wire array carries no `uniqueItems`, duplicates are tolerated but
  meaningless), and the absent-vs-`[]` rule carries to the report
  direction: absent = nothing to report (don't send), `[]` = MUST NOT
  be sent as an event detail (it asserts nothing).

### 5. Slot accounting (implementation-phase requirement)

The `MUST NOT oversell` rule currently has **no enforcement point**
(dossier F2, rows 11-17), so the implementation PR for this LEP is
blocked until all four of the following land together:

1. **One write-time decision point.** The remaining-budget check and
   the post write occur in **one transaction**, counting distinct
   awarded buyers (Units, §1). Today the budget is a per-pass slice
   (`max_winners`/`winners = candidates[:max_winners]`,
   `router.py:680-683`), re-entry is allowed for a `POSTED` lead
   (`POSTED` is absent from the early-return status set,
   `router.py:636-637`), and the only guard is an advisory read in one
   critical section (`has_delivery_for_offer`, `storage.py:2183-2188`)
   whose write is a separate transaction. Reproduced races: two award
   passes whose slot-state reads overlap delivered **4 posts** against
   `max_buyers: 2` (row 14); two OS processes on the shipped Postgres
   backend delivered **3** post rows against a budget of 2, with a
   primary-key collision raised inside the losing replica's loop
   (row 15); the serialized control run holds (row 16) — the defect is
   the read→write window plus per-pass budgeting, not single-thread
   logic.
2. **DB-enforced uniqueness of awarded slots.** The deliveries table
   constrains only `message_id` (`message_id TEXT NOT NULL UNIQUE`,
   `storage.py:613-614`); a second `insert_delivery(kind='post')` for
   the same `(lead_id, offer_id)` was measured `accepted=2 rows=2`
   (row 17). The invariant must survive application bugs: a durable
   uniqueness constraint on the award — e.g. a unique index on
   `(lead_id, offer_id)` restricted to `kind='post'` rows, or a
   `(lead_id, slot_ordinal)` award table — plus the distinct-buyer
   count at the decision point (T4).
3. **Locked or leased expiry/re-offer sweep.** The auction-close
   trigger (`router.py:480-483`) is the platform's only multi-worker
   path back into `_route_lead` and takes neither a lease nor a row
   lock (`list_expired_open_pings`, `storage.py:1921-1926`), unlike
   every other worker path (`claim_due_deliveries` lease columns,
   `storage.py:2117+`; the Postgres backend's
   `FOR UPDATE SKIP LOCKED`, `postgres.py:231, 268`). Expiry sweeps
   claim under `FOR UPDATE SKIP LOCKED` (or a lease) before routing.
4. **Award-eligible ping state.** `_route_lead` builds candidates
   from every accepted bid without checking `ping.status`
   (`router.py:645-667`); the OPEN-ping requirement lives only in
   `submit_bid` (`router.py:373-380`), so a post-expiry bid that
   reached the store by another path stays award-eligible. The
   decision point filters non-OPEN/expired pings (dossier's
   cross-surface observation, recorded at #153).

Named blocking tests (#153): **T2**
`plat: test_slot_budget_never_oversells_under_concurrent_award` +
`plat: test_slot_budget_two_process_postgres_race`; **T3**
`plat: test_second_post_for_same_lead_offer_is_refused` (DDL in the
`deliveries` schema); **T4**
`plat: test_max_buyers_bounds_distinct_buyers`. Ledger honesty:
`docs/SPEC-COVERAGE.md:88` marks row `EXC-02` ("no over-award") green
on the strength of
`plat:test_exclusive_lead_overaward_refused_single_post_only`
(`test_adversarial_e2e.py:216`) — the exclusive, single-pass,
single-thread case; no shared multi-award or concurrent test exists
(routed observation O1); T2/T4 extend that row when they land.

### Valid example — ping with slots

*(Re-validated by execution 2026-11-03. `attributes` use only fields
defined and `ping_safe: true` in `verticals/home_services.json`
(`service_type`, `budget_band`) — the F1 fix; the previously drafted
`service_category`/`project_size_band` exist nowhere in the vertical
registry and the example failed the repository's own runner with
`LCP-008 PII_IN_PING` even after the v1.1 fields were added.)*

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
      "attributes": { "service_type": "gutters", "budget_band": "1000_5000" },
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

Two earlier parties were contacted (both by phone) by the first
slot holder; the platform re-keyed both digests under `buyer_002`'s pair
secret before posting. No plaintext contact identifiers appear in the
exchange. Phone-form only, deliberately: §3 gates any email-form
`contacted_hashes` entry behind the SPEC §9 normalization pin plus #153
**T9** vectors, and the reference platform hashes phone only today — an
example shipping an email-form entry would depict what this document
forbids shipping.

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

*(Re-validated by execution 2026-11-03. The single entry is derivable,
not decorative: it is §3's phone reference digest —
`HMAC-SHA256(pair-secret-for-vectors-0123456789, "+15125550147")` — the
delivered `consumer.phone` verbatim per §3's preimage rule, keyed with
the example pair secret — and it satisfies §4's `^[a-f0-9]{64}$` ingest
MUST. An earlier draft carried a 63-hex string the doc's own MUST
rejects; corrected per re-dossier RF-1.)*

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
          "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12"
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

**Honest status of "rejects" (dossier F8).** Under the original naive
property drafts (`slots_available` added with `minimum: 1` and nothing
else), this shape **validates** — measured `PASS` under the v1.1
overlay while the vector list promised a rejection test (row 8). The
contradiction *is* expressible because `ping.json`'s `exclusivity` is
a plain string enum (`schemas/ping.json:163-168`) and the object has
no existing `allOf`, so the drafted `ping.json` now carries a
top-level conditional — `if properties.exclusivity == "exclusive"`
(and present) `then properties.slots_available const 1` — and the
negative vector `lcp-neg-exclusive-multislot-001` lands in
`test-vectors/sdk/validation-corpus.json` plus
`test-vectors/sdk/negative/` (#153 **T7**). With that conditional the
overlay run recorded in the Revision section **rejects** this
construction; without it, neither schema nor vector would.

### Invalid example C — slot oversell

A lead has `max_buyers: 2` and **both buyer slots already awarded**
(two delivered posts to two distinct buyers; `slots_available` reflects
the remaining **buyer** budget per the Units statement — copies are not
the budget). Two further accepted bids arrive (buyers C and D). The
platform MUST NOT award either beyond the budget: distinct awarded
buyers MUST NOT exceed `max_buyers`, and re-entry for a `POSTED` lead
changes nothing. Under today's code this invariant is enforced only by
the advisory read described in §5, and the dossier **reproduced**
over-award: two overlapping award passes delivered 4 posts against a
2-buyer budget; two OS processes on the shipped Postgres backend
delivered 3 (rows 14-15). Additionally, a `slots_claimed: 2` bid is
**out of range for v1.1** (`maximum: 1`, §2) — multi-slot claims are
deferred, not silently possible. Award-oversell is a conformance
failure with named blocking tests T2/T3/T4; which eligible bid to
re-route is the platform's choice.

### Invalid example D — v1.1 field to a v1.0 receiver

`GET /v1/lcp/capabilities` for `buyer_009` returns
`"lcp_versions": ["1.0.0"]`, yet a platform pings `buyer_009` with
`slots_available: 2`. `buyer_009`'s L2 conformance validates the ping
against the v1.0 strict allowlist and rejects it (`LCP-008`/validation
error, per SPEC.md §4). This is the compatibility boundary the
capability-gating rule in the Compatibility section exists to prevent;
that rule's match mechanics (full-semver elements, parsed comparison,
hosting obligation, OpenAPI pattern) are normed there as an
implementation-phase deliverable (F6/T10), not borrowed from today's
one-sentence §8.

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
- **Consequence (normative):** v1.1 slot fields are capability-gated,
  not ignore-gated. A sender MUST include `slots_available` only in
  pings to receivers whose §8 capability advertisement supports v1.1
  — compared as **parsed semver** (an advertised element with
  `major == 1` and `minor >= 1`), never as the string literal `1.1`,
  and every advertised element MUST be a full `major.minor.patch`
  version — (and likewise gate `slots_claimed`, `contacted_hashes`,
  and `CONTACTED` on the receiver's declared v1.1 support).
- **Honest status of the cited surface (dossier F6).** What exists
  today is the field *name*, not a consumable negotiation rule:
  SPEC.md §8 is one sentence listing `{ lcp_versions, … }` with no
  element format, no match rule, and no statement of who must host
  `GET /v1/lcp/capabilities` for a *ping receiver* or whether
  discovery is obligatory below L3 (`SPEC.md:594-601`; capability
  discovery is named only as an L3 item, `SPEC.md:594-595`); OpenAPI
  types `lcp_versions` as `array of string` with no pattern and does
  not require it (`api/lcp-openapi.yaml:798-804`); the only shipped
  producer advertises `["1.0.0"]` (`router.py:1310`). And the earlier
  wording of this very bullet ("include protocol version `1.1`") is
  self-inconsistent with every semver-form advertisement: exact
  membership of `1.1` is `False` for `1.1.0`/`1.0.0`+`1.1.0` forms
  while prefix membership also matches `1.10.0` (measured, row 24) —
  the gate as previously drafted could never open, or would open on
  the wrong version. **The implementation PR MUST add this exact
  normative text** (it is this proposal's spec-text deliverable, not
  an existing rule): (a) SPEC.md §8 — "Every `lcp_versions` element
  MUST be a full `major.minor.patch` semantic version. A peer supports
  protocol extensions of minor `m` when its advertised set contains
  an element with `major == 1` and `minor >= m`; senders MUST compare
  parsed versions, never string literals or prefixes. Every endpoint
  that initiates traffic to a counterparty (ping senders included)
  MUST host `GET /v1/lcp/capabilities` for that counterparty."
  (b) OpenAPI — `lcp_versions` items gain pattern
  `^\d+\.\d+\.\d+$` and become required. (c) Test #153 **T10**
  `plat: test_capabilities_lcp_versions_gate_match_rule` asserts the
  element format and both gate directions. Invalid example D below
  illustrates the *absence* of this rule's protection; with (a)–(c)
  landed it is the mechanism.
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
  Invalid C). Contact reports replay-proof by **event `message.id`**
  dedupe (`lifecycle_events.event_id` primary key, §4) — the earlier
  claim in this section that `idempotency_key` covered report replay
  was factually wrong (the event store has no idempotency column at
  all; measured, dossier F9) and is replaced by the at-least-once +
  receiver-side-dedupe semantics in §4.
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
  contacted-list growth as a storage side channel (reports capped at
  32 entries each (§4); per-lead accumulation is a set union bounded
  by distinct contacts; bulk suppression sync stays out of scope with
  the array-bound deferred question).
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
  (`slots_available`, integer, `minimum: 1`) **plus the top-level
  conditional** `if properties.exclusivity == "exclusive" (present)
  then properties.slots_available const 1` (invalid-B fix, F8/T7),
  `bid.json` (`slots_claimed`, integer, `minimum: 1, maximum: 1` at
  v1.1 — F4), `post.json` (`contacted_hashes`, `array` of
  `^[a-f0-9]{64}$`, min 0; no `uniqueItems` — duplicates merge
  receiver-side per §4). No `$defs/exclusivity` change; no
  `event.json` change (`details` stays open by design; the report
  rule is ingest behavior, §4).
- **Examples:** new positive examples (this document's four) added to
  `examples/`; the four invalid shapes land in the negative corpus
  (`test-vectors/sdk/validation-corpus.json` and message-level vectors).
- **Conformance vectors:** valid ping-with-slots/bid-with-claim/
  post-with-hashes roundtrip; SHA-1-shaped and short-digest rejection;
  exclusive+multi-slot contradiction rejection **by the new schema
  conditional** (`lcp-neg-exclusive-multislot-001`, T7); oversell
  attempt yields no post beyond the distinct-buyer budget — including
  the two-pass concurrent and two-process Postgres races
  (`plat:…`, T2/T3/T4, platform behavior vectors, L2/L3); scoped-buyer
  `CONTACTED` acceptance + advertisement (T6); replay-idempotence of
  `CONTACTED` reports under §4's `message.id`-dedupe / set-union
  semantics (`plat: test_contacted_report_replay_is_idempotent`, T8);
  capability
  `lcp_versions` semver match rule (T10); published `dedup-hash`
  vectors executed by the reference platform and all ten SDKs (T9);
  `CONTACTED` event accepted as open-enum value with pass-through
  storage (L1 rule regression — recorded honestly:
  `test-vectors/conformance.py` ignores each vector's `checks` array
  (no reference to the key anywhere in the runner), so labels like
  `event_open_enum_pass_through` on `L3-006`
  (`test-vectors/l3-advanced.json:287`) are currently decorative
  (routed O3) — the implementation PR wires `checks` or ships real
  platform tests); absent-vs-empty `contacted_hashes` distinction in
  both directions (§4); and a tooling test that validates every
  fenced `### Valid example` block in `governance/LEPs/*.md` through
  the platform's own `SchemaValidator` (#153 **T1**,
  `tool: test_lep_wire_examples`) so the F1 class of defect cannot
  silently return.
- **SDKs (all 10 languages: python, typescript, go, rust, java, kotlin,
  ruby, php, csharp, swift):** generated models for the three fields
  (closed-object regeneration, schema bundles + SHA-256 manifests re-sync
  via `tools/check_sdk_schema_sync.py`); helpers: a slot-claim/remaining
  accounting shim, and contacted-hash builders that **reuse the existing
  per-pair `phone_hash`/`email_hash` HMAC utilities** — no new hashing
  primitive, no SHA-1 anywhere (grep-gate: reject `sha1` adoption in SDK
  hash helpers). Recorded honestly (dossier F7/H5): **no SDK contains a
  dedup-hash builder today** — `phone_hash`/`email_hash` appear in
  generated models and schema bundles only — so the builders are new
  code executed against the T9 vectors, hashing the **wire bytes
  verbatim** per §3, and no email builder ships before §9 pins the
  normalization.
- **OpenAPI:** `submitBid` request body gains `slots_claimed`; ping/post
  surfaces expose `slots_available`/`contacted_hashes` on existing
  schemas. No new endpoint — contact reporting rides `submitEvent`.
- **MCP:** `submit_bid` tool arguments gain `slots_claimed`; the
  server's pre-send schema validation (which loads the published
  schemas) automatically enforces the allowlist change; buyer-facing ping
  handling surfaces `slots_available` in tool responses.
- **Reference platform:** the §5 enforcement set lands before any
  feature flip — one write-time, locked decision point counting
  distinct awarded buyers, DB-enforced `(lead, offer)`/slot-ordinal
  uniqueness on post deliveries, expiry/re-offer sweeps under
  `FOR UPDATE SKIP LOCKED` (or a lease), ping-status filtering at the
  decision point; `CONTACTED` ingestion **plus** its addition to
  `_BUYER_EVENT_TYPES` and `capabilities().events` (F5/T6) + per-pair
  re-keying on subsequent posts **from held plaintext only** (§3);
  capability advertisement gains a full-semver v1.1 element
  (`1.1.0`) in `lcp_versions` behind the release (F6/T10).
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
   advertises a full-semver v1.1 element (`1.1.0`) in `lcp_versions`
   and emits slot fields only to receivers whose advertisement parses
   to `major == 1, minor >= 1` (never string-prefix matching — F6);
   buyers adopt in any order, no coordinated flag day. Platform-side
   accounting ships behind the release with the same gate
   (feature-flag-able per LEP-0001's rollout pattern) — and the §5
   enforcement set (T2/T3) lands **before** the flip, since the flip
   is what activates the oversell surface.
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
   designed idle state) and/or drop every v1.1-or-higher minor element
   from advertised `lcp_versions`;
   disable the platform accounting feature. No data-format migration, no
   stored-envelope rewrite: slot counters are platform-internal bookkeep-
   ing reconstructible from delivery history, and received contacted
   hashes are disposable derived data.

## Revision 2026-11-03 (post-dossier)

The independent ARB L-Platform review dossier on [#124](https://github.com/SpearSystems/LCP/issues/124#issuecomment-6579188344)
(head `0bbc1437`, verdict **FINDINGS**, 10 findings + 3 routed
observations; durable conversions tracked at
[#153](https://github.com/SpearSystems/LCP/issues/153)) reviewed this
Draft adversarially against `SPEC.md`, `schemas/`, `verticals/`, and
the reference platform's award path. This revision dispositions F1–F10
**in the record**; it changes no status, no registry row, and
authorizes no implementation (Draft gates unchanged). Dispositions:

| Finding | What changed here |
|---|---|
| **F1** (major) — the ping example's `attributes` (`service_category`, `project_size_band`) exist nowhere in `verticals/home_services.json`, so the example failed the repository's own validator/runner (`LCP-008 PII_IN_PING`) **independently of the v1.1 fields** | Example fixed to `service_type: "gutters"` / `budget_band: "1000_5000"` (defined, `ping_safe: true`); note added under the example; the four invalid shapes re-tested; recurrence gate named (`tool: test_lep_wire_examples`, T1). No vertical-schema change (the scope promise holds). |
| **F2** (major) — `MUST NOT oversell` had **no enforcement point**: per-pass slice, advisory read vs separate write, `message_id`-only constraint, unlocked re-offer sweep, `POSTED` re-entry; oversell reproduced in-process (4 posts vs 2) and cross-process on Postgres (3 vs 2) | New normative **"Slot accounting (implementation-phase requirements)"** (§5): single write-time decision point counting distinct buyers, DB-enforced uniqueness per awarded slot, leased/`FOR UPDATE SKIP LOCKED` sweeps, ping-status filtering; reproduced races cited as the problem evidence; **T2/T3** named as blocking tests; the scope bullet's `max_buyers − delivered posts` formula replaced by the buyer-unit definition. |
| **F3** (major) — budget unit contradiction: §3 (`SPEC.md:312`) counts **buyers**; this LEP counted delivered **posts**, so one buyer with two offers consumed a two-buyer budget (measured) | "Units (normative)" under §1: a slot is a buyer award; one buyer = one slot regardless of offer count; delivery per `(lead, offer)`; copies are never the budget. Ping table, bid section, invalid C, the refund deferred item, and the T4 test all aligned. |
| **F4** (major) — `slots_claimed > 1` has no delivery path (per-`(lead, offer)` guard; awarded count unobservable on the wire) | v1.1 scope narrowed: schema `maximum: 1`; the agency framing replaced with the measured behavior + a new **Deferred question** with its trigger and **T5** named. |
| **F5** (major) — the reporting direction is refused today for properly scoped buyers (`CONTACTED` absent from `_BUYER_EVENT_TYPES`, measured `LCP-002`), unadvertised in `capabilities().events`, and the open `details` lets anything through | §4 gained the scoped-buyer MUST (allowlist + advertisement changes, **T6**), the wildcard-default-scope path recorded as routed observation O2 (not a conformance path), the explicit report bounds (32-entry / 64-hex ingest rule) and trust model (format+count sanity only; keyed HMAC unverifiable-by-design), and corrected replay semantics. |
| **F6** (major) — the capability gate cited text that defines nothing (§8 one sentence; OpenAPI untyped-ish array; `["1.0.0"]` the only producer; literal `1.1` unsatisfiable, prefix wrong) | The Compatibility section rewritten honestly: what exists is quoted with line cites; the exact normative text the implementation PR must add (element format, parsed-semver match rule, hosting obligation for ping initiators, OpenAPI pattern) is specified here; rollout/rollback wording moved to full-semver elements; **T10** named; invalid D points at the corrected rule. |
| **F7** (major) — the hash preimage is normed nowhere; only `consumer.phone` verbatim exists; email hashing implemented nowhere; re-keying a reported digest is impossible without plaintext | §3 gained: the verbatim wire-bytes preimage rule (matching `messages.py:89-93`), the honest "no email behavior exists to inherit" statement, the gate (no email entries until T9 vectors), proposed fixture form, two locally computed reference digests (labeled conformance targets), and the plaintext-only re-key constraint (`router.py` post builder is the only source). |
| **F8** (minor) — invalid B validated under the naive draft while vectors promised rejection | Drafted `ping.json` now carries the expressible conditional (`exclusive ⇒ slots_available const 1`), negative vector `lcp-neg-exclusive-multislot-001` (**T7**) promised, invalid B text made honest, conditional re-run recorded. |
| **F9** (minor) — "`idempotency_key` covers replay of contact reports" factually wrong (dedupe is `event_id`); accumulation undefined; report-direction absent-vs-`[]` unstated | The privacy bullet corrected; §4 defines at-least-once + receiver-side `message.id` dedupe, set-union accumulation with digest merge, absent-vs-`[]` for reports. Conversion test **T8** (`plat: test_contacted_report_replay_is_idempotent`) named in the impact list (re-dossier RF-6). |
| **F10** (minor) — `slots_available ≤ max_buyers` unverifiable by the receiver (no `max_buyers` on the allowlist); undeclared-`shared` case left unbounded while the platform caps at 1 | §1 states the invariant platform-side only, bans receiver reliance, and norms the undeclared budget to 1 (matching the router's implicit cap). |
| **O1/O2/O3** (routed, carried) | Implementation-phase test obligations recorded in §5/§4/impact: `EXC-02` ledger honesty (extended by T2/T4), scoped buyer credentials for T6 (the wildcard path is not a conformance path), `conformance.py` ignoring `checks` arrays (wire it or ship real tests). The dossier's cross-surface note (`ping.status` unchecked in `_route_lead`) became §5 item 4. |

**Example re-validation (executed 2026-11-03).** Every `### Valid
example` block and every invalid shape in this document were run
against the **published** v1.0 schemas and a v1.1 draft overlay
carrying exactly this document's corrected drafts (`slots_available`
min 1 + the invalid-B conditional; `slots_claimed` 1..1;
`contacted_hashes` 64-hex pattern), using the repository's own
`lcp_platform.validation.SchemaValidator` (`jsonschema 4.26.0`) and
`test-vectors/conformance.py::validate_message` for the vertical /
ping-safe layer. Results (full commands and outputs recorded on the
revision PR): the corrected ping example and the bid/post/event
examples `PASS` under the overlay; under the published tree three
`REJ` exactly as invalid D predicts (ping/bid/post — closed objects),
while the `CONTACTED` event example `PASS`es — that PASS is §4's
zero-schema-cost mechanism in action (open `details`);
invalid A `REJ` (pattern), invalid B `REJ` (conditional),
`slots_available: 0/-1` `REJ` (minimum), `slots_claimed: 2` `REJ`
(maximum), duplicate/`[]` report shapes behave per §4. The baseline
repository gates (`tools/check_lep_registry.py`,
`tools/check_doc_citations.py`, `test-vectors/conformance.py`,
`tools/check_reserved_fields.py`) pass on this revision unchanged.

**Re-review corrections (2026-11-03).** The ARB re-dossier of the
revision (posted on #124, dispositions tracked at #160) verified F1–F10
resolved but raised six record-level findings RF-1–RF-6; this commit
applies all six (citations re-verified at the branch SHA):
- **RF-1** (major) — the `CONTACTED` event example's digest was 63 hex
  chars, failing this document's own §4 ingest MUST (the F1 copy-the-example
  defect class, invisible to T1's open-`details` schema check). Replaced
  with the true digest under §3's verbatim preimage rule and the example
  pair secret — `hmac.new(b"pair-secret-for-vectors-0123456789",
  b"+15125550147", sha256)` → `c47aff33…4a2e12`, recomputed by execution,
  equal to the §3 phone reference digest — with the derivation stated
  under the example; not padded.
- **RF-2** — the post example's narrative asserted a prior **email**
  contact in `contacted_hashes` while §3 gates email-form entries behind
  the §9 pin + T9 (and the platform hashes phone only today); both prior
  parties are now phone-form and the note cites the gate.
- **RF-3** — wildcard-scope cite `storage.py:916` → `storage.py:917`
  (the seed statement; 916 is a `tenant_id,` tuple element) — verified
  at this SHA.
- **RF-4** — `normalized_email` was credited with `SPEC.md:97, 375, 720`;
  the email term's sites are `97` and `375` only — `720` at the reviewed
  SHA (and `743` at this SHA, after SPEC drift) is the phone-side
  `normalized_e164` site. Cited accordingly.
- **RF-5** — the re-validation summary claimed "the same four `REJ`
  under the published tree"; measured is three `REJ` with the event
  example `PASS` — the summary now states that, since the event PASS is
  precisely §4's zero-schema-cost mechanism.
- **RF-6** — T8 (`plat: test_contacted_report_replay_is_idempotent`,
  F9's conversion) was never named; added to the impact/conformance
  list alongside T1–T7/T9/T10 and pointed to from F9's disposition row.

**Status unchanged: Draft.** The registry row already records the
2026-11-03 maintainer directive; the ARB charter voids the `0bbc1437`
verdict for the changed surface, so the next step is a fresh dossier
run on this revision — not acceptance.

## Deferred questions

- **Slot refund on no-show / dispute / expiry.** When a delivered slot
  dies (`REJECTED`, `EXPIRED`, `DISPUTED → REFUNDED`, or the §9
  agent-timeout auto-`EXPIRED` re-offer path), does its slot return to
  the pool? Current draft: the budget is spent at **award** (one slot
  per distinct buyer awarded — §1 Units), and refund is
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
  interop fails silently if pairs normalize differently (measured,
  dossier F7 row 26). **Revised 2026-11-03:** the *phone* preimage is
  no longer deferred — §3 norms it to the verbatim wire bytes,
  matching the only implemented behavior (`messages.py:89-93`); what
  remains deferred is the email half, and the §3 rule escalates it
  from "nice to pin" to a **gate**: no email-form
  `contacted_hashes` entry ships until §9 pins the canonical and
  #153 **T9** vectors publish (the T9 proposal in §3 carries a
  concrete candidate rule + reference digests so the pin has a
  starting point).
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
- **Multi-slot agency delivery (`slots_claimed > 1`).** v1.1 caps the
  claim at exactly 1 (§2) because the `(lead, offer)` guard has no
  second-copy path to one buyer and no wire surface reports the
  awarded count (dossier F4). Trigger: an adopter with a concrete
  agency workflow needing N > 1 copies per buyer — the implementation
  must then add the awarded-count signal (ack or post), the
  `(lead, buyer, ordinal)`-keyed delivery path, and #153 **T5**
  (`plat: test_slots_claimed_multi_award_within_budget`) before the
  schema maximum can be lifted.

**Revisit trigger:** first v1.1 shared-lead production deployment, or
adopter evidence on refund economics and per-market defaults.

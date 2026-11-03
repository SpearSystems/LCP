# LEP-0007 — Outbound call-center outreach: the registered `{org}.outbound.call_request` extension

- **Title:** Outbound call-center outreach: registered `{org}.outbound.call_request` event-value extension
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-03
- **Status:** **Deferred** — deferred 2026-11-03 (trigger-unmet); number and record permanent
- **Target version:** v1.1.0 (additive MINOR; **zero schema change** — the entire surface rides open enumerations and open objects already governed by SPEC.md §6)
- **Discussion:** [#186](https://github.com/SpearSystems/LCP/issues/186)
  (`[LEP] Outbound call-center outreach: registered {org}.outbound.call_request extension`)
- **Roadmap trigger:** `docs/V1.1-ROADMAP.md`, Phase 6 — "Outbound
  call-center outreach" (low priority; finish-read of the section,
  including its reference pattern, design constraints, and the
  "distinct declined/ignored outcome" clause)
## Decision (maintainer, 2026-11-03)

**Deferred (trigger-unmet).** Recorded by the maintainer (randybeno) after
the independent ARB dossier on issue #186. The record's own trigger — buyers
or platforms needing outbound-call requests as a first-class operation — has
no adopter evidence in the repository or tracker, and the deliverable is a
proposed-on-acceptance extension row, so deferral strands nothing (no
implementation exists, nothing is advertised, no schema is committed).
The design work is preserved: the extension route
(`{org}.outbound.call_request` + `ignored` as an open `rejection_reason`
value), the measured rollout asymmetry that rules out a new message type at
v1.1, and the D1–D6 deferred questions with their triggers.

Revival requires a new or revised record citing concrete adopter evidence
and linking back to this number; the number and this record are permanent.

## Problem statement and evidence

**Trigger (roadmap, lines 314-328):** buyers or platforms need to
request an outbound call to a consumer — call-center follow-up, warm
transfer — as a first-class operation rather than a manual dial.

**Reference pattern (as read in full):** a `POST` endpoint requesting an
outgoing call to a number, keyed by zip/need/phone, with a distinct
`ignored` response status for requests the network declines. The
roadmap's LCP-side constraints: the `call` message already models the
call itself and `provenance.source_type` includes `outbound`; add an
explicit outbound-call request surface (message type or extension) with
a declined/ignored outcome distinct from `rejected`; "Additive; no core
change."

**What LCP already ships (the record engages this head-on before
proposing anything):**

- The `call` message is a first-class type (SPEC.md §4;
  `schemas/call.json`) with `direction: inbound|outbound`
  (`schemas/call.json:80-84`), durations, recording, IVR, disposition,
  and `transferred_from{original_call_id, transfer_reason}` — the
  *result* of an outbound call is already fully modelable, including
  transfers.
- `provenance.source_type` includes `outbound` and
  `acquisition_method` includes `warm_transfer` (SPEC.md §3,
  lines 184-186; open enums) — provenance for outbound-originated
  contacts exists.
- Call lifecycle events `CALL_OFFERED` / `CALL_CONNECTED` /
  `CALL_ENDED` / `CALL_OUTCOME` are documented known values of the open
  `event` enum (SPEC.md §4) and are already in the reference platform's
  **buyer-reportable** set (`router.py:82-96`), with buyer-offer
  `payable_rules` classifying outcomes and `concurrent_call_cap`
  advertised under §8 capacity.
- `POST /events` (`api/lcp-openapi.yaml:175-180`, `submitEvent`) is the
  authenticated, non-PII inbound event channel; the event `lead_id`
  required key (SPEC.md §4) points the platform at the stored lead and
  its plaintext contact — no phone number needs to cross the wire.

**What is missing is only the request and its distinct decline
outcome.** Today a buyer wanting the platform to dial holds the lead
(from `post`), dials out-of-band, and reports the call after the fact
via `call`/`CALL_*`. There is no protocol object for "please place this
call", and the ack model's fault-bearing statuses cannot express the
network's `ignored` (no-fault, terminal, no billing effect) cleanly.

**Gate honesty — why this is NOT a new message type.** LEP.md decision
rule: "Before adding a new universal message type, require evidence from
at least two independent adopters or a documented interoperability
necessity, **plus an extension feasibility analysis**." Evidence today
is one reference network's endpoint (external pattern) and zero LCP
adopters asking — the roadmap lists this item as **low** priority, the
weakest of the Phase 6 findings. This record performs the extension
feasibility analysis the rule demands (the wire shape below), and the
analysis shows the registered-extension route covers every element of
the reference pattern — which itself defeats the necessity claim. A new
message type would additionally be **strictly worse rollout math**:
`message.type` is closed (SPEC.md §6:675) so every v1.0 receiver
rejects it with `LCP-006` (measured below), while an unknown **namespaced
event value** is stored and passed through by construction
(`schemas/event.json:16-19` — no enum; §6 open-enum table). The
deliverable of this LEP is therefore the **registered extension**,
with the message type kept as a documented future path under concrete
triggers (Deferred questions D1).

## Scope

- **Core protocol:** no schema change, no new message type, no new
  envelope shape. One registered **namespaced event value**
  `{org}.outbound.call_request` riding the open `event` enum, a
  recommended `details` shape inside the open `event.details` object,
  one new documented known value on the open `rejection_reason`
  enumeration (`ignored`), and normative guidance text (SPEC.md §4
  event + ack; §8 advertisement note).
- **Registered extension:** the event-value namespace per
  `governance/EXTENSION-REGISTRY.md` format rules;
  `lcp.outbound.call_request` proposed-on-acceptance as the reference
  registration (adopters register their own `{org}` rows under the open
  registration process).
- **Reference implementation (gated on acceptance):** extension-value
  policy in the event gate, advertisement, per-sender rate limiting,
  compliance pre-action checks, request store and the `ignored` ack —
  all enumerated in the impact section. Not authorized by this Draft.
- **Not in scope:** dialer/queue implementation, telephony integration,
  per-market TCPA/DNC rule text (compliance is expressed only as the
  generic stored-lead gates that already exist in core §3/§9), request
  **pricing** semantics (bid/offer pricing untouched), the LEP-0006
  surfaces (independent decision — this route needs neither
  `FINAL_PRICING` nor `lead`/`post` `extensions`), and a normative
  MCP tool (noted gap).

## Proposed wire shape

### 1. The request — namespaced event value `{org}.outbound.call_request`

A delivered buyer sends an `event` message to the platform whose
`event` value is its registered namespace. Nothing about the message
class changes; the value is data inside the already-open enum.

Recommended `details` shape:

| Field | Type | Notes |
|---|---|---|
| `requested_action` | string | Open enum, known values `dial` (place the call), `warm_transfer` (bridge buyer agent), `scheduled_callback`. Unknown values stored and passed through (§6). |
| `offer_id` | string | Optional — the buyer's offer/entry the request relates to. |
| `buyer_agent_id` | string | Optional — agent requesting or to be bridged (mirrors `call.agent`). |
| `window_from`, `window_to` | string (date-time) | Optional requested contact window; the platform MUST treat it as bounded by the stored lead's `contact_window` (SPEC.md §4 lead) — a request can narrow, never widen, publisher-advertised contactability. |
| `reason_code` | string | Optional open enum (`follow_up`, `no_answer_retry`, `crm_workflow`…) — machine-usable context. Free-text `note` fields are deliberately **not** offered: they are the PII-smuggling shape this surface must not carry. |

Keying discipline (the reference pattern's "keyed zip/need/phone"
converted to LCP): the schema-required `lead_id` is the **only**
consumer locator. The platform resolves the contact from the lead it
already holds; the request MUST NOT contain a phone number, email, or
address — not plaintext, not hash (a hash adds nothing when the
platform holds the lead and would tempt pair-secret misuse on a
non-pair channel). `event` is a non-PII message class (SPEC.md §4
table); the same ingest honesty as LEP-0003 §4 / LEP-0006 A-1 applies:
**the open `details` cannot enforce this in schema — it is a receiver
ingest MUST** (invalid example 7-1; named platform test in the impact
section).

Authorization and trust: the sender MUST be a buyer with a delivered
post for that `lead_id` (the reference platform's existing
`is_delivered_buyer` condition, `router.py:1216-1218`), `event:submit`
scope, HMAC-signed (§9). A request is an authenticated attestation of
buyer intent, scoped to one lead; the platform keeps no obligation to
comply beyond the compliance gates below — the ack protocol expresses
the verdict.

**Zero schema cost, nonzero platform cost (measured path).**
`_BUYER_EVENT_TYPES` (`router.py:82-96`) is a closed constant set and
the authorization at `router.py:1219-1226` rejects any name outside it
for a delivered buyer: a properly scoped buyer sending today's proposed
value gets `LCP-002` ("Sender is not authorized for this lead event"),
while `CALL_OUTCOME` on the same transport is accepted. The wildcard
`platform:admin` route (`router.py:1220`; `auth.py` treats `*` as
satisfying every scope check — the LEP-0003 O2 class) is not a
conformance path. Acceptance therefore obligates the implementation PR
to land an **extension-value policy** — accept namespaced event values
only when the value is registered for the sender's organization and
the platform advertises it (below) — plus the named tests in the impact
section (LEP-0003 F5/T6 pattern, verbatim).

### 2. Capability advertisement (§8)

A platform that accepts requests MUST list the registered value in
`capabilities().events` (today thirteen values,
`router.py:1716-1730`; adding a namespaced string is a list-content
change, no schema change — the OpenAPI `events` arrays
(api/lcp-openapi.yaml:327, :835) already carry arbitrary strings), and
senders gate on the §8 parsed-semver membership rule (LEP-0003 F6
verbatim) + this value listing. Rollout safety is the open enum's: a
v1.0-conformant receiver **stores and passes through** an unknown event
value (§6) instead of hard-failing, so a mis-gated request is inert
rather than destructive — the property the closed `message.type`
alternative lacks (7-invalid-2 measurement).

### 3. Outcomes — the ack, including the distinct `ignored`

The reference pattern's distinct `ignored` outcome, translated without
touching a closed enumeration:

| Outcome | Expression | Meaning |
|---|---|---|
| Queued/accepted | `ack.status: ACCEPTED` | Request valid; platform will act subject to its compliance gates. |
| Duplicate | `ack.status: DUPLICATE` (or `LCP-005` on id reuse) | Same message-id replay, existing mechanism (`router.py:1229-1252`). |
| Malformed/unauthorized | `ack.status: REJECTED` + `rejection_reason` ∈ existing known set (`compliance_fail`, `out_of_scope`, `duplicate`, `capacity_exceeded`, …) | Fault-bearing or retryable — publisher/buyer automation routes on it. |
| **Declined (no-fault)** | `ack.status: REJECTED` + **`rejection_reason: "ignored"`** (new documented known value on the OPEN `rejection_reason` enum, `schemas/ack.json:43` — no schema constraint change) | "Seen, will not act": consumer unreachable per stored state, market rule, platform choice. Terminal for this request id, **no fault, no billing effect** — the roadmap's "distinct from `rejected`" clause. |

Why `rejection_reason` and not a new `ack.status: IGNORED`: `ack.status`
is a **closed** schema enum (`schemas/ack.json:17-24`) — adding a value
there is a closed-object/enum change with exactly the compatibility
burden this record exists to avoid, and LEP.md prefers open-surface
growth. The distinction the reference network expressed at HTTP status
level survives intact at the reason layer (open enum by design,
SPEC.md §6, and this record's proposal mirrors the §4 precedent of
`already_customer`/`out_of_geography` — no-fault outcomes already live
there). Where a deployment wants a wire-visible async decline after an
initial `ACCEPTED` (dialer exhaustion, late compliance hit), the
platform sends a second ack for the same `original_message_id` with
`REJECTED` + `ignored` — `ack` is "response to any message" with no
sync-only rule in §4.

### 4. Lifecycle — unchanged, and deliberately so

If the platform dials, the call is modeled by the **existing** `call`
message (`direction: "outbound"`, `schemas/call.json:80-84`;
`provenance.source_type: "outbound"` recorded on the resulting lead
contact — SPEC.md §3:184-186) and the existing `CALL_*` event
lifecycle with `payable_rules` classification (SPEC.md §4/§8) — all of
which ship today. An `ignored` request produces no call and therefore
no payable event; no refund/dispute semantics are touched.

### Compliance pre-action gates (normative, existing fields only)

A conformant platform MUST NOT place a call unless the **stored lead's
own compliance state** permits voice contact at dial time:

- `compliance.scrubs[]` / `dnc_status` equivalent: DNC-flagged or
  blocked records MUST NOT be dialed (the offer-restriction vocabulary
  already distinguishes `reject_dnc_flagged` outcomes, SPEC.md §8).
- `consent_purposes` include voice/calls semantics per §3 (a lead
  consented for email only MUST NOT be dialed because a buyer asked).
- `contact_window` (SPEC.md §4 lead) bounds when dialing may occur;
  request windows intersect, never widen (details table above).
- `CONSENT_WITHDRAWN` suppression (SPEC.md §4) MUST suppress queued
  requests for that lead — the platform that accepted a request and
  then withdrew-consent event arrives MUST cancel rather than dial, and
  SHOULD ack the queued request `REJECTED` + `ignored` (or
  `compliance_fail` where the deployment's reason taxonomy says so).
- §9 rate limits apply to the request surface per sender.

No law-specific fields are introduced (§12 universal audit item 2):
these are the generic core compliance blocks doing their existing job;
market rules (TCPA quiet hours, DNC registry lookups) are deployment
policy layered on the same gates.

### Valid example — namespaced call_request event

*(Validated by execution 2026-11-03 against the **published** tree
(`SchemaValidator`, `jsonschema 4.26.0`, base SHAs `5d6000d` and
`ace8861` — results identical, no schema/vertical drift between them):
`PASS`. No overlay exists for this proposal at all — the PASS on the
unmodified published tree is the zero-schema claim. Fixture lineage:
`buyer_001` holds the delivered `post` for `lead_abc123` in the
repository's own `examples/post.json`.)*

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "c40e8400-0000-4000-8000-000000000701",
      "type": "event",
      "timestamp": "2026-11-03T10:00:00Z",
      "sender_id": "buyer_001",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "buyer-001-event-lead_abc123-calldial-1",
      "test": true
    },
    "payload": {
      "lead_id": "lead_abc123",
      "event": "acme.outbound.call_request",
      "timestamp": "2026-11-03T09:59:41Z",
      "details": {
        "requested_action": "warm_transfer",
        "offer_id": "offer_mortgage_nsw",
        "window_from": "2026-11-03T17:00:00Z",
        "window_to": "2026-11-03T19:00:00Z",
        "buyer_agent_id": "agt_77"
      },
      "external_reference": "crm-ticket-456"
    }
  }
}
```

### Valid example — ack carrying the distinct ignored outcome

*(Validated by execution 2026-11-03 against the published tree: `PASS`
— `rejection_reason` is an open string enumeration with no schema
constraint to change (`schemas/ack.json:43`). The no-fault decline for
the request above.)*

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "c50e8400-0000-4000-8000-000000000703",
      "type": "ack",
      "timestamp": "2026-11-03T10:00:02Z",
      "sender_id": "platform_001",
      "receiver_id": "buyer_001",
      "correlation_id": "c40e8400-0000-4000-8000-000000000701",
      "idempotency_key": "platform-001-ack-701",
      "test": true
    },
    "payload": {
      "original_message_id": "c40e8400-0000-4000-8000-000000000701",
      "status": "REJECTED",
      "errors": [],
      "lead_id": "lead_abc123",
      "rejection_reason": "ignored"
    }
  }
}
```

### Invalid example 7-1 — phone number in the request

```json
"details": {
  "requested_action": "dial",
  "phone": "+61412345678"
}
```

Violates the keying discipline and the non-PII event class (SPEC.md §4
table). **Honest status of "rejects":** measured `PASS` against the
published tree — `details` is `additionalProperties: true`
(`schemas/event.json:25-28`) and no schema change reaches it. The
rule is therefore a receiver **ingest MUST** (reject the request,
`REJECTED` + `compliance_fail` or `LCP-100`-style validation error per
§5) with a named implementation test; the same stated-boundary
discipline LEP-0003 §4 and LEP-0006 A-1 record. The plaintext also
would not be needed: the platform holds `lead_abc123`'s contact — the
phone field is either redundant or stale, and stale contacts are how
dialers reach the wrong person.

### Invalid example 7-2 — the new-message-type alternative

```json
"message": { "type": "outbound_call_request", ... }
```

Measured `REJ` against the published tree: `'outbound_call_request' is
not one of ['lead', 'call', 'ping', 'post', 'ack', 'event', 'bid']` —
the concrete expression of SPEC.md §6:675 (closed `message.type`;
unknown → `LCP-006`). Every v1.0 receiver hard-fails this construction;
a namespaced event value degrades to store-and-pass-through instead.
This asymmetry, plus the evidence state above, is why the message type
is not the deliverable.

### Invalid example 7-3 — unregistered or unadvertised namespace

A sender whose org namespace is not registered (or a platform that does
not list the value in `capabilities().events`) submitting example 1's
request: a conformant implementing platform MUST refuse
(`REJECTED` + `out_of_scope`), and the sender MUST NOT have sent it per
the §8 gate. This row has no measurement because there is nothing to
schema-test — it is the advertisement gate itself; the implementation
obligation and test are named below.

## Privacy, security, and universal-core audit

- **No new PII on the wire.** The request rides the non-PII event
  class; keying is `lead_id`-only (7-1's ban); nothing in `details`
  identifies the consumer beyond what the platform already stores.
- **The action is the risk, and it is pre-gated.** Outbound dialing is
  the regulated act; the record's stance is that the protocol must not
  create a dial path around the stored compliance state — hence the
  Compliance pre-action gates (DNC/consent/window/withdrawal) using
  only existing core fields, and the platform holding the dialing
  decision (a request is intent, never authorization to contact).
  Decision on this record requires the named human security/privacy
  reviewer or the ARB-Security co-sign per LEP.md (it changes a
  contactability boundary in practice even though it moves no PII).
- **Abuse surfaces and their bounds:** request flooding (§9 rate
  limits + per-lead active-request cap left to deployment); requests
  for leads the sender does not hold (delivered-buyer gate,
  `router.py:1216-1218`); namespace squatting (registry
  collision/format rules already police — and an unregistered namespace
  is inert by the advertisement gate); reason-code enum gaming (open
  enum pass-through, no executable semantics anywhere — values are
  data, never predicates, unlike the `lcp.platform.requirements`
  matching surface which needed its own allowlist machinery).
- **Retention:** request/ack rows are lifecycle audit state — inherit
  §9 PII-retention and erasure behavior; they store no contact data
  (the request never had any), so post-`ERASURE_REQUEST` rows remain
  contact-free by construction (the LEP-0005 history-surface property).
- **Universal audit (SPEC.md §12) on the delta:** zero core fields,
  zero vertical changes, zero envelope changes — the checklist is
  engaged only by the new documented known values on two open enums
  (`event` value space via registration, `rejection_reason: ignored`),
  which §6's open/closed policy explicitly prices as MINOR prose, not
  core growth; the simplicity budget (AGENTS.md rule 2) sees no new
  field, block, or message — the entire proposal is **guidance + one
  registered namespace**, which is LEP.md's preferred shape
  ("Prefer a namespaced extension or reference-platform behavior over a
  new core field").

## Compatibility, versioning, and deprecation impact

- **Additive MINOR to v1.1.0** (SPEC.md §6): documented known values
  on open enumerations + recommended `details` guidance. **No schema
  file constraint changes**; the implementation PR MAY mirror `ignored`
  into the `schemas/ack.json:43` description list (a prose list edit,
  no validation change) — recorded as an implementation-phase choice,
  not a wire difference.
- **v1.0 receivers:** a namespaced event value is stored and passed
  through (§6 open-enum row; `schemas/event.json:16-19` `$comment`) —
  unknown-value handling was already a v1.0 MUST, so an unimplemented
  peer degrades inertly rather than failing `LCP-006` (contrast 7-2).
  `rejection_reason` unknown values are equally pass-through by §6.
  The sender-side §8 gate (advertisement + parsed semver) is still
  required so requests are not wasted on a peer that cannot act;
  violation wastes bandwidth, not trust (nothing is re-priced,
  refunded, or dialed by an inert pass-through).
- **Deprecation:** none introduced.
- **Rollback:** stop sending (sender-side opt-in); platform feature-flags
  the extension-value policy and removes the value from
  `capabilities().events`; queued requests MAY be acked `REJECTED` +
  `ignored` during wind-down. No data migration either direction.

## SDK, OpenAPI, MCP, conformance, and operational impact

Change-gated at implementation (Draft authorizes none):

- **Schemas:** no constraint change (measured zero-diff surface). SDK
  generated models are unchanged — `event.details` and the ack string
  fields are free/open in every generated SDK today.
- **OpenAPI:** `/events` + `submitEvent` (api/lcp-openapi.yaml:175-180)
  unchanged (details already an open object; the capabilities `events`
  arrays (:327, :835) carry arbitrary strings — advertisement is
  list-content). The ack `rejection_reason` description MAY mirror the
  new known value.
- **MCP:** no event-submission tool exists
  (`implementations/mcp-server/lcp_mcp_server/server.py:80-160` —
  seven tools, none for events), so v1's request surface is REST-only;
  a future `submit_event` MCP tool would carry this and every other
  event value. Gap recorded, not a blocker for this proposal (LEP-0006
  Track A records the identical REST-only status).
- **Reference platform obligations (named):** (1) extension-value
  policy in `submit_event` — accept `{org}.outbound.call_request` only
  when registered for an authenticated counterpart and advertised,
  replacing today's flat closed-set refusal at `router.py:1219-1226`;
  (2) add the advertised value to `capabilities().events`
  (`router.py:1716-1730`); (3) `ignored` handling in the ack path;
  (4) compliance pre-action gates + queued-request cancellation on
  `CONSENT_WITHDRAWN` (the lifecycle hooks at `router.py:1256+` show
  where stored state already drives transitions); (5) rate limiting per
  §9. Tests:
  `plat: test_delivered_buyer_namespaced_call_request_accepted_and_advertised`,
  `plat: test_call_request_with_phone_in_details_refused`,
  `plat: test_call_request_ignored_is_rejection_reason_not_status`,
  `plat: test_consent_withdrawn_cancels_queued_call_request`,
  `plat: test_unadvertised_namespace_refused` (7-3).
- **Conformance vectors:** no *schema* vectors are expressible by
  design (that is the zero-schema claim restated); the battery above is
  the Draft evidence and the platform tests named above are the
  implementation-phase conversion (LEP-0003 O3 discipline: real tests,
  not fake vectors).
- **Docs:** SPEC.md §4 (request pattern under `event`; `ignored` added
  to the ack rejection-reason known list), §6 (no change), §8
  (advertisement note); `docs/V1.1-ROADMAP.md` status line at
  acceptance; registry row activation on acceptance.

## Alternatives considered

- **New universal message type** (`outbound_call_request`, or a REST
  endpoint with its own envelope): the tempting shape, and it fails the
  LEP.md decision rule as analyzed above (adopter evidence absent;
  extension feasibility analysis succeeds; §6 rollout asymmetry
  measured in 7-2). Kept as the documented future under trigger D1 —
  if the request surface grows synchronous negotiation semantics
  (capacity, queue position, priced callbacks) that an async
  event/ack pair serves badly, a message type earns its closed-enum
  cost **then**, with adopter evidence attached.
- **Offer-side `extensions` registration** (`{org}.outbound.call_request`
  as an `offer.extensions` predicate — the alternative the roadmap's
  "message type or extension" clause permits on the offer side):
  rejected on ownership semantics. Offers are **pre-award,
  buyer-declared matching criteria** evaluated by the platform against
  leads (`schemas/offer.json:248-252` values; `offer_extensions.py:8-9`
  constants; `matching.py:116-120` consumption) — a static predicate
  about what a buyer accepts, not a directed request against one
  delivered lead. A lead-keyed imperative operation sent by a **buyer**
  post-delivery belongs to the event channel, which is exactly
  per-instance, authenticated-sender, lead-scoped, and ack-responseable.
- **`bid`-style synchronous request** (a new short-lived request/
  response message): rejected — bids price auctions; a dial request
  prices nothing, and ack-on-event already gives the synchronous
  verdict the reference pattern's HTTP status provided.
- **Status quo: manual dial out-of-band + after-the-fact `call`
  reporting:** explicitly kept as legal (no adoption requirement is
  created), but rejected as the destination: the roadmap names the gap
  ("first-class operation rather than a manual dial") and the two
  missing capabilities (request visibility for the platform's routing/
  compliance layer; the distinct no-fault decline) are what buyers
  actually asked for in the reference network comparison.
- **Deployment-specific vendor vocabulary with no registration:**
  available today and insufficient: without the registry row + §8
  advertisement there is no discovery, and without this record's
  guidance there is no outcome or compliance discipline — the
  registered extension is what makes a private practice interoperable,
  which is the extension mechanism's stated purpose in SPEC.md §6.

## Rollout, migration, observability, and rollback

1. **Record + registry row first** (this Draft → Review): the guidance
   is the wire shape; nothing may be emitted until a platform
   implements and advertises.
2. **Platform implementation PR (gated on acceptance):** the five named
   obligations + five named tests; advertisement list update; rate
   limits.
3. **Adopter onboarding:** orgs register `{org}.outbound.call_request`
   rows (open registration, format-checked) and their platforms
   advertise; publishers migrating from the legacy network's
   `request-outbound-call` endpoint map: zip/need → `lead_id`
   (the LCP post already resolved them), phone → **removed** (never
   crosses), declined → `REJECTED` + `ignored`.
4. **Observability:** counters `lcp_call_requests_total` (labels:
   action, disposition ∈ {queued, accepted, ignored, rejected_fault});
   queued-request age histogram (dialer SLI); suppression-cancel
   counter (gate 4's `CONSENT_WITHDRAWN` hits); ack-lag for async
   declines. All bounded-metadata; no contact values in any of them
   (the request never held them).
5. **Rollback:** flag the extension-value policy off; un-advertise;
   queued requests ack `REJECTED` + `ignored`. Every byte already sent
   is an open-enum value a v1.0 peer was required to pass through —
   rollback cannot produce a validation failure at any peer.

## Deferred questions

- **D1 — New universal message type.** Conditions that would earn the
  closed-enum cost: (a) **two or more independent adopters** implement
  the registered extension and each report at least one of the
  following that the event/ack route measurably fails: synchronous
  capacity/queue negotiation at request time (position, ETA, priced
  callback windows), request multiplexing across leads, or a
  request-scoped consent attestation envelope the event class cannot
  carry; or (b) a normative REST/MCP request surface becomes a product
  requirement of the reference platform (an `request_outbound_call`
  MCP tool is the likely first signal — today there is no event tool at
  all, see impact). **Trigger:** adopter evidence per LEP.md, not
  preference.
- **D2 — Capacity semantics for dialing.** §8 `capacity` advertises
  `concurrent_call_cap` for call **buyers**; a platform operating an
  outbound dialer on behalf of buyers has queue/capacity state a
  request would want (queue position, next-slot) that this LEP's async
  ack does not negotiate. **Trigger:** first production dialer
  implementation reporting queue thrash, or D1(a) evidence.
- **D3 — `ignored` taxonomy refinement.** One known no-fault value may
  be too coarse (unreachable vs. market-rule vs. buyer-reputation
  decline have different retry economics for sender automation).
  Open enum allows sibling values later at MINOR cost; this record
  deliberately ships one to keep the Draft falsifiable. **Trigger:**
  adopter automation that cannot route on a single `ignored`.
- **D4 — Reverse direction: publisher-side callback requests** (agent
  leads where the *publisher* asks a buyer to call back). The request
  shape would mirror, the delivered-buyer authorization would not
  (the requester is not a lead recipient). **Trigger:** an
  agent-vertical adopter.
- **D5 — MCP parity.** `submitEvent` is REST-only (impact section);
  whether the request surface should lead an MCP event tool or follow
  one. **Trigger:** MCP binding work (roadmap already lists MCP/REST
  contract items; LEP-0006 Track A shares this gap).
- **D6 — Consent-snapshot evidence per dial.** Regulators in some
  markets may want a per-outbound-dial attestation of the consent
  state read at dial time. The gates here log stored-state checks but
  define no evidence object. **Trigger:** a named market's rule change;
  would ride `call` metadata or a registered extension, not a core
  field.

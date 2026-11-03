# LEP-0001 — Bounded batch submission and event subscriptions

- **Title:** Bounded batch submission and event subscriptions
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-08-18
- **Status:** **Accepted** — accepted 2026-11-04 (maintainer, `docs/DECISIONS-v1.2.md` D1); both tracks
- **Target version:** v1.2.0 (MINOR; see Compatibility and versioning)
- **Last revised:** 2026-11-04 (2026-11-03 Deferral superseded by maintainer
  waiver D1 — accepted, not re-evidenced; F1–F5 dispositioned and
  implementation authorized, see "Decision")
- **Discussion:** [#164](https://github.com/SpearSystems/LCP/issues/164)
  (`[LEP] Bounded batch submission and event subscriptions`)

## Decision (maintainer, 2026-11-04)

**Accepted — both tracks.** Recorded by the maintainer (randybeno) under the
per-decision sign-off model. Basis: `docs/DECISIONS-v1.2.md` **D1** (trigger
waiver, 2026-11-04) and the work items **W2** (bounded batch submission) and
**W3** (event subscriptions) it funds in `docs/V1.2-EXECUTION-PLAN.md`. This
decision supersedes the 2026-11-03 Deferral of this same record; the
deferral's numbers and its dossier link remain permanent history, and its
disposition is discharged by the F1–F5 resolutions and the compensating
constraints below.

**Superseded by waiver, not by evidence.** The 2026-11-03 finding stands
unchanged. `LEP.md` requires evidence from at least two independent adopters
or a documented interoperability necessity before a new universal message
type is added; this record never produced either — the repo shows one
non-independent adopter, and the roadmap's own volume trigger is arithmetically
unmet by its published baselines. D1 waives that trigger for the items the
execution plan enumerates; it does not refute the arithmetic, and this record
does not claim adopter evidence it does not have.

**Two tracks, accepted together.** Batch submission and event subscriptions
are the two independently decidable tracks described in this record (see
`governance/LEP-REGISTRY.md`). Both are accepted here because both are funded
by the same waiver; neither is a precondition for the other, and either may be
withdrawn later without reopening the other's acceptance criteria.

### Post-draft collisions F1–F5, resolved

Each collision recorded on 2026-11-03 is resolved explicitly below. Where the
resolution is a design change, the change is stated.

| # | Collision (as recorded 2026-11-03) | Resolution in this acceptance |
|---|---|---|
| **F1** | Two-adopter/trigger arithmetic unmet by published baselines | **Waived by D1, not refuted.** No adopter evidence is asserted. The deterministic evidence that stands in for it is the W2-05 batch-vs-sequential throughput benchmark (100 items, recorded in `docs/OPERATIONS.md`) plus the batch and subscription conformance vectors. Re-evaluation 2027-05-04. |
| **F2** | Event-vocabulary mismatch with `capabilities().events` and the shipped LEP-0003 `CONTACTED` surface | **Design change.** Subscription `event_types` are the receiving platform's **advertised** event names — the closed set `capabilities().events` publishes (`DELIVERED`, `ACCEPTED`, `REJECTED`, `DISPUTED`, `REFUNDED`, `EXPIRED`, `CONVERTED`, `ARCHIVED`, `CALL_OFFERED`, `CALL_CONNECTED`, `CALL_ENDED`, `CALL_OUTCOME`, `CONTACTED`, `FINAL_PRICING`) plus registered extension event values (LEP-0006). The draft's dotted lowercase names (`lead.accepted`, `lead.posted`, `offer.selected`) are withdrawn; the record carries no second vocabulary. `CONTACTED` is subscribable, and its buyer-side reporting scope (LEP-0003 §4: delivered, scoped buyer) decides who may register for it. A type absent from the advertisement is refused at registration (`422`), never silently ignored. |
| **F3** | Replay/signing interplay with the now canonical LEP-0002 timestamp | **Clarified, no new mechanism.** Subscription delivery is signed with the existing per-pair HMAC canonical input (SPEC §9, `<timestamp>\n<idempotency-key>\n<raw-request-body>`) using the per-subscription secret, and the timestamp is the whole-second UTC wire form emitted verbatim and verified byte-for-byte. Because the replay window (default five minutes) is judged on the transmitted header, a delivery attempt cannot reuse an earlier timestamp: every attempt — including a cursor replay — is signed over the bytes actually sent, with its own `X-LCP-Timestamp` and its own idempotency key, while `event_id` and the event body stay byte-identical. Dedupe is therefore by `event_id`, never by signature; replay reproduces the same event **set**, not the same signature bytes. |
| **F4** | PII claims unenforceable while `event.details` stays open | **Resolved by enforcement that now exists.** The SPEC §4 class rule is enforced at ingest for every event name and both reporting directions by `_validate_event_details_pii` (`implementations/reference-platform/lcp_platform/router.py:1417`, invoked at `router.py:1637`), using the shared walk in `pii.py` that also backs the LEP-0006 B.3.4 extension-value rule (issue #198): a plaintext contact value anywhere in the open `details` object is a validation rejection, not a documentation promise. Subscription delivery reuses that same `event` payload and validator, so no consumer value reaches a subscriber and this record adds no PII field. |
| **F5** | Ack-contract shadowing in the batch response | **Design clarification.** The batch response is a closed per-item **receipt** vocabulary (`accepted`, `duplicate`, `rejected`, `error`) carried inside the HTTP response (partial success → `207`). It is not `ack.status`, it does not extend or reuse the `ack` enum, and it never acknowledges a `post` delivery — the `ack` message remains the only acknowledgement surface, with `PROCESSED` added there alone (D2). `duplicate` reuses existing `LCP-005` semantics at item granularity, so batch introduces no new error code. The caps this record flagged as draft are fixed at acceptance at **100 items / 1 MiB body** (execution plan W2-01), narrowed from the draft's 500 / 5 MB. |

Three consequences worth stating plainly, because they are the points a reader
could otherwise read as a silent rewrite:

- The batch track is a **loop over the existing single-item path**, not a
  second ingestion engine (W2-02). Per-item isolation, parity of validation,
  and per-item idempotency are the reason the wire type exists; the intake
  semantics are unchanged.
- The subscription track delivers the **existing** event vocabulary. It adds
  registration, signed at-least-once push, cursor replay, and dead-lettering
  around events the platform already emits and already advertises; it does not
  mint new event names, and `capabilities().events` stays the single source of
  truth for what is subscribable.
- The subscription **REST route** is the execution plan's
  `POST`/`GET`/`DELETE /v1/lcp/events/subscribe` with `GET /v1/lcp/events?cursor=`
  for replay (W3-01, W3-03), replacing the earlier
  `/v1/lcp/subscriptions/{id}` sketch. The `subscription` message type and its
  payload are unchanged by that move; only the HTTP surface moves.

### Compensating constraints (D1)

Every surface built under this acceptance MUST be:

1. **Additive-only on the wire and capability-gated.** The two message types and
   their payloads are additions to the closed `message.type` and no existing
   field, type, or error code changes. Both tracks are advertised through
   `capabilities()` — including the `capabilities().features` map (W0-03) that
   names the version each surface arrived in — so a peer that does not read the
   advertisement never receives these surfaces unsolicited, and a 1.0/1.1 peer
   still rejects them with the structured unknown-message-type error.
2. **Off by default in the reference platform.** Both tracks ship behind
   `LCP_FEATURE_*` flags — one per track, off by default — while remaining
   exercised by CI, the vectors, and the conformance suite. Presence in the
   code is not a claim that a deployment has it enabled.
3. **Backed by stand-in evidence.** The deterministic evidence that would have
   stood in for the missing adopter signal — the W2-05 batch throughput
   benchmark and the batch/subscription vectors — accompanies the
   implementation. Benchmarks here are directional, not capacity guarantees.
4. **Dated for re-evaluation — 2027-05-04.** The registry row and
   `docs/DECISIONS-v1.2.md` carry the same date.

### Independent review and challenge

- The 2026-11-03 ARB dossier
  (<https://github.com/SpearSystems/LCP/issues/164#issuecomment-6592471520>)
  remains the independent-review record for this proposal. It is not re-run,
  nothing in it is withdrawn, and its F1 finding is answered by a waiver rather
  than by refutation.
- The waiver is a **maintainer decision**, recorded as a decision: a reader can
  see that no adopter evidence was produced. A reader may challenge it at the
  2027-05-04 re-evaluation; a dossier that survives the `ARB.md` charter gates
  and contradicts this acceptance returns the record to Review.
- Implementation proceeds under the `LEP.md` traceability gate — schemas,
  examples, vectors, SDK bundles and SHA-256 manifests, OpenAPI/MCP contracts,
  changelog. Any wire-shape deviation discovered during implementation returns
  this record to Review before merge.

## Problem statement and evidence

Independent publishers need bulk intake (backfills, migrations, high-volume
campaigns) and lower per-lead request overhead, and buyers need event delivery
that does not depend on polling or per-offer webhook configuration.

Measured baselines from the reference platform (synthetic data, single
process; see `docs/V1.1-ROADMAP.md` Phase 3/4):

- SQLite intake and routing saturate around **50–54 records/sec**
  single-process; the full active-offer list is scanned per lead.
- A publisher exceeding a **few hundred leads per minute** is already above
  the single-process intake profile, so bulk intake is the first scaling lever.
- Buyer webhook latency dominates delivery throughput; delivery must be
  parallelized once per-webhook latency exceeds a few milliseconds, and
  polling/per-offer webhooks cannot meet latency, volume, or
  integration-management requirements for buyers at that point.

These numbers are directional, not capacity guarantees, but they establish
that the pain point is real at the measured baselines and is cross-language and
cross-organization — not a single deployment's quirk.

## Scope

- **Core protocol:** two new message types (`batch`, `subscription`) with
  payload schemas dispatched through the existing `message.type` + `payload`
  envelope shape — requires the MINOR version bump and the universal-core audit
  (§12 checklist).
- **Reference implementation:** batch intake + subscription registry,
  delivery, replay, retry/dead-letter in the reference platform.
- **REST / OpenAPI and MCP:** `submit_leads_batch` and `subscribe_to_events`
  (authorized by the 2026-11-04 acceptance; tracked as W2/W3 in
  `docs/V1.2-EXECUTION-PLAN.md`).
- **SDKs:** typed models, schema bundles, and helpers in all 10 SDKs.
- **Conformance:** new vectors for batch and subscription behavior.
- **Not in scope:** multi-currency auctions, richer predicate language,
  streaming uploads, and polygon geocoding (see Alternatives considered).

## Proposed wire shape

### Batch submission

A new `batch` message type uses the same top-level `lcp.message` and
`lcp.payload` shape as existing messages. Each item payload reuses the current
lead schema and authentication boundary; the batch dispatch and per-item
metadata are the new v1.2.0 surface.

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "d9a20000-0000-4000-8000-000000000001",
      "type": "batch",
      "timestamp": "2026-08-18T09:00:00Z",
      "sender_id": "publisher_001",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "publisher-001-batch-001",
      "test": true
    },
    "payload": {
      "request_id": "batch-20260818-001",
      "items": [
        {
          "item_id": "item-001",
          "idempotency_key": "publisher-001/batch-001/lead-1",
          "payload": {
            "lead_id": "lead-1",
            "status": "NEW",
            "channel": "form",
            "consumer": { "first_name": "Ada", "last_name": "Lovelace" },
            "location": { "country_code": "AU", "state_region": "NSW" },
            "attributes": { "vertical": "mortgage", "schema_version": "1.0.0" }
          }
        },
        {
          "item_id": "item-002",
          "idempotency_key": "publisher-001/batch-001/lead-2",
          "payload": {
            "lead_id": "lead-2",
            "status": "NEW",
            "channel": "form",
            "consumer": { "first_name": "Grace", "last_name": "Hopper" },
            "location": { "country_code": "AU", "state_region": "NSW" },
            "attributes": { "vertical": "mortgage", "schema_version": "1.0.0" }
          }
        }
      ]
    }
  }
}
```

**Response — per-item result, partial success is the contract:**

```json
{
  "request_id": "batch-20260818-001",
  "summary": { "total": 2, "accepted": 1, "duplicate": 1, "rejected": 0, "error": 0 },
  "results": [
    { "item_id": "item-001", "status": "accepted", "lead_id": "lead-1" },
    { "item_id": "item-002", "status": "duplicate", "lead_id": "lead-1" }
  ]
}
```

**Constraints (from the roadmap design constraints):**

- **No cross-item transaction.** Each item is accepted or rejected
  independently; a failure in one item never rolls back others.
- **Per-item idempotency.** Each item carries its own `idempotency_key`;
  replays return the original result (`duplicate`, reusing the existing
  `DUPLICATE`/`LCP-005` semantics at item granularity).
- **Per-item acknowledgement (receipt).** Every item gets an explicit status
  in the HTTP response, so a client can retry **only failed items**. This is a
  receipt, not an `ack` message — the two vocabularies stay separate (see the
  F5 resolution).
- **Limits (fixed at acceptance):** maximum 100 items and 1 MiB body
  per request; exceed either → whole-request `413`, not per-item rejection.
- **Validation:** every item is validated by the same `lead.json` schema,
  authentication, and lifecycle intake rules as a single lead. Items are
  full-PII intake payloads, never ping payloads.

**Invalid example — one bad item must not fail the batch:**

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "b2000000-0000-4000-8000-000000000002",
      "type": "batch",
      "timestamp": "2026-08-18T09:00:01Z",
      "sender_id": "publisher_001",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "publisher-001-batch-002",
      "test": true
    },
    "payload": {
      "request_id": "batch-20260818-002",
      "items": [
        {
          "item_id": "item-001",
          "idempotency_key": "publisher-001/batch-002/lead-1",
          "payload": {
            "lead_id": "lead-1",
            "status": "NEW",
            "channel": "form",
            "consumer": {},
            "location": { "country_code": "AU", "state_region": "NSW" },
            "attributes": { "vertical": "mortgage", "schema_version": "1.0.0" }
          }
        },
        {
          "item_id": "item-002",
          "idempotency_key": "publisher-001/batch-002/lead-2",
          "payload": {
            "lead_id": "lead-2",
            "status": "NEW",
            "channel": "form",
            "consumer": { "first_name": "Lin", "last_name": "Zhao" },
            "location": { "country_code": "AU", "state_region": "NSW" },
            "attributes": { "vertical": "mortgage", "schema_version": "1.0.0" }
          }
        }
      ]
    }
  }
}
```

Result: `item-001` → `rejected` (`required` field error, per-item), `item-002` →
`accepted`. No cross-item rollback.

### Event subscriptions

A new `subscription` message type uses the existing envelope shape to
register an authenticated, tenant-scoped event subscription:

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "a1000000-0000-4000-8000-000000000001",
      "type": "subscription",
      "timestamp": "2026-08-18T09:00:00Z",
      "sender_id": "buyer_002",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "buyer-002-subscription-001",
      "test": true
    },
    "payload": {
      "event_types": ["DELIVERED", "ACCEPTED", "CONTACTED"],
      "callback_url": "https://buyer.example/hooks/lcp",
      "filter": { "offer_id": "offer-17" }
    }
  }
}
```

**Delivery contract (roadmap design constraints):**

- **Authenticated registration** and **tenant authorization** — a subscriber
  can only register for and receive events about its own leads/offers.
- **Explicit event allowlist** — `event_types` values are the receiver's
  advertised event names (`capabilities().events`) plus registered extension
  event values (LEP-0006); no second vocabulary exists. Unknown or
  unadvertised types are rejected (`422`), not silently ignored.
- **Privacy-safe filters** — filters are a closed key set drawn from core
  enums and identifiers (event type, status, vertical, lead/offer ids);
  **no consumer fields become subscription filters** — `consumer.*`,
  `provenance.*`, and `attributes.*` paths are refused — and no raw PII is
  delivered in events (hashes only, mirroring ping discipline). The open
  `details` object is class-checked at ingest (see the F4 resolution).
- **Signed at-least-once delivery** — existing LCP signing: the SPEC §9
  per-pair canonical input, whole-second timestamp emitted verbatim, signed
  per delivery attempt (see the F3 resolution — a replay re-signs the bytes it
  actually sends). Delivery is retried with backoff and moves to a
  dead-letter queue after the retry budget is exhausted.
- **Stable event IDs and replay/cursor support** — each delivery carries a
  stable `event_id` and a monotonic cursor so subscribers can dedupe and
  replay from a cursor. Duplicates are legal (at-least-once); delivery order
  is not part of the contract, and a replay reproduces the same event set.

**Invalid example:**

```json
{
  "lcp": {
    "version": "1.2.0",
    "message": {
      "id": "a2000000-0000-4000-8000-000000000002",
      "type": "subscription",
      "timestamp": "2026-08-18T09:00:00Z",
      "sender_id": "buyer_002",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "buyer-002-subscription-002",
      "test": true
    },
    "payload": {
      "event_types": ["CONSUMER_EMAIL_CHANGED"],
      "callback_url": "https://buyer.example/hooks/lcp"
    }
  }
}
```

Result: rejected — the event type is not advertised in `capabilities().events`
(and an event carrying a consumer email would violate the §4 class rule
anyway).

## Privacy, security, and universal-core audit

- **PII boundaries unchanged.** Batch items are full-PII lead-intake payloads
  validated by the existing lead schema and authentication rules; nothing new
  crosses into pings. Subscription events carry hashes and allowlisted metadata
  only.
- **No predicate-driven PII leakage.** Subscription filters are restricted to
  allowlisted event metadata; arbitrary consumer-field filters are rejected by
  schema (closed `filter` shape).
- **AuthN/AuthZ:** batch intake authenticates the publisher exactly like
  single lead intake; subscription registration requires authentication and is
  tenant-scoped, with authorization checked on registration and on every
  delivery.
- **Universal core audit:** the two new message types and their payloads add
  **zero vertical-specific or market-specific fields** to the core. Vertical
  data continues to live in `attributes`. Run the §12 checklist before merge.
- **Abuse cases considered:** oversized batches (limit + 413), subscription
  flooding (per-subscriber rate limits, dead-letter caps), filter enumeration
  (allowlist only), and replay amplification (cursor TTL + retention bounds).

## Compatibility, versioning, and deprecation impact

- **MINOR bump to v1.2.0** — new message types and additive payload schemas
  are backward compatible; unknown-message-type receivers already return the
  structured error required by SPEC.md, so a v1.0 receiver rejects `batch` /
  `subscription` cleanly instead of misreading them.
- Additive-only: no existing field, message type, or error code changes.
- N+2 deprecation window applies to any later change; nothing here is
  deprecated.
- The closed `message.type` gains `batch` and `subscription`, each with a
  payload schema; the existing `lcp.message` + `lcp.payload` envelope shape is
  retained and unknown message types continue to receive the structured error.
  Unknown optional fields continue to be ignored by v1 receivers (unchanged
  rule).

## SDK, OpenAPI, MCP, conformance, and operational impact

- **OpenAPI/REST:** `POST /v1/lcp/leads/batch`; subscription
  registration `POST` / `GET` / `DELETE` on `/v1/lcp/events/subscribe`; and
  `GET /v1/lcp/events?cursor=` for replay (W3-01, W3-03 — the route shape this
  record's earlier `/v1/lcp/subscriptions/{id}` sketch is replaced by).
- **MCP:** `submit_leads_batch` and `subscribe_to_events` tools added
  together with the REST surface (per roadmap constraint).
- **SDKs:** typed models + schema bundles + SHA-256 manifest re-synced in all
  10 SDKs (`tools/check_sdk_schema_sync.py`), plus helpers that surface
  per-item results and cursor replay.
- **Conformance:** new vectors — batch happy path, partial success, per-item
  duplicate/idempotency, size/count limits, subscription registration,
  allowlist rejection, cursor replay, dead-letter after retry budget.
- **Reference platform:** batch intake path, subscription registry,
  parallelized delivery, retry/dead-letter, cursor storage.
- **Operational:** backpressure on intake, dead-letter observability,
  delivery lag metrics, retention-bounded event and cursor storage (ties into
  the Phase 4 retention work item).

## Alternatives considered

- **Deployment-specific extension instead of core.** Rejected: the roadmap
  evidence shows bulk intake and event delivery are shared cross-organization
  needs; keeping them private means every publisher re-implements idempotent
  batch and every buyer re-implements polling. The namespaced-extension path
  remains the fallback if the core review rejects the wire shape.
- **Per-publisher bulk endpoints.** Rejected: duplicates authentication,
  validation, and vector coverage for no protocol gain.
- **Streaming/chunked upload.** Deferred: not needed at current volumes;
  revisit if backfill sizes exceed the 100-item / 1 MiB limits in practice.
- **Keep polling + per-offer webhooks.** Rejected as the *only* option: the
  latency, volume, and integration-management gap is what this record
  addresses, and subscriptions are additive — they do not remove webhooks. No
  adopter measurement is asserted here; the trigger is waived by D1, not
  evidenced.

## Rollout, migration, observability, and rollback

1. Release the additive schema + vectors first; old receivers reject the new
   message types with the structured error (verified by vector).
2. Implement batch + subscription behind their reference-platform feature
   flags (off by default); roll out REST and MCP surfaces together.
3. Ship SDK helpers and bundles in the same release; no migration required
   for existing single-item publishers (unchanged paths).
4. **Observability:** per-item batch latency and outcome distribution, batch
   size/limit utilization, delivery lag, retry and dead-letter rates, cursor
   replay depth.
5. **Rollback:** stop advertising the new message types / disable the flag —
   additive-only means no data-format migration and no impact on v1.0
   receivers.

## Deferred questions (trigger to revisit)

Status of each question at the 2026-11-04 acceptance. Each is marked
**Resolved** (answered here, by machinery already shipped, or by the named
work item that must answer it before the surface ships) or **Waived**
(explicitly dropped, with the reason). None is deleted.

- **Exact limits** — **Resolved 2026-11-04.** Fixed at 100 items / 1 MiB body
  per request (execution plan W2-01), narrowed from this record's draft
  500 items / 5 MB. The W2-05 benchmark (batch vs sequential at 100 items,
  recorded in `docs/OPERATIONS.md`) is the calibration evidence; raising either
  cap needs that evidence and a revised record.
- **Event ordering** — **Resolved 2026-11-04 by narrowing the promise, not by
  picking an ordering.** Delivery is at-least-once and order is not part of the
  contract: subscribers dedupe by `event_id`, and a cursor replay reproduces
  the same event set (W3-02, W3-03). A per-subscription FIFO guarantee is not
  claimed and MUST NOT be relied on; a subscriber that needs ordering is a new
  record.
- **Event retention** — **Resolved 2026-11-04 (numeric TTLs land in W4-06).**
  Cursor and event-stream TTLs come from the retention work under invariant I8
  — retention is defined before a surface stores new data — and an expired
  cursor returns an explicit error (W3-03). The surface does not ship enabled
  until those bounds exist, so no unbounded event or cursor storage is
  authorized by this acceptance.
- **Error taxonomy** — **Resolved 2026-11-04.** Batch introduces no new error
  code: per-item outcomes reuse existing codes (`duplicate` → `LCP-005` at item
  granularity) and whole-request failures use the existing limit and validation
  errors. The F5 resolution records why the per-item receipt vocabulary is
  closed and distinct from `ack.status`.
- **Cross-tenant delivery** — **Resolved 2026-11-04.** Authorization is checked
  at registration and again on every delivery against the subscriber's tenant;
  fan-out is per subscription, not a shared broadcast, and no wildcard scope
  exists by default. Tenant-isolation tests are blocking (invariant I6).

**Re-evaluation:** 2027-05-04 (D1). Earlier trigger: adopter or subscriber
evidence that the limits, ordering, retention, or delivery assumptions do not
hold, or an `ARB.md` dossier that survives charter gates and contradicts this
acceptance — either returns this record to Review.

## Implementation authorization

Accepted 2026-11-04 (maintainer). This section lists the work the acceptance
authorizes and nothing more; it authorizes implementation against this record,
not a release (`LEP.md`, "Traceability and implementation gate"). Work-item ids
refer to `docs/V1.2-EXECUTION-PLAN.md`.

**Batch submission (W2).**

- REST `POST /v1/lcp/leads/batch` — `items[]` (max 100 / 1 MiB), per-item
  `lead_id` and `idempotency_key`, per-item receipt, `207` on partial success,
  no cross-item transaction; the platform path is a loop over the single-item
  intake path, not a second ingestion engine (W2-01, W2-02).
- MCP `submit_leads_batch` — same contract, per-item receipts returned in input
  order (W2-03).
- SDK helpers in **all 10 SDKs** — `submit_leads_batch` with per-item result
  types; a retry covers failed items only, never the whole batch (W2-04).
- Throughput evidence — batch vs sequential at 100 items, recorded in
  `docs/OPERATIONS.md`; directional, not a capacity guarantee (W2-05).

**Event subscriptions (W3).**

- Registration `POST /v1/lcp/events/subscribe`, with `GET`/`DELETE` on the same
  resource — tenant-scoped, explicit `event_types` allowlist drawn from
  `capabilities().events`, per-subscription secret never returned, deactivation
  supported, no wildcard by default (W3-01).
- Signed at-least-once delivery — `X-LCP-Signature`/`X-LCP-Timestamp` over the
  SPEC §9 canonical input with the per-subscription secret, stable `event_id`,
  retry with backoff, dead-letter after the retry budget; duplicates are legal
  and documented (W3-02 and the F3 resolution).
- Replay and cursor — `?cursor=` on `GET /v1/lcp/events` and a `replay_from`
  subscription parameter; a replay returns the identical event set, an expired
  cursor is an explicit error (W3-03).
- Privacy-safe filters — closed key set of core enums and identifiers, consumer
  paths refused (W3-04).
- MCP `subscribe_to_events` and SDK helpers in all 10 SDKs — registration plus
  poll-fallback parity (W3-05).
- Ops CLI — `lcp-platform subscriptions list/show/disable` and dead-letter
  inspection, with documentation citations (W3-06).

**Vectors (both tracks).**

- Batch: all-valid, partial success, per-item duplicate/idempotency replay
  returning the original receipts, oversize → `413`, per-item validation error,
  and sibling persistence after an induced item failure.
- Subscriptions: registration, unadvertised event type refused (`422`), filter
  on a consumer path refused, signature verification, clock skew, duplicate
  delivery, cursor replay, and dead-letter after the retry budget.

**Not authorized here.** Multi-currency auctions, a richer predicate language,
streaming uploads, polygon geocoding, new event names, and any change to
`ack.status` beyond D2. Both tracks ship behind `LCP_FEATURE_*` flags that
default off; enabling them in a deployment is an operator decision, not a
protocol claim. Registry status, release notes, and verification evidence remain
the maintainer's records — this section does not make them.

# LEP-0001 — Bounded batch submission and event subscriptions

- **Title:** Bounded batch submission and event subscriptions
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-08-18
- **Status:** **Draft** — not accepted, not implemented
- **Target version:** v1.1.0 (MINOR; see Compatibility and versioning)

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
that the pain point is real before v1.1 and is cross-language and
cross-organization — not a single deployment's quirk.

## Scope

- **Core protocol:** two new message types (`batch`, `subscription`) and their
  additive envelope blocks — requires the MINOR version bump and the universal
  core audit (§12 checklist).
- **Reference implementation:** batch intake + subscription registry,
  delivery, replay, retry/dead-letter in the reference platform.
- **REST / OpenAPI and MCP:** `submit_leads_batch` and `subscribe_to_events`
  (currently deferred in `docs/V1.1-ROADMAP.md`).
- **SDKs:** typed models, schema bundles, and helpers in all 10 SDKs.
- **Conformance:** new vectors for batch and subscription behavior.
- **Not in scope:** multi-currency auctions, richer predicate language,
  streaming uploads, and polygon geocoding (see Alternatives and deferral).

## Proposed wire shape

### Batch submission

A new `batch` message type in the existing envelope. The envelope is unchanged
for single-item flows; the `batch` block is additive.

```json
{
  "lcp": {
    "version": "1.1",
    "message": {
      "type": "batch",
      "message_id": "d9a2...",
      "timestamp": "2026-08-18T09:00:00Z"
    },
    "sender": { "id": "publisher_001" },
    "batch": {
      "request_id": "batch-20260818-001",
      "items": [
        {
          "item_id": "lead-1",
          "idempotency_key": "pub-001/batch-001/lead-1",
          "lead": { "consumer": { "first_name": "Ada" }, "attributes": {} }
        },
        {
          "item_id": "lead-2",
          "idempotency_key": "pub-001/batch-001/lead-2",
          "lead": { "consumer": { "first_name": "Grace" }, "attributes": {} }
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
    { "item_id": "lead-1", "status": "accepted", "lead_id": "L-9001" },
    { "item_id": "lead-2", "status": "duplicate", "lead_id": "L-9001" }
  ]
}
```

**Constraints (from the roadmap design constraints):**

- **No cross-item transaction.** Each item is accepted or rejected
  independently; a failure in one item never rolls back others.
- **Per-item idempotency.** Each item carries its own `idempotency_key`;
  replays return the original result (`duplicate`, reusing the existing
  `DUPLICATE`/`LCP-005` semantics at item granularity).
- **Per-item acknowledgement.** Every item gets an explicit status, so a
  client can retry **only failed items**.
- **Limits (draft, flagged for review):** maximum 500 items and 5 MB body
  per request; exceed either → whole-request `413`, not per-item rejection.
- **Validation:** every item is validated by the same schema and
  `ping_safe` rules as a single post. Items are full-PII lead payloads
  (post flow), never ping payloads.

**Invalid example — one bad item must not fail the batch:**

```json
{
  "lcp": {
    "version": "1.1",
    "message": { "type": "batch", "message_id": "b2", "timestamp": "2026-08-18T09:00:01Z" },
    "sender": { "id": "publisher_001" },
    "batch": {
      "request_id": "batch-20260818-002",
      "items": [
        { "item_id": "lead-1", "idempotency_key": "k1", "lead": { "consumer": {} } },
        { "item_id": "lead-2", "idempotency_key": "k2", "lead": { "consumer": { "first_name": "Lin" } } }
      ]
    }
  }
}
```

Result: `lead-1` → `rejected` (`required` field error, per-item), `lead-2` →
`accepted`. No cross-item rollback.

### Event subscriptions

A new `subscription` message type registers an authenticated, tenant-scoped
event subscription:

```json
{
  "lcp": {
    "version": "1.1",
    "message": { "type": "subscription", "message_id": "s1", "timestamp": "2026-08-18T09:00:00Z" },
    "sender": { "id": "buyer_002" },
    "subscription": {
      "event_types": ["lead.accepted", "lead.posted", "offer.selected"],
      "callback_url": "https://buyer.example/hooks/lcp",
      "filter": { "offer_id": "offer-17" }
    }
  }
}
```

**Delivery contract (roadmap design constraints):**

- **Authenticated registration** and **tenant authorization** — a subscriber
  can only register for and receive events about its own leads/offers.
- **Explicit event allowlist** — `event_types` is a closed enum of
  non-PII events; unknown types are rejected (`422`), not silently ignored.
- **Privacy-safe filters** — filters may reference only allowlisted event
  metadata (offer id, status, vertical); **no consumer fields become
  subscription filters** and no raw PII is delivered in events (hashes only,
  mirroring ping discipline).
- **Signed at-least-once delivery** — existing LCP signing; delivery is
  retried with backoff and moves to a dead-letter queue after the retry
  budget is exhausted.
- **Stable event IDs and replay/cursor support** — each delivery carries a
  stable `event_id` and a monotonic cursor so subscribers can dedupe and
  replay from a cursor.

**Invalid example:**

```json
{
  "lcp": {
    "version": "1.1",
    "message": { "type": "subscription", "message_id": "s2", "timestamp": "2026-08-18T09:00:00Z" },
    "sender": { "id": "buyer_002" },
    "subscription": {
      "event_types": ["lead.consumer_email_changed"],
      "callback_url": "https://buyer.example/hooks/lcp"
    }
  }
}
```

Result: rejected — the event type is not on the allowlist (and the example
event would violate PII discipline anyway).

## Privacy, security, and universal-core audit

- **PII boundaries unchanged.** Batch items are full-PII post-flow payloads
  validated by the existing post schemas and the recursive `ping_safe` rules;
  nothing new crosses into pings. Subscription events carry hashes and
  allowlisted metadata only.
- **No predicate-driven PII leakage.** Subscription filters are restricted to
  allowlisted event metadata; arbitrary consumer-field filters are rejected by
  schema (closed `filter` shape).
- **AuthN/AuthZ:** batch intake authenticates the publisher exactly like
  single posts; subscription registration requires authentication and is
  tenant-scoped, with authorization checked on registration and on every
  delivery.
- **Universal core audit:** the two new message types and their blocks add
  **zero vertical-specific or market-specific fields** to the core. Vertical
  data continues to live in `attributes`. Run the §12 checklist before merge.
- **Abuse cases considered:** oversized batches (limit + 413), subscription
  flooding (per-subscriber rate limits, dead-letter caps), filter enumeration
  (allowlist only), and replay amplification (cursor TTL + retention bounds).

## Compatibility, versioning, and deprecation impact

- **MINOR bump to v1.1.0** — new message types and additive envelope blocks
  are backward compatible; unknown-message-type receivers already return the
  structured error required by SPEC.md, so a v1.0 receiver rejects `batch` /
  `subscription` cleanly instead of misreading them.
- Additive-only: no existing field, message type, or error code changes.
- N+2 deprecation window applies to any later change; nothing here is
  deprecated.
- Envelope schema gains the optional `batch` and `subscription` blocks;
  unknown optional fields continue to be ignored by v1 receivers (unchanged
  rule).

## SDK, OpenAPI, MCP, conformance, and operational impact

- **OpenAPI/REST:** `POST /v1/lcp/leads/batch`, `POST /v1/lcp/subscriptions`,
  `GET /v1/lcp/subscriptions/{id}` (status/cursor), replay endpoint.
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
- **Streaming/chunked upload.** Deferred: not needed at v1.1 volumes; revisit
  if backfill sizes exceed the 5 MB / 500-item limits in practice.
- **Keep polling + per-offer webhooks.** Rejected as the *only* option: the
  roadmap trigger (latency/volume/integration-management) is already met by
  adopter requirements; subscriptions are additive and do not remove webhooks.

## Rollout, migration, observability, and rollback

1. Release the additive schema + vectors first; old receivers reject the new
   message types with the structured error (verified by vector).
2. Implement batch + subscription behind a reference-platform feature flag;
   roll out REST and MCP surfaces together.
3. Ship SDK helpers and bundles in the same release; no migration required
   for existing single-item publishers (unchanged paths).
4. **Observability:** per-item batch latency and outcome distribution, batch
   size/limit utilization, delivery lag, retry and dead-letter rates, cursor
   replay depth.
5. **Rollback:** stop advertising the new message types / disable the flag —
   additive-only means no data-format migration and no impact on v1.0
   receivers.

## Deferred questions (trigger to revisit)

- **Exact limits:** 500 items / 5 MB are drafts; calibrate from the batch
  benchmark (roadmap item 5: benchmark batch against single-item baselines).
- **Event ordering:** per-subscription FIFO vs. per-entity ordering — decide
  with first production subscriber; document whichever is chosen.
- **Event retention:** cursor and event-stream TTL, aligned with the Phase 4
  retention work; revisit when storage is unbounded.
- **Error taxonomy:** whether batch introduces new error codes or reuses
  existing ones per item (draft: reuse at item granularity).
- **Cross-tenant delivery:** delivery authorization at scale (fan-out to
  multiple buyers) — revisit with the first multi-buyer auction workload.

**Revisit trigger:** first v1.1 production deployment or adopter evidence that
limits/ordering/retention assumptions do not hold.

# LCP capability map

> **Reader page · how the pieces talk.** Normative behaviour lives in
> [`spec/`](../spec/); this page only draws the shapes the prose describes.
> Each diagram names the capability gate that must be advertised before the
> surface exists for a peer (`spec/05-capabilities.md` §8).

LCP is one wire contract with several distinct topologies on top of it. The
system map that introduces them lives in the [README](../README.md); the six
diagrams below run from intake to what happens to the data afterwards. They are
deliberately small: if a flow needed more than a dozen nodes, it is two flows.

---

## 1. Intake — what happens to one lead

```mermaid
sequenceDiagram
    autonumber
    participant Pub as Publisher
    participant Plt as Platform
    participant Q as History queue
    participant St as Lead store

    Pub->>Plt: POST /v1/lcp/leads (envelope + idempotency key)
    Plt->>Plt: signature, schema, PII class checks
    alt provenance.agent.attestation present
        Plt->>Plt: verify issuer JWS (ES256/EdDSA, pinned fallback)
    end
    Plt->>St: dedup by idempotency key + lead_id (LCP-005 on mismatch)
    Plt->>St: persist lead (ciphertext at rest)
    Plt->>Q: enqueue history + match audit (bounded, never blocks)
    Plt-->>Pub: ack ACCEPTED
    Note over Q: capacity 1024 · drain, drop-risk counter, then drop<br/>a lead is never lost by queue saturation
```

Gate: none. Batch uses the same path per item (`features.batch`, off by
default); see diagram 3 for routing.

## 2. Auction and shared slots

```mermaid
sequenceDiagram
    autonumber
    participant Plt as Platform
    participant BA as Buyer A
    participant BB as Buyer B

    Plt->>BA: ping (phone_hash/email_hash, slots_available=2)
    Plt->>BB: ping (same ping_id, same slots)
    BA->>Plt: bid (price_cents, slots_claimed=1)
    BB->>Plt: bid (price_cents, slots_claimed=1)
    Plt->>Plt: rank in settlement currency (FX comparable, else excluded)
    Plt->>BA: post + contacted_hashes[]
    Plt->>BB: post + contacted_hashes[]
    BA-->>Plt: ack ACCEPTED → PROCESSED (CRM sync, terminal success)
    BA-->>Plt: event CONTACTED (hashes of parties already contacted)
    Note over Plt: slots_claimed never exceeds slots_available<br/>exclusive is the one-slot case
```

Gate: slots are part of the 1.1 baseline. Cross-currency ranking needs
`features.multi_currency` and a fresh, consistently-sourced rate
(`LCP_FX_MAX_AGE_SECONDS`, default 300); otherwise the offer is
non-comparable and excluded with a reason.

## 3. Agent-to-agent negotiation

```mermaid
sequenceDiagram
    autonumber
    participant Plt as Platform
    participant Agt as Buyer agent

    Plt->>Agt: quote (negotiation_id, price, floor, max_rounds=3, expires_at)
    alt counter within bounds and monotone
        Agt->>Plt: counter (price_cents)
        Plt-->>Agt: ack (rounds_remaining, binding_token on accept)
    else below floor
        Agt->>Plt: counter (price below floor)
        Plt-->>Agt: REFUSED below_floor_price (never clamped)
    else round cap reached
        Agt->>Plt: counter (round 4)
        Plt-->>Agt: REFUSED round_limit_exceeded
    else expired
        Agt->>Plt: counter after expires_at
        Plt-->>Agt: REFUSED negotiation_expired
    end
    Agt->>Plt: bid (negotiation_binding_token)
    Plt-->>Agt: REDEEMED (single use · replay returns the original outcome)
```

Gate: `features.negotiation` (`LCP_FEATURE_NEGOTIATION`, off by default).
A peer that does not advertise it gets `LCP-006` for `quote`/`counter`, not a
silent downgrade.

## 4. Event subscriptions

```mermaid
sequenceDiagram
    autonumber
    participant Sub as Subscriber
    participant Plt as Platform
    participant DLQ as Dead-letter queue

    Sub->>Plt: POST /v1/lcp/events/subscribe (event_types, filter, callback_url)
    Plt->>Plt: tenant check · allowlist · SSRF check · secret (returned once)
    Plt-->>Sub: 201 subscription_id
    Plt->>Sub: POST callback (X-LCP-Timestamp, X-LCP-Event-Id, signature)
    Note over Plt,Sub: HMAC over timestamp + event_id + exact body bytes<br/>at-least-once: duplicates share the same event_id
    alt non-2xx
        Plt->>Sub: retry 1s → 5s → 30s → 120s → 600s (max 5)
        Plt->>DLQ: FAILED (queue_type=event_subscription)
    end
    Sub->>Plt: GET /v1/lcp/events?cursor= (replay, TTL 86400 s)
```

Gate: `features.event_subscriptions` (`LCP_FEATURE_EVENTS`, off by default).
Filters are closed to core enums and identifiers — a consumer field is refused
by name.

## 5. Profiles, matching, and extension admission

```mermaid
flowchart TD
    O["Offer"] --> PR["Requirement profile ref<br/>{profile_id, version}"]
    PR --> RES{"resolve version"}
    RES -->|"pinned"| PRED
    RES -->|"unpinned"| ACT["active version"]
    ACT --> PRED["Predicates (allowlisted)<br/>eq · in · not_in · gte · lte · range · exists"]
    PRED --> SA["Service-area set<br/>(versioned, effective window)"]
    SA --> IDX["Candidate index"]
    IDX --> M["Full matcher"]
    M --> A["match_audits<br/>(per predicate, reason)"]
    M --> R["routed / excluded"]
    EXT["Extensions by surface"] -.-> O
    EXT -.-> M
```

Limits: ≤50 predicates, ≤100 values per set, typed paths only. An unknown
path or operator is refused when the profile is written, not at match time.
Offers keep the version they were created with, so activation and rollback
never rewrite history.

Extension admission is surface-scoped:

| Surface | Admits |
|---|---|
| `ping` | only registrations declaring ping safety (`lcp.platform.assessment`) |
| `lead` / `post` | `lcp.form.questions`, `lcp.intake.raw_form` (`restricted`), `lcp.platform.assessment` |
| `offer` | `lcp.platform.requirements`, `lcp.platform.service_area`, `lcp.buyer.criteria` |
| `event` | `lcp.marketplace.final_pricing`, `lcp.outbound.call_request` |

## 6. Data lifecycle — retention, archive, erasure

```mermaid
flowchart LR
    IN["Intake"] --> ENV["Encrypted envelope<br/>(90 d default)"]
    IN --> HQ["History queue"]
    HQ --> HIST["History + audit tables<br/>(365 d default)"]
    HQ --> MA["Match audits<br/>(90 d default)"]
    ENV --> SW["Retention sweep"]
    HIST --> SW
    MA --> SW
    SW -->|"before delete"| RED["Redact ciphertext /<br/>unreachable to readers"]
    RED --> PURGE["Delete"]
    ENV -->|"export --before"| ARC["Archive JSONL +<br/>SHA-256 manifest"]
    ARC --> LED["Archive ledger"]
    ERS["Erasure request"] --> RED
    ERS --> ARC
```

Retention defaults are configuration, not policy: `LCP_ENVELOPE_RETENTION_DAYS`
(90) and `LCP_HISTORY_RETENTION_DAYS` (365). Exports refuse rows in the
erasure set, and every archive digest is recorded so an erased subject can be
accounted for after the live row is gone.

---

## Reading order

| If you are… | Start at |
|---|---|
| a publisher | 1 (intake) → 6 (retention) |
| a buyer | 2 (auction) → 3 (negotiation) → 4 (events) |
| a platform operator | README map → 5 (matching) → 6 (lifecycle) |
| an agent integrator | README map → 3 → 1 |

**Previous:** [SPEC index](../SPEC.md) · **Next:** [Platform integration](PLATFORM-INTEGRATION.md)

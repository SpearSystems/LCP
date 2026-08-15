# LCP Implementation Decisions

> **Status:** Approved implementation profile for the LCP v1.0 draft
>
> This document records the decisions made for the reference SDK,
> platform/router, and sandbox. It is intentionally separate from the
> transport-neutral protocol specification so future maintainers can revise
> deployment behavior without silently changing the wire format.

## 1. Reference implementation scope

The reference platform is **production-oriented** and is intended to be a
usable foundation for a real deployment. It provides:

- Persistent lead, offer, bid, delivery, and lifecycle-event storage.
- HTTP intake for leads, calls, and bids.
- Schema validation and strict ping-safe enforcement.
- HMAC and Bearer authentication.
- Idempotency and replay protection.
- Deterministic offer matching.
- Ping/bid/post routing.
- At-least-once webhook delivery with retries.
- Capability, offer, lead-status, and schema discovery.
- A Docker/local sandbox using the same application and validation path as
  the live deployment.
- Durable routing jobs, worker leases, audit records, and a controlled lead
  erasure operation.
- AES-GCM application-level encryption for persisted envelopes outside test
  mode.

It remains an implementation rather than a hosted LCP service. Operators are
responsible for TLS termination, secret management, database backups,
monitoring, alerting, retention policy, access control, and deployment
hardening.

The platform is deliberately layered so a production operator can replace the
SQLite store, webhook worker, or secret provider without changing the LCP
message contract.

## 2. Offer model

Offers are buyer acceptance profiles. The standardized offer document lives
in [`schemas/offer.json`](../schemas/offer.json).

The v1.0 implementation standardizes common, explainable predicates:

- Vertical and schema version.
- Country, state/region, and postal-code scope.
- Channel scope.
- Source and acquisition-method exclusions.
- Incentive exclusions.
- Verified phone/email requirements.
- Spam-risk and data-completeness thresholds.
- Consent, DNC, litigator, and blacklist requirements.
- Delivery windows and capacity limits.
- Direct or auction routing mode.
- Floor price, currency, and payable definition.

Offer creation, updates, credentials, and buyer administration remain
platform-specific. `GET /v1/lcp/offers` is the interoperable discovery
surface; it is not an admin API.

Complex vertical-specific predicates belong in the namespaced `extensions`
object. The matcher must fail closed when it cannot evaluate a required
standard predicate and must never execute arbitrary code supplied in an
offer.

## 3. Matching behavior

Matching happens before a ping is sent. All configured restrictions are
ANDed together. An offer matches only when every configured predicate passes.

The matcher returns structured reasons, not only a boolean. This makes a
rejected lead explainable to platform operators without exposing PII to
buyers.

Standard behavior:

1. Inactive offers do not match.
2. The lead vertical must equal the offer vertical.
3. The lead country must be included in `countries`.
4. Optional state/region and postal-code lists are allowlists.
5. Optional channel lists are allowlists.
6. Source, acquisition, incentive, verification, quality, and compliance
   restrictions are evaluated from the lead payload.
7. Missing data fails a `require_*` predicate rather than being treated as a
   pass.
8. PII-bearing values are never copied into a ping merely to evaluate an
   offer. Only ping-safe fields may be included in the ping.
9. Capacity and delivery-window checks happen immediately before routing.

## 4. Auction selection

The reference router uses this deterministic policy:

1. A `direct` offer sends a full post at its floor price without a bid. An
   `auction` offer follows the bid policy below.
2. Only `accept` bids compete for an auction post.
3. A bid below the offer floor price is not eligible.
4. The highest eligible bid wins.
5. Ties are broken by the lowest `estimated_contact_seconds` when supplied.
6. Remaining ties are broken by bid timestamp, then buyer ID, so the result
   is deterministic across retries.
7. A ping expires at its configured `expires_at`. If there is no eligible bid,
   the lead is marked `EXPIRED` unless an operator adds a future fallback
   strategy.
8. `exclusive` leads produce at most one post. `shared` leads may be posted
   to up to `max_buyers` winners when the operator enables shared routing.

The router records the winning offer, bid, price, and match reasons for audit.
Pricing and settlement remain outside LCP's scope.

## 5. HTTP authentication and signing profile

The reference implementation supports Bearer API keys and HMAC. HMAC is the
recommended profile for bilateral publisher/platform and platform/buyer
traffic.

For an HTTP request, the canonical signing input is:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

The signature is the lowercase hexadecimal HMAC-SHA256 digest of that exact
UTF-8 byte sequence. Required HMAC headers are:

- `X-LCP-Timestamp` — UTC ISO-8601 timestamp.
- `X-LCP-Idempotency-Key` — must match the envelope message key.
- `X-LCP-Signature` — HMAC-SHA256 digest.

The default replay window is five minutes. Operators may choose a shorter
window. Secrets are bilateral and must be rotated outside the message body;
the reference credential store supports an active secret and an optional
previous secret during rotation.

Bearer requests use `Authorization: Bearer <token>` and still require the
idempotency header for mutating operations. All authenticated requests are
subject to sender authorization and rate limiting at the deployment edge.

## 6. Delivery semantics

Platform-to-buyer delivery is HTTP webhook delivery with **at-least-once
semantics**:

- A 2xx response acknowledges transport receipt.
- A `409` response is treated as an idempotent duplicate after the first
  successful delivery.
- Timeouts, connection errors, and 5xx responses are retried.
- The reference retry schedule is five attempts with delays of 1, 5, 30, 120,
  and 600 seconds.
- Every delivery uses a stable LCP message ID and idempotency key.
- Workers claim deliveries with a lease so multiple worker replicas do not
  process the same job concurrently; expired leases are reclaimable.
- Operators must retain delivery attempts and the last error for operations
  and dispute review.
- A failed final delivery is recorded as a failed delivery; it is not silently
  discarded or represented as a successful post.

The platform acknowledges the publisher intake request after durable storage.
Webhook delivery continues asynchronously so a slow buyer cannot block
publisher intake. Reusing an idempotency key with different message content
is rejected as a conflict rather than treated as a duplicate.

## 7. Sandbox parity

The sandbox uses the same platform application, schema loader, matcher,
authentication code, and delivery worker as a live deployment. It differs
only through configuration:

- Separate database.
- Test credentials and webhook endpoints.
- `X-LCP-Test: true` and envelope `test: true` for test traffic.
- Local or mock delivery targets.
- No production credentials or real consumer data.

The conformance vectors remain the protocol-level test suite. The sandbox
adds end-to-end tests for authentication, persistence, matching, routing,
retry behavior, and idempotency.

## 8. SDK boundary

The Python SDK is deliberately independent of the MCP binding and platform
storage. It provides message construction, validation, signing, verification,
idempotency, and HTTP operations. It does not contain offer-matching or
routing logic.

MCP remains a thin adapter and should use the same signing profile as the SDK
and reference platform.

## 9. Production hardening profile

The production deployment profile uses Postgres, external secret injection,
AES-GCM envelope encryption, scoped tenant credentials, resource
authorization, HTTPS-only webhooks, private-destination/egress checks, leased
routing and delivery workers, health probes, and a WSGI process manager.
SQLite and database-stored secrets remain local/reference options.

The reference platform does not claim a certification. Operators should map
their deployment to OWASP ASVS Level 2 and a NIST/ISO-style control framework,
then obtain independent security and privacy review for regulated operation.

## 10. Future changes

Future maintainers should revisit these decisions through a documented
proposal before changing interoperable behavior:

- Async SDK APIs and batch submission.
- Multi-currency auction rules.
- Shared-lead routing and fallback buyers.
- Webhook subscription management.
- Delivery receipts beyond transport acknowledgement.
- Deeper key-management service/HSM integration and mandatory mTLS profiles.
- Additional production database migration tooling and queue backends.
- Fully managed DSAR/consent-withdrawal propagation across downstream buyers.
- Hosted conformance sandbox and certification workflow.
- Formal namespaced buyer criteria in `extensions`.

Any change to the wire format or a required interoperable behavior must also
update the relevant schema, OpenAPI document, examples, test vectors, and
changelog.

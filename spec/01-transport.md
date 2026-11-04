# LCP — Lead Context Protocol Specification

> Part of the normative LCP v1.0 specification: **Transport and security**.
> TLS, authentication and HMAC signing, hash schemes, agent attestation, rate limits, PII handling, webhooks, and the agent binding layer.
> Index: [SPEC.md](../SPEC.md) · File map: [spec/README.md](README.md).

## 9. Security

### Transport

TLS 1.3 required for all transports.

### Authentication

Two auth methods:

1. **API keys** (Bearer token) — for simple integrations.
2. **HMAC** — `X-LCP-Signature`, `X-LCP-Timestamp`,
   `X-LCP-Idempotency-Key` headers (HTTP binding). For
   transport-agnostic deployments, the same values are mirrored in a
   `payload.message.security` block:

   ```json
   "security": {
     "signature": "...",
     "timestamp": "2026-08-15T10:20:00Z",
     "algorithm": "HMAC-SHA256"
   }
   ```

For HTTP HMAC authentication, the canonical signing input is the exact
UTF-8 byte sequence:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

The signature is the lowercase hexadecimal HMAC-SHA256 digest of that
input. `X-LCP-Idempotency-Key` MUST match
`lcp.message.idempotency_key` on mutating messages. Receivers MUST reject
requests outside the acceptable timestamp window before processing PII.
The default replay window is five minutes; deployments MAY choose a
shorter window. During key rotation, a receiver MAY accept a previously
active secret for a bounded migration period.

**Canonical timestamp generation (LEP-0002).** Signers MUST emit
`X-LCP-Timestamp` (and the mirrored `payload.message.security.timestamp`,
and SDK envelope `lcp.message.timestamp` defaults) in the whole-second
UTC wire form:

```text
YYYY-MM-DDTHH:MM:SSZ
```

— UTC, whole seconds, exactly one `Z`, no fractional part, no numeric
offset. Fractional (`2026-08-15T10:20:00.123456789Z`), numeric-offset
(`2026-08-15T10:20:00+00:00`), unmarked (`2026-08-15T10:20:00`), and
RFC-2822 (`Mon, 17 Aug 2026 10:20:00 GMT`) forms MUST NOT be generated
by a conforming signer.

The HMAC covers the **exact timestamp string transmitted in the header**,
byte-for-byte: signers MUST build the canonical input from the same string
they put on the wire, and verifiers MUST recompute the digest over the
received header value verbatim — never over a parsed-and-reformatted
timestamp. Receivers keep applying their existing (lenient) clock parsing
to decide the replay window; this canonicalization changes no verifier
behavior.

### Phone hash (dedup)

`phone_hash = HMAC-SHA256(shared_secret, normalized_e164)`

- The `shared_secret` is **per-pair** — the publisher hashes with a
  secret shared with each specific buyer. Each buyer receives a
  different hash for the same phone number.
- **Purpose:** dedup within a single buyer's stream. Not a
  cross-buyer dedup mechanism (different buyers see different hashes).
- **Privacy property:** an attacker who intercepts a ping cannot
  reverse the hash without the shared secret. Unlike unsalted SHA-256,
  a precomputed rainbow table over the phone-number keyspace is
  useless.
- The spec does not define key distribution — that is a deployment
  concern (bilateral agreement between publisher and buyer).

### Contact hashes (slot-holder dedup, v1.1, LEP-0003)

`contacted_hashes` entries (post §4, `CONTACTED` report details §4)
use the **same per-pair HMAC-SHA256 model as `phone_hash`** — keyed
with the secret shared with the message's receiver:

`contact_hash = HMAC-SHA256(pair_secret, contact_preimage)`

- **Preimage normalization (normative).** The only hash-input behavior
  this protocol implements is hashing the **wire value verbatim,
  byte-for-byte**: for phone entries the preimage is the
  `consumer.phone` string the platform delivered, unchanged — no
  folding, no stripping, no separator handling (the reference
  platform's `phone_hash` builder is the executed baseline, and
  `consumer.phone` is E.164 by core schema, so the verbatim value IS
  the E.164 form on conforming traffic). **The reference platform
  hashes phone only today.**
- **Email preimages (candidate canonical; gated).** The proposed
  canonical for `normalized_email` — and the rule the pinned vectors
  were computed under — is: trim surrounding whitespace, ASCII-lowercase
  the domain, keep the local-part bytes verbatim, and treat
  plus-addresses as distinct (no stripping). It is a **pin-in-progress,
  not yet shipped behavior**: no email-form `contacted_hashes` entry
  ships until this canonical is confirmed and the executed dedup-hash
  vectors (#153 **T9**, `lcp-sdk-dedup-hash-001..N`, run by all ten
  SDKs and the reference platform) publish. Divergent-but-plausible
  normalization produces *different digests silently*, so the vectors
  are the interop guarantee, not the prose.
- **Pinned reference vectors.** `test-vectors/sdk/contact-hash.json`
  pins preimage→digest pairs (pair secret `pair-secret-for-vectors-0123456789`):
  phone `+15125550147` and the email canonical example are byte-exact
  recomputable by any implementation; SDK builders and the platform
  MUST reproduce every row (lowercase-hex output, UTF-8 input bytes).
- **Re-keying requires the plaintext.** HMAC of a *received* digest
  under another pair's secret is not a re-key; a platform may re-key
  only from contact values it holds in plaintext (the leads it
  delivered), never from reported digests.
- **Never unsalted digests.** The unsalted SHA-1 contact hashes used
  by the reference commercial network are explicitly NOT adopted —
  phone and email keyspaces are enumerable, so unsalted digests invert
  by lookup (the same weakness §14 blocker B3 removed from
  `phone_hash`). SHA-1 anywhere in a contact-hash path is a
  conformance failure.

### Agent attestation

`provenance.agent.attestation` is a signed token proving the agent's identity
and authority as an acting principal. Transport authentication (the request
HMAC, above) proves *which sender* sent the bytes; the attestation proves
*which agent* acted, and on whose behalf. Free-text attestations are
non-conformant. The full decision record, rationale, and conformance battery
for this section are LEP-0008.

**Token form (normative).** A compact JWS (RFC 7519 §7.1):
`base64url(header) "." base64url(claims) "." base64url(signature)` — three
non-empty segments, UTF-8 JSON objects, standard base64url alphabet **without
padding**. `schemas/core.json` enforces the shape (`^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$`,
`maxLength` 8192); a padded or free-text value is rejected by schema
validation (`LCP-100`, `field: "provenance.agent.attestation"`) before any
verification work. Shape is not verification: the checks below are
implementation duties, not schema constraints.

**Header rules.**

| Member | Rule |
|---|---|
| `alg` | MUST be exactly `EdDSA` (OKP/Ed25519) or `ES256` (P-256). Any MAC (`HS256`/`HS384`/`HS512`), `none`, or anything else is rejected with reason `algorithm`. Exact, case-sensitive match, before any key lookup. |
| `kid` | MUST be present; selects the JWK. |
| `typ` | SHOULD be `JWT`; MUST NOT decide a verification outcome (a receiver must not reject a valid `at+jwt` token for its media type). |
| unknown members | MUST be ignored except `crit`: this profile defines no extensions, so any `crit` header names an unsupported extension and the token fails at the shape step. URL-bearing members (`jku`, `x5u`, `x5c`) MUST NOT be dereferenced — the issuer's JWKS (below) or a pinned key is the only key source. |

**Claim set (normative vocabulary).**

| Claim | Required | Type | Rule |
|---|---|---|---|
| `iss` | MUST | string | HTTPS issuer URL; base of the JWKS location. |
| `aud` | MUST | string | Equals `lcp.message.receiver_id`, and the receiver MUST confirm that value is its own platform identity. Stops cross-receiver replay. |
| `agent_id` | MUST | string | Byte-equal to `provenance.agent.agent_id`. |
| `acting_for` | MUST | string | Equal to the authenticated `sender_id`, or carried by that sender's `act_for:` scope. |
| `iat` | MUST | integer (NumericDate, RFC 7519 §2) | Whole seconds, base-10 literal — no fraction, no exponent notation. |
| `exp` | MUST | integer (NumericDate) | Same numeric form; expiry enforced as below. |
| `nbf`, `jti` | MAY | integer / string | `nbf` SHOULD be honored under the same skew rule; `jti` is recorded, not required and not single-use-cached in v1. |
| others | MAY | — | Ignored (claims are open inside a verified signature; unknown optional data is forward-compatible). |

Absent or wrong-typed MUST claims are a decode-time `format` failure — never
a post-signature claims mismatch, never ignorable.

**Verification procedure (normative order).** A receiver MUST, in order:
(1) require the attestation when an agent block is present; (2) parse both
segments as JSON objects, rejecting malformed JSON, non-objects, and
**duplicate member names at any nesting depth** (`format`); (3) exact `alg`
allowlist match before touching keys (`algorithm`); (4) check MUST-claim
presence and types on the claim bytes (`format`); (5) resolve the issuer's
JWKS — `https://<iss>/.well-known/jwks.json` — over TLS only, after the
request is transport-authenticated, through the same paired egress controls
used for delivery webhooks (HTTPS-only with no user info, host resolved and
rejected if private/loopback/link-local, no redirect following, no ambient
proxy); (6) a document failing to parse or containing duplicate `kid` values
is invalid — the token fails `key_unknown`, never “first key wins”;
(7) select the JWK by `kid` and check its `kty`/`crv`/`alg` agree with the
header `alg`; (8) check the signature encoding — EC signatures are P1363
fixed-width `r ‖ s` (64 bytes for ES256), DER MUST NOT be accepted; EdDSA is
64 bytes — then verify the signature over the exact `header.payload` bytes
(`signature`); (9) reject when `exp` is at or before now (equality rejects);
(10) honor `nbf` if present under the same rule; (11) require `aud` to equal
the envelope's `receiver_id` and the receiver's own identity; (12) require
`agent_id` to equal the envelope's, and `acting_for` to equal the
authenticated sender or be covered by its `act_for:` scope; (13) apply the
same time-skew rule as request replay protection (the §9 replay window;
default five minutes, receivers MUST NOT widen it for attestations).
Issuer unavailability (transport failure) is a server-side availability
failure (`LCP-500`, fail closed); an invalid but successfully fetched JWKS
is `key_unknown`. Verification failures are identity failures: `LCP-002`
with a machine-readable `details.reason` drawn from
`{format, algorithm, signature, key_unknown, expired, claims_mismatch}`.
None of these steps replaces the request HMAC: a verified attestation MUST
NOT relax the envelope signature check.

**Capability advertisement honesty.** `conformance_level` in the
capabilities document MUST describe what the receiver actually enforces: L3
is advertised only when attestation verification is live and enforced
(`observe` mode is not L3), otherwise L2. Shape validation alone is not L3.

### Rate limits

Per-sender rate limits are enforced. Exceeding the limit returns
`LCP-011 RATE_LIMITED` (429) with a `Retry-After` header (HTTP binding).

### PII

- Ping: stripped (strict allowlist, no PII).
- Post: TLS + per-contract retention policies.
- Attachments: authenticated out-of-band bytes, encrypted storage, hash
  verification, access control, retention, and jurisdiction-specific deletion.
- Hashes for dedup (HMAC-SHA256, per-pair).
- PII retention policies are deployment-defined.

### Webhook delivery

When a platform pushes `event` messages to a buyer's webhook (HTTP POST),
the webhook request MUST be signed with the same HMAC scheme used for
LCP messages: `X-LCP-Signature`, `X-LCP-Timestamp`,
`X-LCP-Idempotency-Key` headers. The buyer verifies the signature
before processing the event. This prevents forged events (e.g. a fake
`CONVERTED` to avoid payment, or a fake `REJECTED` to manipulate
routing).

Webhook delivery is a platform implementation concern — the protocol
defines the message format and signing, not the delivery mechanism.

### Agent-as-consumer timeout

When a buyer's agent evaluates a delivered lead (post), the agent MUST
respond within **30 seconds**. If no response is received, the lead
auto-transitions to `EXPIRED` and may be re-offered to the next buyer
in the routing order. The 30-second timeout is the default; endpoints
MAY advertise a different timeout in their capability response.

**What "respond" means (HTTP binding, normative).** The window runs from the
delivery of a `post` to the receiver's acknowledgement of that delivery. A post
that is not acknowledged within the window MUST transition the lead to
`EXPIRED`; a post that was acknowledged is not expired by this window, because
the acknowledgement is what establishes the delivery (and, where the offer's
payable rules say so, payability). Re-offer after expiry is **MAY**: when a
platform re-offers, it MUST respect the §3 slot budget, since the expired copy
already consumed an award. Endpoints MUST advertise the window they enforce in
the §8 capability response.

## 10. Agent binding

Agents are first-class senders via a binding layer, never coupled into
the core.

### Agent roles

1. **Agent as source** — a consumer's AI assistant submits a lead on
   their behalf. New channel; requires consent proof.
2. **Agent as transporter** — a publisher's AI submits leads for the
   publisher. Requires attestation ("acting for partner X").
3. **Agent as consumer** — a buyer's AI evaluates and accepts delivered
   leads. Requires deterministic response contracts (answer within the
   post timeout — 30s default, see §9 — or treated as timeout).

### Agent identity

`provenance.agent{agent_id, acting_for, attestation}` — first-class
concept. The MCP binding maps this to MCP identity; other bindings
(A2A, future) map it the same way.

### Binding rules (abstract)

- Capability discovery → tool list.
- JSON Schema → tool input schema.
- LCP auth → credential mapping.
- LCP ack → tool result.
- LCP event → tool notification.

### MCP binding (first concrete binding)

The official reference MCP server (see
[implementations/mcp-server/](../implementations/mcp-server/)) exposes:
`submit_lead`, `submit_call`, `submit_bid`, `query_lead_status`,
`get_schema`, `get_capabilities`, `list_offers`. It is a thin adapter —
the core never depends on MCP.

> **Future:** `submit_leads_batch` and `subscribe_to_events` (webhook/
> push delivery) are **not** part of v1.1. LEP-0001 is Deferred until its
> trigger is met — independent publishers need bulk intake, backfills, or
> materially lower request overhead, or polling at volume becomes costly —
> and revival requires a revised record with adopter evidence and
> vocabulary harmonisation with the shipped `capabilities().events`
> surface (see [LEP-REGISTRY.md](../governance/LEP-REGISTRY.md)). The current
> single-item, pull-based tools remain the supported agent surface.

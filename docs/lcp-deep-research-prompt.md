# LCP — Deep Research Prompt (cross-LLM review)

> Purpose: run this prompt through several frontier LLMs to stress-test
> the LCP v1.0 design before implementation. The goal is a bulletproof,
> universally applicable lead exchange protocol. Collect their critiques,
> resolve or explicitly defer each, then update `SPEC.md`.

---

You are a senior protocol architect reviewing a proposed open standard for
exchanging consumer lead data (PII) between publishers, platforms, and
buyers — the "HTTP of lead generation." Your job is to find every flaw,
gap, ambiguity, and future-proofing risk. Be adversarial but constructive.
Assume the protocol will be published as Apache 2.0 and must survive 20+
years, supporting every lead type that exists or will exist (form fills,
calls, chats, clicks, API submissions, AI-agent submissions), in every
market (AU, NZ, US, and any future market), for every vertical (mortgage,
insurance, solar, legal, home services, auto, health, and any future
vertical).

## Context: the existing system

The protocol formalizes an existing production wire contract
(`spx-pingpost-v1`) that already handles: HMAC-signed posts with
idempotency keys and nonce replay protection; a strict PII split (ping =
non-PII attributes + hashes only, with a forbidden-keys list; post = full
PII to the winning buyer); phone normalization to E.164 with sha256
phone-hash dedup (1-hour spam window + 30-day buyer exclusivity); and a
market model of `au_nz` (8 AU states + NZ) and `us` (50 states + DC).
The platform is a ping/post lead router: publishers submit leads, the
router pings eligible buyers with non-PII attributes, buyers bid, the
winner receives the full lead (post), and lifecycle events follow
(accepted/rejected/disputed/refunded/converted).

## The proposed design (LCP v1.0)

### Envelope

```json
{
  "lcp": {
    "version": "1.0.0",
    "message": {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "type": "lead",
      "timestamp": "2026-08-15T10:20:00Z",
      "sender_id": "pub_123",
      "receiver_id": "spx",
      "correlation_id": null,
      "idempotency_key": "pub-lead-20260815-001",
      "test": false
    },
    "payload": {}
  }
}
```

### Canonical core (universal — no vertical-specific or market-specific fields)

- `consumer`: first_name, last_name, email, phone (E.164), phone_hash
  (sha256 of normalized E.164), dob (sensitive), gender (sensitive).
  At least one of name/email/phone required.
- `location`: country_code (ISO 3166-1 alpha-2, REQUIRED), state_region
  (per-country validation registry), postal_code (per-country format),
  city, timezone (IANA). state_region OR postal_code required.
- `compliance`: consent_timestamp, consent_source_url, consent_text_version,
  jornaya_token, trustedform_token, otp_verified, session_capture_id
  (rrweb), call_recording_id, dnc_checked, tcpac_consent, extensible.
- `provenance` (non-PII): received_at, source_type
  (publisher|direct|agent|api|referral), source_id, source_url, ip_hash,
  user_agent_hash, funnel_key, flow_key, campaign_id, creative_id,
  landing_page_url.
- `attributes`: vertical-specific, JSON Schema per vertical. `vertical`
  + `schema_version` required.
- `status`: NEW, PINGED, POSTED, ACCEPTED, REJECTED, DUPLICATE, DISPUTED,
  REFUNDED, EXPIRED, CONVERTED, ARCHIVED.

### Message types

- `lead`: intake. consumer + location + compliance + provenance +
  attributes + external_id + channel (form|chat|click|api|agent|referral).
- `call`: telephony. call block: call_id, status (answered|no_answer|
  busy|failed|voicemail), hangup_cause, direction, did, caller_phone_hash,
  started_at, durations{total_seconds, ivr_seconds, hold_seconds,
  agent_seconds}, recording{url, transcript_url, storage_ref},
  ivr{digits, path}, disposition, agent{id, name} + consumer + location +
  compliance + attributes.
- `ping`: non-PII offer. ping_id, lead_reference, phone_hash, country_code,
  state_region, postal_code, vertical, attributes (banded/aggregated only
  — e.g. loan_amount_band, never exact), compliance_flags (presence, not
  tokens), floor_price_cents, currency (ISO 4217). FORBIDDEN keys:
  first_name, last_name, name, phone, normalized_phone, phone_hash, email,
  postcode, address, street, suburb, dob, date_of_birth, ip, ip_address,
  user_agent, lead_data, consumer, raw_payload.
- `post`: full delivery. lead_id, delivered_at, price_cents, currency,
  buyer_id, buyer_reference, pricing{floor_price_cents, premium_cents,
  final_price_cents, uncapped_price_cents, max_allowed_price_cents,
  price_guardrails, is_duplicate_resub}, matched_preferences, consumer
  (full), location, compliance (full evidence), attributes, provenance.
- `ack`: response to any message. Original message id, status
  (RECEIVED|VALIDATED|ACCEPTED|REJECTED|DUPLICATE|ERROR), errors[],
  lead_id, request_id.
- `event`: lifecycle notification. lead_id, event (DELIVERED|ACCEPTED|
  REJECTED|DISPUTED|REFUNDED|EXPIRED|CONVERTED|ARCHIVED), timestamp,
  details, external_reference.

### Error taxonomy

LCP-001 INVALID_FORMAT (400), LCP-002 UNKNOWN_SENDER (401), LCP-003
MISSING_REQUIRED_FIELD (400), LCP-004 INVALID_STATUS_TRANSITION (422),
LCP-005 DUPLICATE_LEAD (409), LCP-006 UNKNOWN_MESSAGE_TYPE (400),
LCP-007 SCHEMA_VERSION_UNSUPPORTED (422), LCP-008 PII_IN_PING (400),
LCP-009 INVALID_PHONE (400), LCP-010 INVALID_TERRITORY (422),
LCP-100 VALIDATION_ERROR (400), LCP-500 INTERNAL_ERROR (500).
Error body: `{ "code", "message", "field", "details" }`.

### Versioning & extensibility

- Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes).
- Per-schema versions (vertical_schemas.version).
- Deprecation: N+2, 2-year grace, migration guidance.
- Extensions: `extensions` object, namespaced `{org}.{division}.{purpose}`.
- Unknown optional fields ignored; unknown message types → structured error.

### Conformance tiers

- L1: envelope + required fields + idempotency + ack + valid timestamps.
- L2: full lifecycle + ping/post split + compliance records.
- L3: full + dedup fingerprints + capability discovery + published JSON
  Schemas + agent binding.

### Capability discovery

`GET /v1/lcp/capabilities` → `{ lcp_versions, message_types,
verticals[{id, schema_version}], markets, auth_methods, events,
conformance_level }`.

### Security

- TLS 1.3. Auth: API keys (Bearer) or HMAC (X-LCP-Signature /
  X-LCP-Timestamp / X-LCP-Idempotency-Key).
- Nonce replay protection.
- PII: ping stripped; post TLS + per-contract; hashes for dedup; PII
  retention policies.
- Rate limits per sender.

### AI-agent readiness

- `channel: agent` / `source_type: agent` in the core — agents are
  first-class senders.
- Agent identity: `provenance.agent{agent_id, acting_for, attestation}`.
- Three agent roles: source (consumer's assistant submits), transporter
  (publisher's AI submits), consumer (buyer's AI accepts — needs
  deterministic response contracts).
- MCP server wrapper is a thin adapter (tools map 1:1 to endpoints);
  the protocol itself is NOT MCP (no JSON-RPC, no sessions, no tool
  invocation in the core). MCP is the first binding; A2A and future
  bindings map the same way.

## Your review tasks

1. **Universal audit.** Find any field, assumption, or example that is
   mortgage-only, AU/US-only, or otherwise not universally applicable.
   The core must work for a gutter lead in Australia, a solar lead in the
   US, a call lead in NZ, and a lead submitted by an AI agent — with zero
   core changes.
2. **Future-proofing (20 years).** What will break? Consider: new lead
   channels (video, IoT, in-app), new compliance regimes (consent
   frameworks beyond TCPA/GDPR), new PII categories (biometrics, voice
   prints), new markets (UK, CA, EU, SG, BR), new verticals, new
   transport (HTTP/3, WebSockets, message queues), AI-agent scale
   (agents submitting millions of leads, agent-to-agent negotiation).
   Is the three-axis model (message type × vertical × lifecycle) the
   right decomposition? What's missing?
3. **PII and compliance.** Is the ping/post PII split sound? Are the
   forbidden keys complete? Is the compliance block future-proof (e.g.,
   consent frameworks that don't exist yet)? Is phone-hash dedup
   privacy-safe (hash-based dedup is vulnerable to dictionary attacks —
   is that acceptable, and should the spec say so)?
4. **Envelope and message design.** Is the envelope right? Should
   sender/receiver be in the envelope or the transport? Is `test` a
   message field or a header? Is the status enum complete and are the
   transitions well-defined? Is `ack` the right response model (vs.
   HTTP status + body)?
5. **Error taxonomy.** Complete? Missing codes (rate limit, auth
   expired, schema mismatch, partial success)? Should errors be
   machine-readable beyond the code (e.g., a stable error registry URL)?
6. **Versioning.** Is semver + per-schema versioning + N+2 deprecation
   sound for a protocol with 20-year ambitions? What happens when a
   vertical schema and the core disagree? How do you handle
   forward-compat (unknown fields, unknown message types, unknown
   events)?
7. **MCP/AI-agent integration.** Is "REST core + MCP wrapper" the right
   architecture, or should the spec define an agent-native surface?
   What do agents need that the current design lacks (e.g., batch
   submission, streaming status, tool schemas, agent identity/attestation)?
8. **Governance.** Apache 2.0 + anti-capture clause + extension registry
   + self-declared conformance — sound? What's missing for a credible
   open standard (e.g., trademark policy, spec-site requirements,
   security disclosure process)?
9. **Naming.** "LCP" collides with Largest Contentful Paint. Is that a
   real problem? Alternatives?
10. **Prioritized gap list.** Rank every issue you find by severity
    (blocker / should-fix / nice-to-have) and by whether it must be
    resolved before v1.0 or can be deferred to v1.1+.

## Output format

1. **Verdict** (1 paragraph): is this design sound as the foundation of
   a 20-year open standard?
2. **Blockers** (must fix before v1.0) — each with a concrete fix.
3. **Should-fix** (v1.0 if cheap, else v1.1) — each with a concrete fix.
4. **Deferred** (v1.1+) — each with a trigger condition.
5. **Universal audit result** — any non-universal field found, with fix.
6. **MCP/AI-agent assessment** — is the wrapper architecture right?
7. **Naming verdict.**
8. **Anything we missed entirely.**

Be specific. Cite the exact field, message type, or section you are
criticizing. Prefer concrete fixes over general advice. If something is
genuinely fine, say so — do not manufacture problems.

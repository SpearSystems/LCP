# LCP — Lead Context Protocol Specification

**Status:** DRAFT v1.0 — under active development. Not yet published.
**License:** Apache 2.0 (see [LICENSE](LICENSE)).

> This is the working skeleton. Sections are filled in as the spec is
> authored. The design is grounded in the production `spx-pingpost-v1`
> wire contract (HMAC signing, idempotency, nonce replay protection,
> PII-stripped pings) and the industry-standard ping/post model.

## 1. Overview

LCP is a universal protocol for exchanging consumer lead data between
publishers, platforms, and buyers. It is:

- **Channel-agnostic** — form fills, calls, chats, clicks, API, AI agents.
- **Vertical-agnostic** — the core has zero vertical-specific fields;
  verticals are typed extension blocks.
- **Market-agnostic** — ISO 3166-1 country codes, per-country territory
  and postal validation, ISO 4217 currency.
- **PII-disciplined** — ping messages carry non-PII attributes + hashes
  only; full PII flows only in post messages to the winning buyer.
- **Agent-ready** — agents are first-class senders via a binding layer
  (MCP first), never coupled into the core.

### Design principles

1. Universal core, extensible verticals.
2. Channel-agnostic message types.
3. PII discipline (ping/post split).
4. Envelope/payload separation.
5. Semver + per-schema versions + N+2 deprecation.
6. Mandatory idempotency.
7. Structured errors.
8. Machine-readable schemas (JSON Schema).
9. Capability discovery.
10. Conformance tiers L1–L3, self-declared.
11. Backward compatibility.

## 2. Envelope

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

Schema: [schemas/envelope.json](schemas/envelope.json)

## 3. Canonical core

The core contains zero vertical-specific or market-specific fields.

### consumer
`first_name`, `last_name`, `email`, `phone` (E.164), `phone_hash`
(sha256 of normalized E.164), `dob` (sensitive), `gender` (sensitive).
At least one of name/email/phone required.

### location
`country_code` (ISO 3166-1 alpha-2, REQUIRED), `state_region`
(per-country validation registry), `postal_code` (per-country format),
`city`, `timezone` (IANA). `state_region` OR `postal_code` required.

### compliance
`consent_timestamp`, `consent_source_url`, `consent_text_version`,
`jornaya_token`, `trustedform_token`, `otp_verified`,
`session_capture_id`, `call_recording_id`, `dnc_checked`,
`tcpac_consent`, extensible.

### provenance (non-PII)
`received_at`, `source_type` (publisher|direct|agent|api|referral),
`source_id`, `source_url`, `ip_hash`, `user_agent_hash`, `funnel_key`,
`flow_key`, `campaign_id`, `creative_id`, `landing_page_url`.

### attributes
Vertical-specific, JSON Schema per vertical (see [verticals/](verticals/)).
`vertical` + `schema_version` required.

### status
`NEW`, `PINGED`, `POSTED`, `ACCEPTED`, `REJECTED`, `DUPLICATE`,
`DISPUTED`, `REFUNDED`, `EXPIRED`, `CONVERTED`, `ARCHIVED`.

## 4. Message types

| Type | Purpose | PII |
|---|---|---|
| `lead` | Intake (form/chat/click/api/agent) | Full |
| `call` | Telephony lead | Full |
| `ping` | Non-PII offer to buyers | None (hashes only) |
| `post` | Full delivery to winning buyer | Full |
| `ack` | Response to any message | None |
| `event` | Lifecycle notification | None |

### lead
`consumer` + `location` + `compliance` + `provenance` + `attributes` +
`external_id` + `channel` (form|chat|click|api|agent|referral).

### call
`call` block: `call_id`, `status` (answered|no_answer|busy|failed|
voicemail), `hangup_cause`, `direction`, `did`, `caller_phone_hash`,
`started_at`, `durations{total_seconds, ivr_seconds, hold_seconds,
agent_seconds}`, `recording{url, transcript_url, storage_ref}`,
`ivr{digits, path}`, `disposition`, `agent{id, name}` + `consumer` +
`location` + `compliance` + `attributes`.

### ping
`ping_id`, `lead_reference`, `phone_hash`, `country_code`,
`state_region`, `postal_code`, `vertical`, `attributes` (banded/
aggregated only — never exact), `compliance_flags` (presence, not
tokens), `floor_price_cents`, `currency` (ISO 4217).

**Forbidden keys** (PII must never appear in a ping): `first_name`,
`last_name`, `name`, `phone`, `normalized_phone`, `phone_hash`, `email`,
`postcode`, `address`, `street`, `suburb`, `dob`, `date_of_birth`, `ip`,
`ip_address`, `user_agent`, `lead_data`, `consumer`, `raw_payload`.

### post
`lead_id`, `delivered_at`, `price_cents`, `currency`, `buyer_id`,
`buyer_reference`, `pricing{floor_price_cents, premium_cents,
final_price_cents, uncapped_price_cents, max_allowed_price_cents,
price_guardrails, is_duplicate_resub}`, `matched_preferences`,
`consumer` (full), `location`, `compliance` (full evidence),
`attributes`, `provenance`.

### ack
Original message id, `status` (RECEIVED|VALIDATED|ACCEPTED|REJECTED|
DUPLICATE|ERROR), `errors[]`, `lead_id`, `request_id`.

### event
`lead_id`, `event` (DELIVERED|ACCEPTED|REJECTED|DISPUTED|REFUNDED|
EXPIRED|CONVERTED|ARCHIVED), `timestamp`, `details`, `external_reference`.

## 5. Error taxonomy

| Code | Name | HTTP |
|---|---|---|
| LCP-001 | INVALID_FORMAT | 400 |
| LCP-002 | UNKNOWN_SENDER | 401 |
| LCP-003 | MISSING_REQUIRED_FIELD | 400 |
| LCP-004 | INVALID_STATUS_TRANSITION | 422 |
| LCP-005 | DUPLICATE_LEAD | 409 |
| LCP-006 | UNKNOWN_MESSAGE_TYPE | 400 |
| LCP-007 | SCHEMA_VERSION_UNSUPPORTED | 422 |
| LCP-008 | PII_IN_PING | 400 |
| LCP-009 | INVALID_PHONE | 400 |
| LCP-010 | INVALID_TERRITORY | 422 |
| LCP-100 | VALIDATION_ERROR | 400 |
| LCP-500 | INTERNAL_ERROR | 500 |

Error body: `{ "code", "message", "field", "details" }`.

## 6. Versioning & extensibility

- Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes).
- Per-schema versions (`vertical_schemas.version`).
- Deprecation: N+2, 2-year grace, migration guidance.
- Extensions: `extensions` object, namespaced `{org}.{division}.{purpose}`.
- Unknown optional fields ignored; unknown message types → structured error.

## 7. Conformance tiers

- **L1:** envelope + required fields + idempotency + ack + valid timestamps.
- **L2:** full lifecycle + ping/post split + compliance records.
- **L3:** full + dedup fingerprints + capability discovery + published
  JSON Schemas + agent binding.

## 8. Capability discovery

`GET /v1/lcp/capabilities` → `{ lcp_versions, message_types,
verticals[{id, schema_version}], markets, auth_methods, events,
conformance_level }`.

## 9. Security

- TLS 1.3. Auth: API keys (Bearer) or HMAC (`X-LCP-Signature` /
  `X-LCP-Timestamp` / `X-LCP-Idempotency-Key`).
- Nonce replay protection.
- PII: ping stripped; post TLS + per-contract; hashes for dedup; PII
  retention policies.
- Rate limits per sender.

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
   post timeout, or treated as timeout).

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
[implementations/mcp-server/](implementations/mcp-server/)) exposes:
`submit_lead`, `submit_call`, `query_lead_status`, `get_schema`,
`get_capabilities`, `list_offers`. It is a thin adapter — the core
never depends on MCP.

## 11. Governance

- Apache 2.0 (patent retaliation clause rationale).
- Anti-capture clause: no mandatory fees, approvals, or proprietary
  dependencies.
- Extension registry: namespace registration, format-only validation.
- No mandatory certification; conformance self-declared.
- CLA for contributors.

See [governance/](governance/).

## 12. Universal audit

The core must work for a gutter lead in Australia, a solar lead in the
US, a call lead in NZ, and a lead submitted by an AI agent — with zero
core changes. Audit checklist:

- [ ] No vertical-specific fields in core.
- [ ] No market-specific fields in core.
- [ ] Phone E.164 universal; `phone_hash` sha256.
- [ ] Call leads first-class (`call` message type).
- [ ] AI-agent submissions: `channel=agent`, `source_type=agent`.
- [ ] Legacy `spx-pingpost-v1` fields map 1:1 into LCP.

## 13. TODO (authoring order)

- [ ] Fill envelope + core schemas (`schemas/*.json`).
- [ ] Fill message-type schemas.
- [ ] Fill vertical schemas (`verticals/*.json`).
- [ ] Fill examples (`examples/*.json`).
- [ ] Fill test vectors (`test-vectors/`).
- [ ] Run the deep-research review pass (`docs/lcp-deep-research-prompt.md`).
- [ ] Resolve or explicitly defer every finding.
- [ ] Publish decision (repo, spec site, branding).

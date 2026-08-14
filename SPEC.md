# LCP — Lead Context Protocol Specification

**Status:** DRAFT v1.0 — under active development. Not yet published.
**License:** Apache 2.0 (see [LICENSE](LICENSE)).

> This is the working specification. The design is grounded in the
> production ping/post wire contract (HMAC signing, idempotency,
> nonce replay protection, PII-stripped pings) and the industry-standard
> ping/post model. Findings from the deep-research review pass
> (`docs/lcp-deep-research-review.md`) have been resolved — see the
> review-log appendix (§14).

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

**Scope.** LCP governs lead data exchange — the envelope, payload,
lifecycle, and compliance evidence. It does **not** govern money
movement (settlement, invoicing, payout); financial arrangements are
bilateral between parties. LIP lifecycle statuses (`accepted`,
`rejected`, `disputed`, `refunded`, `converted`) describe the lead's
state, not the payment's state.

### Design principles

1. Universal core, extensible verticals.
2. Channel-agnostic message types.
3. PII discipline (ping/post split).
4. Envelope/payload separation.
5. Semver + per-schema versions + N+2 deprecation.
6. Mandatory idempotency.
7. Structured errors (array-shaped, multi-error).
8. Machine-readable schemas (JSON Schema).
9. Capability discovery.
10. Conformance tiers L1–L3, self-declared.
11. Backward compatibility with open/closed enum policy.

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
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "pub-lead-20260815-001",
      "test": false
    },
    "payload": {}
  }
}
```

The envelope is transport-agnostic. `sender_id` and `receiver_id` live
in the envelope (not transport headers) so the message is self-describing
on any transport (HTTP, message queue, WebSocket).

The `test` field is canonical in the envelope. For the HTTP binding,
`X-LCP-Test: true` MUST also be sent as a header so infrastructure can
filter test traffic without parsing the JSON body.

Schema: [schemas/envelope.json](schemas/envelope.json)

## 3. Canonical core

The core contains zero vertical-specific or market-specific fields.

### consumer

| Field | Type | Notes |
|---|---|---|
| `first_name` | string | Given name. |
| `last_name` | string | Family name. |
| `full_name` | string | Unstructured name — use when Western given/family splitting does not apply (large fraction of global names). |
| `email` | string (email) | |
| `phone` | string | E.164 format. |
| `phone_hash` | string | `HMAC-SHA256(shared_secret, e164)` — see §9. |
| `email_hash` | string | `HMAC-SHA256(shared_secret, normalized_email)` — per-pair, for email dedup in ping. |
| `dob` | string (date) | Sensitive. |
| `locale` | string | BCP-47 language tag (e.g. `en-AU`, `es-US`). Optional; for non-English free-text fields. |

At least one of name (`first_name`+`last_name` OR `full_name`),
`email`, or `phone` is required.

> **`gender` removed from core.** Gender was inherited from
> insurance/mortgage underwriting and is not universally needed (a
> gutter lead or solar lead does not require it). Vertical schemas that
> need gender (e.g. insurance) define it in `attributes`.

### location

`country_code` (ISO 3166-1 alpha-2, REQUIRED), `state_region`
(per-country validation registry), `postal_code` (per-country format),
`city`, `timezone` (IANA). `state_region` OR `postal_code` required.

### compliance

Consent and regulatory compliance evidence. The core defines a generic
evidence array — no vendor-specific fields are baked into the core.

| Field | Type | Notes |
|---|---|---|
| `consent_timestamp` | string (datetime) | When consent was captured. |
| `consent_source_url` | string (uri) | URL of the consent page/form. |
| `consent_text_version` | string | Version identifier of the consent text shown. |
| `consent_purposes` | array\<string\> | What was consented to (e.g. `"calls"`, `"sms"`, `"email"`, `"share_with_partners"`). Consent *scope* is what gets litigated — capture it. |
| `consent_evidence` | array\<object\> | Structured evidence records. See below. |
| `otp_verified` | boolean | One-time-passcode verified. |
| `otp` | object | OTP details: `channel` (sms/email/voice), `verified_at`, `verified_value_hash`, `attempts`. The actual OTP code MUST NOT be included — it is ephemeral and poses a replay risk. |
| `session_capture_id` | string | Session replay reference (e.g. rrweb). |
| `call_recording_id` | string | Call recording reference. |
| `dnc_checked` | boolean | Do-not-call list checked. |
| `consent_expires_at` | string (datetime) | When consent validity expires. Optional — absent = does not expire. Buyers should not contact after this. |
| `scrubs` | array\<object\> | Compliance scrubbing results. See below. |

#### `scrubs[]` entries

Each entry is one scrub check performed against the consumer's
phone/email:

| Field | Type | Notes |
|---|---|---|
| `type` | string | Scrub type: `dnc_national`, `dnc_state`, `dnc_internal`, `litigator`, `blacklist`, `fraud_check`, `carrier_lookup`, `number_portability`. Open enumeration. |
| `provider` | string | Service that performed the scrub (e.g. `ftc_dnc`, `au_dncregister`, `litigator_list`, `tcpa_scrub`, `first_orion`, `hiya`, `internal`). |
| `result` | string | `clean` (passed), `flagged` (on a list, buyer decides), `blocked` (should not be contacted), `not_checked`. |
| `checked_at` | string (datetime) | When the scrub was run. |
| `details` | string | Optional human-readable detail. Do not include PII. |

The `result` values are deliberately split into three actionable states:
- `clean` — the consumer is not on any list; safe to contact.
- `flagged` — the consumer appears on a list (e.g. known litigator, DNC
  registry) but the publisher did not block the lead. The buyer decides
  whether to accept. This is important for TCPA compliance: a
  `litigator` flag doesn't mean the lead is invalid, it means the buyer
  should be aware of elevated legal risk.
- `blocked` — the consumer is on a list and the publisher blocked
  further processing. This lead should not have been delivered. If a
  buyer receives a `blocked` lead, they should reject it immediately.

#### `consent_evidence[]` entries

```json
{
  "type": "tcpa_consent",
  "provider": "jornaya",
  "token_or_url": "jrn_abc123..."
}
```

| Field | Type | Notes |
|---|---|---|
| `type` | string | Consent framework or evidence type (e.g. `"tcpa_consent"`, `"gdpr_opt_in"`, `"verified_consent"`). |
| `provider` | string | Vendor or system that generated the evidence (e.g. `"jornaya"`, `"trustedform"`, `"internal"`, `"eu-consent-platform"`). |
| `token_or_url` | string | Verification token, certificate URL, or reference. |

The `consent_evidence` array is extensible — new consent frameworks
(GDPR, LGPD, CCPA, future regimes) register new `type` values without
core changes. US-specific vendors (Jornaya, TrustedForm) are
*examples* of providers, not required core fields. See the non-normative
provider examples in [Appendix A](#appendix-a-consent-evidence-provider-examples).

### provenance (non-PII)

`received_at`, `source_type` (publisher|direct|agent|api|referral|
organic|marketplace|outbound|scraped — open enum), `acquisition_method`
(paid_ad|organic_search|marketplace|gmb|directory|cold_outbound|
warm_transfer|scraped|unknown — open enum), `is_incentivized` (boolean),
`incentive_type` (free_consultation|gift_card|discount_coupon|free_quote|
cash_offer — open enum), `source_id`, `source_url`, `ip_hash`,
`user_agent_hash`, `funnel_key`, `flow_key`, `campaign_id`, `creative_id`,
`landing_page_url`, `platform_source` (google_ads|facebook|tiktok|
bing_ads|organic|direct, open enum).

Agent-specific provenance (when `source_type` = `agent`):

| Field | Type | Notes |
|---|---|---|
| `provenance.agent.agent_id` | string | Unique agent identifier. |
| `provenance.agent.acting_for` | string | Party the agent acts on behalf of. |
| `provenance.agent.attestation` | string | Signed verification token (JWT recommended — see §9). |

### attributes

Vertical-specific, JSON Schema per vertical (see [verticals/](verticals/)).
`vertical` + `schema_version` required.

Every field in a vertical schema MUST be tagged `ping_safe: true` or
`ping_safe: false`. The conformance runner (L2/L3) mechanically rejects
any `ping` payload containing a non-`ping_safe` field. This closes the
loophole where vertical-specific fields leak PII through `attributes`.

Vertical schemas MUST NOT redefine core field names (`phone`, `email`,
`first_name`, `last_name`, `full_name`, `country_code`, etc.) inside
`attributes` — core field names are reserved. See §12.

When a vertical needs country-specific data (e.g. mortgage product
types like HELOC, FHA, VA, etc.), model it as a country-scoped object
within the vertical schema — NOT as separate per-country vertical files.
Each country-scoped object has a `country_code` (ISO 3166-1 alpha-2)
required field and nullable country-specific enum fields. See AGENTS.md
rule 7 for details.

Performance metrics (speed-to-contact, contact rate, close rate, close
value) are **buyer-side analytics**, not lead data. They belong in the
extensions mechanism, not in the core or vertical schemas:

```json
"extensions": {
  "acme.metrics.performance": {
    "speed_to_contact_seconds": 45,
    "contact_rate": 0.72,
    "close_rate": 0.18
  }
}
```

The `CONVERTED` event's `details.conversion_value_cents` is the one
exception — close value on conversion is structured in the event
(see §4 event) because it's a lifecycle fact, not a calculated metric.

### status

`NEW`, `PINGED`, `POSTED`, `ACCEPTED`, `REJECTED`, `DUPLICATE`,
`DISPUTED`, `REFUNDED`, `EXPIRED`, `CONVERTED`, `ARCHIVED`.

#### Status transition table

Terminal states are marked with §. Non-terminal states can transition
per the legal edges below. All unspecified transitions are invalid
(error `LCP-004 INVALID_STATUS_TRANSITION`).

```
NEW ──────────────► PINGED ──────────► POSTED
 │                    │                   │
 │                    │                   ├─► ACCEPTED ──► CONVERTED §
 │                    │                   │      │
 │                    │                   │      ├─► DISPUTED ──► REFUNDED §
 │                    │                   │      │      └────► ACCEPTED (resolved)
 │                    │                   │      └─► ARCHIVED §
 │                    │                   │
 │                    │                   ├─► REJECTED §
 │                    │                   │
 │                    │                   ├─► EXPIRED §
 │                    │                   │
 │                    │                   └─► DUPLICATE §
 │                    │
 │                    ├─► EXPIRED §
 │                    │
 │                    └─► DUPLICATE §
 │
 ├─► REJECTED §
 │
 ├─► EXPIRED §
 │
 └─► DUPLICATE §
```

**Terminal states** (no outgoing transitions): `CONVERTED`, `REFUNDED`,
`REJECTED`, `ARCHIVED`, `EXPIRED`, `DUPLICATE`.

**Notable resolution path:** `DISPUTED → ACCEPTED` (a dispute can
resolve in the buyer's favor) and `DISPUTED → REFUNDED` (dispute
resolves in the publisher's favor, lead is refunded).

### lead expiry

Leads MAY carry an `expires_at` (ISO 8601 datetime) or `ttl_seconds`
(integer) field. If the lead reaches its expiry without being accepted,
the platform transitions it to `EXPIRED`.

### exclusivity

Leads MAY carry an `exclusivity` field:

| Field | Type | Notes |
|---|---|---|
| `exclusivity` | string | `"exclusive"` (single buyer) or `"shared"` (multiple buyers). Default: `"shared"`. |
| `max_buyers` | integer | For `shared` leads, maximum number of buyers who may receive the lead. |

This models the commercial reality of lead selling (exclusive vs.
shared leads) — distinct from `pricing.is_duplicate_resub`, which
addresses accidental resubmission.

## 4. Message types

| Type | Purpose | PII |
|---|---|---|
| `lead` | Intake (form/chat/click/api/agent/referral) | Full |
| `call` | Telephony lead | Full |
| `ping` | Non-PII offer to buyers | None (hashes only) |
| `bid` | Buyer's response to a ping (price, decision) | None |
| `post` | Full delivery to winning buyer | Full |
| `ack` | Response to any message | None |
| `event` | Lifecycle notification | None |

> **Design note — `call` vs. `lead` with `channel: call`:** The review
> suggested consolidating `call` into `lead` with `channel: call`. We
> keep `call` as a separate message type because the `call` block (IVR
> path, durations, recording, disposition, agent) is structurally
> distinct from form-based intake. Two message types sharing
> `consumer`/`location`/`compliance` is cleaner than conditional blocks
> inside `lead`. Contributors who want to revisit this can open a
> governance discussion with a concrete proposal.

### lead

`consumer` + `location` + `compliance` + `provenance` + `attributes` +
`external_id` + `submitted_at` + `channel` (form|chat|click|api|agent|
referral) + optional `expires_at` / `ttl_seconds` + optional `exclusivity`
+ optional `contact_window` (timezone, available_from/to, days — publisher
hint for when the consumer can be contacted) + optional `lead_quality`
(publisher-declared quality signals: `verified_phone`, `verified_email`,
`verification{phone_method, phone_verified_by, phone_verified_at,
email_method, email_verified_by, email_verified_at}`, `duplicate_risk`,
`spam_risk_score`, `data_completeness`).

### call

`call` block: `call_id`, `status` (answered|no_answer|busy|failed|
voicemail), `hangup_cause`, `direction`, `did`, `caller_phone_hash`,
`started_at`, `durations{total_seconds, ivr_seconds, hold_seconds,
agent_seconds}`, `recording{url, transcript_url, storage_ref}`,
`ivr{digits, path}`, `disposition`, `transferred_from{original_call_id,
transfer_reason}`, `queue{id, name}`, `agent{id, name}` + `consumer` +
`location` + `compliance` + `attributes`.

### ping

Non-PII offer to buyers. The ping schema is a **strict allowlist**
(`additionalProperties: false`), not a blocklist. Only the fields
listed below are permitted — any other field is a validation error
(`LCP-008 PII_IN_PING`).

| Field | Type | Notes |
|---|---|---|
| `ping_id` | string | Unique ping identifier. |
| `lead_reference` | string |Opaque reference to the lead (not the lead_id). |
| `publisher_id` | string | ID of the publisher that generated the lead. |
| `offer_id` | string | ID of the buyer offer this ping targets. |
| `phone_hash` | string | `HMAC-SHA256(shared_secret, e164)` — per-pair, see §9. |
| `email_hash` | string | `HMAC-SHA256(shared_secret, normalized_email)` — per-pair, optional. |
| `country_code` | string | ISO 3166-1 alpha-2. |
| `state_region` | string | |
| `postal_code` | string | |
| `vertical` | string | |
| `lead_age_minutes` | integer | Minutes since consumer submitted. Freshness signal for pricing. |
| `attributes` | object | Only fields tagged `ping_safe: true` in the vertical schema. Banded/aggregated only — never exact values. |
| `compliance_flags` | object | Presence indicators only (e.g. `{"consent": true, "otp_verified": true}`), never tokens or evidence. |
| `floor_price_cents` | integer | |
| `currency` | string | ISO 4217. |
| `exclusivity` | string | `exclusive` or `shared` — hint to buyer. |
| `dedup_window_hours` | integer | Sender hint: how many hours to dedup against. |
| `expires_at` | string (datetime) | Optional ping expiry. |

The conformance runner mechanically validates:
1. Ping payload has no fields outside the allowlist (`additionalProperties: false`).
2. Every field in `attributes` is tagged `ping_safe: true` in the vertical schema.
3. No `consumer`, `compliance` (full), `provenance` (full), or other PII-bearing block is present.

### bid

Buyer's response to a ping. No PII — the buyer has not received the
lead yet, only the ping attributes.

| Field | Type | Notes |
|---|---|---|
| `ping_id` | string | ID of the ping being responded to. |
| `decision` | string | `accept` (wants the lead at bid price), `reject` (does not want), `pass` (declines to bid). |
| `bid_price_cents` | integer | Bid price in cents. Required when `decision = accept`. |
| `currency` | string | ISO 4217. |
| `estimated_contact_seconds` | integer | Estimated time-to-contact. For speed-to-contact routing. |
| `buyer_reference` | string | Buyer's internal tracking reference. |
| `reject_reason` | string | Why rejected (e.g. `out_of_hours`, `capacity_full`, `vertical_mismatch`). Open enum. |
| `capacity_remaining` | integer | Remaining capacity after this bid. Helps platform route future pings. |

The ping/bid/post flow:

```
Platform ──ping──► Buyer A ──bid(accept, $22)──► Platform
         ──ping──► Buyer B ──bid(accept, $18)──► Platform
         ──ping──► Buyer C ──bid(pass)─────────► Platform

Platform selects Buyer A (highest bid)
Platform ──post──► Buyer A (full PII delivered)
```

The platform collects bids within the ping expiry window. The winner is
selected by the platform's routing logic (highest bid, fastest estimated
contact, exclusivity rules, etc.). The platform then sends a `post` to
the winner. Losers receive no further messages (no PII was shared).

### post

`lead_id`, `delivered_at`, `submitted_at`, `offer_id`, `price_cents`,
`currency`, `buyer_id`, `buyer_reference`, `pricing{floor_price_cents,
premium_cents, final_price_cents, uncapped_price_cents,
max_allowed_price_cents, price_guardrails, is_duplicate_resub,
payable_definition, payable_status, dispute_window_hours,
dispute_window_expires_at}`, `matched_preferences`,
`consumer` (full), `location`, `compliance` (full evidence),
`attributes`, `provenance`, optional `exclusivity`.

### ack

Response to any message. `ack` uses an **array of errors** — a single
message can fail multiple field validations.

| Field | Type | Notes |
|---|---|---|
| `original_message_id` | string | ID of the message being acknowledged. |
| `status` | string | `RECEIVED` \| `VALIDATED` \| `ACCEPTED` \| `REJECTED` \| `DUPLICATE` \| `ERROR` |
| `errors` | array\<error\> | Zero or more error objects (see §5). |
| `lead_id` | string | Assigned lead ID, if applicable. |
| `request_id` | string | Server-side request ID for tracing. |
| `rejection_reason` | string | Structured rejection reason (open enum: `invalid_phone`, `duplicate`, `out_of_geography`, `credit_too_low`, `already_customer`, `compliance_fail`, `capacity_exceeded`, etc.). Enables publisher automation. |

### event

`lead_id`, `event` (DELIVERED|ACCEPTED|REJECTED|DISPUTED|REFUNDED|
EXPIRED|CONVERTED|ARCHIVED), `timestamp`, `details`, `external_reference`.

For `CONVERTED` events, `details` SHOULD include:

| Field | Type | Notes |
|---|---|---|
| `conversion_type` | string | e.g. `loan_settled`, `policy_issued`, `appointment_booked`. |
| `conversion_value_cents` | integer | Deal/loan/policy value in cents. |
| `conversion_currency` | string | ISO 4217. |
| `converted_at` | string (datetime) | When the conversion occurred. |
| `buyer_reference` | string | Buyer's CRM deal/opp ID. |

For `DISPUTED` events, `details` SHOULD include:

| Field | Type | Notes |
|---|---|---|
| `dispute_reason` | string | e.g. `invalid_phone`, `duplicate`, `out_of_scope`, `already_customer`. |
| `dispute_evidence_url` | string (uri) | Optional evidence (recording, screenshot). |

These are recommended shapes — `details` is `additionalProperties: true`,
so platforms can add custom fields without schema changes.

For `CONSENT_WITHDRAWN` events (consumer opted out after delivery),
`details` SHOULD include:

| Field | Type | Notes |
|---|---|---|
| `phone_hash` | string | Hash of the consumer's phone for suppression. |
| `email_hash` | string | Hash of the consumer's email for suppression. |
| `withdrawn_at` | string (datetime) | When consent was withdrawn. |
| `withdrawn_purposes` | array\<string\> | Which purposes were withdrawn (calls, sms, email, etc.). |

Buyers receiving this event MUST update their contact suppression list.

For `ERASURE_REQUEST` events (consumer requested data deletion under
CCPA / AU Privacy Act / GDPR), `details` SHOULD include:

| Field | Type | Notes |
|---|---|---|
| `phone_hash` | string | Hash for identifying the lead to delete. |
| `email_hash` | string | Hash for identifying the lead to delete. |
| `reason` | string | e.g. `ccpa_request`, `gdpr_erasure`, `privacy_act_request`. |
| `requested_at` | string (datetime) | When the erasure was requested. |
| `deadline_at` | string (datetime) | Legal deadline for deletion. |

Buyers receiving this event MUST delete the lead data and respond with
an `ack`. The event open enumeration supports both `CONSENT_WITHDRAWN`
and `ERASURE_REQUEST` without schema changes.

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
| LCP-011 | RATE_LIMITED | 429 |
| LCP-012 | SIGNATURE_INVALID | 401 |
| LCP-100 | VALIDATION_ERROR | 400 |
| LCP-500 | INTERNAL_ERROR | 500 |

**Error shape** (used everywhere — both HTTP error responses and
`ack.errors[]`):

```json
{
  "errors": [
    {
      "code": "LCP-003",
      "name": "MISSING_REQUIRED_FIELD",
      "message": "consumer.phone is required when no email or name is provided",
      "field": "consumer.phone",
      "details": {}
    }
  ]
}
```

`LCP-002 UNKNOWN_SENDER` covers an unrecognized `sender_id`.
`LCP-012 SIGNATURE_INVALID` covers a recognized sender whose HMAC
signature is invalid or timestamp is expired — these are different
failure modes (identity vs. replay protection) and now have distinct
codes.

`LCP-011 RATE_LIMITED` (429) pairs with the per-sender rate limits
defined in §9.

## 6. Versioning & extensibility

- Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes).
- Per-schema versions (`vertical_schemas.version`).
- Deprecation: N+2, 2-year grace, migration guidance.
- Extensions: `extensions` object, namespaced `{org}.{division}.{purpose}`,
  registered in [governance/EXTENSION-REGISTRY.md](governance/EXTENSION-REGISTRY.md).
- Unknown optional fields ignored; unknown message types → structured error.

### Open vs. closed enumerations

Over the protocol's 20+ year lifetime, new values will appear in
several enum fields. The forward-compatibility policy is:

| Axis | Policy | Unknown value behavior |
|---|---|---|
| `message.type` | **Closed** | Reject with `LCP-006 UNKNOWN_MESSAGE_TYPE`. Adding a message type is a MINOR version bump. |
| `status` | **Open** | Store and pass through. Do not treat as a validation failure. |
| `channel` | **Open** | Store and pass through. |
| `event` | **Open** | Store and pass through. |
| `call.status` | **Open** | Store and pass through. |
| `consent_evidence.type` | **Open** | Store and pass through. |

This ensures that adding a new `status` (e.g. a future
`PARTIALLY_REFUNDED`) or a new `channel` (e.g. `video`, `iot`) is an
additive MINOR change, not a breaking MAJOR change.

## 7. Conformance tiers

- **L1:** envelope + required fields + idempotency + ack + valid timestamps.
- **L2:** full lifecycle + ping/post split (strict allowlist enforcement)
  + compliance records (consent evidence + purposes).
- **L3:** full + dedup fingerprints + capability discovery + published
  JSON Schemas + agent binding (attestation verification).

## 8. Capability discovery

`GET /v1/lcp/capabilities` → `{ lcp_versions, message_types,
verticals[{id, schema_version}], countries, auth_methods, events,
conformance_level, delivery_windows[], capacity }`.

### capacity

Optional. Declares the endpoint's current lead acceptance capacity.

| Field | Type | Notes |
|---|---|---|
| `daily_cap` | integer | Max leads per calendar day (in endpoint's timezone). |
| `daily_remaining` | integer | Leads remaining today. |
| `hourly_cap` | integer | Max leads per hour. |
| `hourly_remaining` | integer | Leads remaining this hour. |
| `concurrent_call_cap` | integer | Max concurrent calls (for call buyers). |
| `reset_at` | string (datetime) | When daily/hourly counters reset. |

If `capacity` is absent, no caps are declared (unlimited). Caps are
advisory — the platform may still route leads above cap, but the
endpoint may reject them (LCP-011 RATE_LIMITED or a 200 with
`ack.status = REJECTED`).

### offer restrictions

Offers (`GET /v1/lcp/offers`) may declare restrictions — lead
characteristics the buyer will not accept. The platform filters against
these before pinging:

| Field | Type | Notes |
|---|---|---|
| `excluded_source_types` | array\<string\> | Source types the buyer rejects (e.g. `["scraped", "outbound", "marketplace"]`). |
| `excluded_acquisition_methods` | array\<string\> | Acquisition methods the buyer rejects (e.g. `["cold_outbound", "scraped"]`). |
| `reject_incentivized` | boolean | If true, buyer will not accept incentivized leads (`is_incentivized = true`). |
| `excluded_incentive_types` | array\<string\> | Specific incentive types the buyer rejects (e.g. `["free_consultation"]`). |
| `require_verified_phone` | boolean | If true, buyer requires `verified_phone = true`. |
| `require_verified_email` | boolean | If true, buyer requires `verified_email = true`. |
| `max_spam_risk_score` | integer | Buyer will not accept leads with `spam_risk_score` above this value. |
| `excluded_claim_language` | array\<string\> | Landing page / ad copy language the buyer prohibits (e.g. `["guaranteed savings", "free consultation", "no obligation"]`). Publisher declares in `extensions` if their copy contains these. |
| `require_consent_evidence` | boolean | If true, buyer requires `consent_evidence[]` to be non-empty. |
| `reject_dnc_flagged` | boolean | If true, buyer will not accept leads with `dnc_status = flagged` or `blocked`. |
| `reject_litigator_flagged` | boolean | If true, buyer will not accept leads with `litigator_status != clean`. |
| `reject_blacklist_flagged` | boolean | If true, buyer will not accept leads with `blacklist_status != clean`. |
| `min_data_completeness` | string | Buyer requires at least this completeness level (`minimal`, `standard`, `rich`). |

These are **contractual preferences**, not protocol enforcement. The
platform uses them to filter before pinging. A buyer receiving a lead
that violates their restrictions may reject it (`ack.status = REJECTED`
with `LCP-100 VALIDATION_ERROR`).

`delivery_windows` is an optional array of time windows when the endpoint
accepts lead/call delivery. Each entry:

| Field | Type | Notes |
|---|---|---|
| `timezone` | string | IANA timezone for the window (e.g. `Australia/Sydney`). |
| `available_from` | string | Local time `HH:MM` (24h). |
| `available_to` | string | Local time `HH:MM` (24h). |
| `days` | array\<string\> | `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`. |
| `verticals` | array\<string\> | Optional — restricts window to specific verticals. |
| `channels` | array\<string\> | Optional — restricts window to specific channels (e.g. `["call"]` for call-only buyers). |

If `delivery_windows` is absent, the endpoint accepts delivery 24/7.

`countries` is an array of ISO 3166-1 alpha-2 country codes the
endpoint supports (e.g. `["AU", "NZ", "US"]`). This replaces the
legacy `markets` field — no more `au_nz` / `us` grouping.

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

Nonce replay protection via `X-LCP-Timestamp` (reject if outside
acceptable window).

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

### Agent attestation

`provenance.agent.attestation` is a signed token proving the agent's
identity and authority. The recommended format is a JWT with claims:

| Claim | Description |
|---|---|
| `agent_id` | Unique agent identifier (matches `provenance.agent.agent_id`). |
| `acting_for` | Party the agent acts on behalf of (matches `provenance.agent.acting_for`). |
| `issuer` | Authority that issued the token. |
| `iat` | Issued-at timestamp. |
| `exp` | Expiry timestamp. |

The token is verifiable against a published key (JWKS or pinned public
key). Free-text attestations are non-conformant — the token MUST be
verifiable. This is a fraud-resistance requirement for high-volume
agent-submitted leads.

### Rate limits

Per-sender rate limits are enforced. Exceeding the limit returns
`LCP-011 RATE_LIMITED` (429) with a `Retry-After` header (HTTP binding).

### PII

- Ping: stripped (strict allowlist, no PII).
- Post: TLS + per-contract retention policies.
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
[implementations/mcp-server/](implementations/mcp-server/)) exposes:
`submit_lead`, `submit_call`, `submit_bid`, `query_lead_status`,
`get_schema`, `get_capabilities`, `list_offers`. It is a thin adapter —
the core never depends on MCP.

> **Future:** `submit_leads_batch` and `subscribe_to_events` (webhook/
> push delivery) are planned for v1.1. The current single-item, pull-
> based tools are sufficient for v1.0 but will not scale to high-volume
> agent submission.

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
- [ ] No market-specific fields in core — **including named sub-fields
  of `compliance`** (no vendor names like `jornaya_token` or
  law references like `tcpac_consent` as core fields; use
  `consent_evidence[]` with generic `type`/`provider`).
- [ ] `consumer` does not assume Western name splitting (`full_name`
  available as alternative to `first_name`/`last_name`).
- [ ] `consumer.gender` is NOT in core (moved to vertical `attributes`).
- [ ] Phone E.164 universal; `phone_hash` is HMAC-SHA256 (per-pair).
- [ ] Call leads first-class (`call` message type).
- [ ] AI-agent submissions: `channel=agent`, `source_type=agent`.
- [ ] Ping is a strict allowlist (`additionalProperties: false`), not a
  blocklist.
- [ ] Vertical schemas tag every field `ping_safe: true/false`.
- [ ] Vertical schemas do not shadow core field names in `attributes`.
- [ ] `capabilities.countries` is ISO 3166-1 alpha-2 codes (no legacy
  `markets` grouping).
- [ ] Status transition table is published and matches §3.
- [ ] Open/closed enum policy is stated (§6).
- [ ] Legacy production wire contract fields map 1:1 into LCP.

## 13. TODO (authoring order)

- [x] Run the deep-research review pass (`docs/lcp-deep-research-prompt.md`).
- [x] Resolve or explicitly defer every finding (see §14).
- [x] Fill envelope + core schemas (`schemas/*.json`).
- [x] Fill message-type schemas (lead, call, ping, post, ack, event).
- [x] Fill vertical schemas — mortgage first (`verticals/mortgage.json`).
- [x] Tag every vertical attribute field `ping_safe: true/false`.
- [x] Fill examples (`examples/*.json`).
- [x] Fill test vectors (`test-vectors/`).
- [x] Write conformance runner (L1/L2/L3) — 27/27 pass.
- [x] Write reference MCP server (`implementations/mcp-server/`).
- [x] Add `governance/SECURITY.md` (responsible disclosure).
- [x] Add trademark/usage policy for "LCP compliant" claims.
- [x] Fill extension registry format (namespace, registration, payload location).
- [x] Write CLA full text (`governance/CLA.md`).
- [ ] Spec site (LEX-style).
- [ ] Publish decision (repo public, branding, announcement).

## 14. Review-log appendix

Findings from the deep-research review
(`docs/lcp-deep-research-review.md`) and their resolution:

### Blockers — all resolved

| ID | Finding | Resolution | Section |
|---|---|---|---|
| B1 | Ping PII safety is a blocklist | Replaced with strict allowlist (`additionalProperties: false`) + `ping_safe` tagging | §4 ping, §12 |
| B2 | Compliance block hardcodes US vendors | Replaced with generic `consent_evidence[]` array; vendors are examples | §3 compliance |
| B3 | `phone_hash` unsalted sha256 (trivially reversible) | Switched to per-pair `HMAC-SHA256(shared_secret, e164)` | §9 |
| B4 | No status transition graph | Published full transition table with terminal states + resolution paths | §3 status |
| B5 | Open/closed enum policy unstated | Added open/closed enum table: `message.type` closed, all others open | §6 |

### Should-fix — resolved in v1.0

| Finding | Resolution | Section |
|---|---|---|
| `call` vs `lead` consolidation | Kept separate; design note added for contributors | §4 |
| `full_name` for non-Western names | Added to `consumer` | §3 |
| `consumer.gender` in core | Removed; moved to vertical `attributes` | §3 |
| No transport-neutral security home | Added `payload.message.security` block | §9 |
| `test` not mirrored as header | Added `X-LCP-Test` header requirement | §2 |
| Error shape inconsistency (singular vs. array) | Unified to `errors[]` everywhere | §5 |
| Missing `LCP-011 RATE_LIMITED` | Added (429) | §5 |
| `LCP-002` conflates identity + signature | Split into `LCP-002` (identity) + `LCP-012` (signature) | §5 |
| `attestation` is free text | Defined as signed JWT with verifiable claims | §9 |
| Agent-as-consumer timeout undefined | Defined as 30s default with auto-`EXPIRED` fallback | §9 |
| Vertical schemas can shadow core field names | Reserved-namespace rule added | §3 attributes, §12 |
| `capabilities.markets` undefined | Replaced with `countries` (ISO 3166-1 alpha-2) | §8 |
| Consent captures *that*, not *what* | Added `consent_purposes[]` | §3 compliance |
| No lead expiry field | Added `expires_at` / `ttl_seconds` | §3 |
| No exclusivity model | Added `exclusivity` + `max_buyers` | §3 |
| Settlement/billing scope ambiguous | Added scope statement to §1 | §1 |

### Should-fix — deferred to v1.1

| Finding | Trigger | Planned resolution |
|---|---|---|
| Batch submission (`submit_leads_batch`) | Agent submission volume grows | Add as MCP tool + REST endpoint |
| Push delivery (webhook/event subscription) | Polling at volume becomes costly | Add `subscribe_to_events` MCP tool + `POST /v1/lcp/events/subscribe` |
| Platform quality score in ping/post | Platforms want to share independent scoring | Extensions namespace `{org}.platform.assessment` |
| Delivery receipt (`PROCESSED` ack status) | Buyer CRM sync failures need detection | Add `PROCESSED` to ack status enum |
| Vertical-specific buyer criteria in offers | Complex matching rules per vertical | Extensions namespace `{org}.buyer.criteria` |
| Raw IP address in post (full PII context) | Fraud detection + geo-validation | Add `ip_address` to provenance (post only, not ping) |
| Raw form submission data | Dispute evidence — what consumer actually entered | Extensions namespace `{org}.intake.raw_form` |
| Multi-currency pricing | Cross-border lead-gen (US buyer purchasing AU leads) | `price_cents_original` + `fx_rate` fields, or bilateral |
| Core schema version in envelope | Schema-level breaking change detection | `core_schema_version` in message block, or document that protocol version = core schema version |
| Address fields in core (`street_address`) | Multiple verticals need consumer residential address | Add to `location` or document as vertical-specific |
| Spec site (LEX-style) | Adoption grows beyond GitHub | Static site render of SPEC.md |

### Deferred to v1.1+ (with trigger)

| Finding | Trigger |
|---|---|
| Agent-to-agent price negotiation | Agent bidding volume large enough that static floor pricing is suboptimal |
| Formal sensitivity taxonomy for PII | A market regulates a category not covered (e.g. biometric) |
| Multi-maintainer governance | External contributors adopt LCP |
| LEP (LCP Enhancement Proposal) process | Community growth requires structured proposal workflow |
| Hosted conformance sandbox | Self-hosted conformance runner is insufficient for certification |
| Split SPEC.md into focused documents | Spec exceeds ~1000 lines or gets second major section |

### Naming

The review flagged that "LCP" collides with Largest Contentful Paint
(Google Core Web Vitals). This is a real discoverability concern. The
decision is to **keep the name "LCP"** — the collision is a marketing/
SEO problem, not a protocol problem. Mitigation: consistent "Lead
Context Protocol" branding, a spec site with topical authority, and
disambiguation in public materials.

---

## Appendix A: Consent evidence provider examples

> Non-normative. These are examples of `consent_evidence` provider/type
> values, not required core fields.

| Provider | Type | Market | Notes |
|---|---|---|---|
| `jornaya` | `tcpa_consent` | US | Lead consent certification. |
| `trustedform` | `tcpa_consent` | US | Form consent certification. |
| `internal` | `verified_consent` | Any | Publisher's own consent system. |
| `eu-consent-platform` | `gdpr_opt_in` | EU | GDPR opt-in evidence. |
| `anatel-consent` | `lgpd_consent` | BR | Brazilian LGPD consent. |
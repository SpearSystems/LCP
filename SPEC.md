# LCP — Lead Context Protocol Specification

> **Created and maintained by Spear Systems** (a Spear company).
> Open standard — Apache 2.0, free to implement.

**Status:** v1.0 — published specification.
**License:** Apache 2.0 (see [LICENSE](LICENSE)).

> This is the canonical specification. The design is grounded in the
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

### attachments

Lead, call, and post payloads MAY include `attachments[]` metadata for
contracts, medical records, insurance documents, call recordings, or other
agreed evidence. Each entry contains `attachment_id`, `purpose`, `filename`,
`content_type`, `size_bytes`, `sha256`, `storage_ref`, and `created_at`, with
optional expiry, residency, encryption, and malware-scan markers. A production
attachment MUST have a clean malware-scan result before downstream release. A
deployment MUST enforce sender ownership, hash integrity, content limits,
authorization, retention, and country/region residency. The HTTP reference
profile uploads bytes through an authenticated attachment endpoint; the
protocol does not mandate a particular object-store provider. The reference
platform's production adapter uses an S3-compatible API with provider-side
SSE-KMS and an opaque storage reference.

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

**Slot semantics (v1.1, LEP-0003).** The budget `max_buyers` counts
**distinct awarded buyers**, not delivered copies: one awarded buyer
consumes exactly **one slot** regardless of how many matching offers
they hold, while delivery remains per `(lead, offer)` — so the number
of delivered post copies for a lead can legally exceed the budget
(a single awarded buyer holding two matching offers receives two posts
inside their one slot). `exclusivity: "exclusive"` is exactly the
one-slot special case. The undeclared case is normative: a `shared`
lead without `max_buyers` has a slot budget of **1**. A conformant
platform MUST NOT award more distinct buyers than the budget (never
oversell) and MUST NOT advertise zero remaining slots — zero remaining
means stop pinging, not ping with zero (see §4 `slots_available`).

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
| `slots_available` | integer | v1.1 (LEP-0003): remaining buyer-award slots INCLUDING this recipient's possible win, `minimum: 1`; `1` when `exclusivity` is `exclusive` (schema-enforced conditional). Absent = no slot state advertised (v1.0). Non-PII count; MUST NOT be `0`. |
| `dedup_window_hours` | integer | Sender hint: how many hours to dedup against. |
| `expires_at` | string (datetime) | Optional ping expiry. |

**Capability gate (v1.1 slot fields).** A sender MUST include
`slots_available` (and likewise `slots_claimed`, `contacted_hashes`,
and `CONTACTED` reports, §4/§9) only when the receiving endpoint's
§8 capability advertisement supports v1.1 — `lcp_versions` parsed as
full `major.minor.patch` semver containing an element with
`major == 1` and `minor >= 1` — never as a string literal or prefix
match (§8). Slot fields toward a receiver advertising only v1.0 are a
conformance failure: the receiver's strict v1.0 allowlist validation
rejects them (`LCP-008`/`LCP-100`), which is the boundary the gate
exists to prevent.

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
| `capacity_remaining` | integer | Remaining capacity after this bid. Helps the platform route future pings. |
| `slots_claimed` | integer | v1.1 (LEP-0003): buyer slots this buyer claims if selected. `minimum: 1, maximum: 1` at v1.1 — single-slot awards only (multi-slot agency claims are deferred; the schema enforces the cap). Only meaningful with `decision: accept`; absent = 1. |

A slot claim is a **request, not a reservation**: the platform awards
slots at **post** time (delivery is the claim event; bids are
non-binding — losers receive no further messages). The platform MUST
NOT award more buyer slots than remain (§3 slot semantics).
`slots_claimed` is capability-gated exactly like the ping-side slot
field (§4 capability gate; §8).

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
`currency`, `buyer_id`, `buyer_reference`, optional `call` detail block,
optional `attachments[]`, `pricing{floor_price_cents,
premium_cents, final_price_cents, uncapped_price_cents,
max_allowed_price_cents, price_guardrails, is_duplicate_resub,
payable_definition, payable_status, dispute_window_hours,
dispute_window_expires_at}`, `matched_preferences`,
`consumer` (full), `location`, `compliance` (full evidence),
`attributes`, `provenance`, optional `exclusivity`, optional
`contacted_hashes`.

**`contacted_hashes` (v1.1, LEP-0003).** Array of strings, each
matching `^[a-f0-9]{64}$`, bounded by `maxItems: 100` (post direction;
the report direction has its own 32-entry ingest bound below). The
field carries the parties already contacted for this lead by the
publisher or prior slot holders — a **hash exchange**, not a data
handover: every entry is `HMAC-SHA256(pair_secret, contact-preimage)`
keyed with the **receiving buyer's per-pair secret** (§9), re-keyed by
the platform per recipient pair. A buyer matches entries against the
hashes it already holds (earlier `phone_hash`/`email_hash` values and
its suppression lists); no match data leaks because digests computed
under one pair's secret are unmatchable by any other pair. **Trust
model: format + count sanity only.** The platform cannot verify that a
reported digest matches a real contact — verification would require
every pair's secret and normalization — so entries are
authenticated-sender attestations scoped to one `lead_id`, and
disputes ride the existing ack/rejection paths. **The reference
platform hashes phone only today; email-form entries are gated** —
none ship until §9's normalization pin plus the executed dedup-hash
vectors land (#153 T9). Absent = no contact reports; `[]` = the
platform attests no prior contacts (the two are distinct). Capability-
gated like the slot fields (§8).

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
EXPIRED|CONVERTED|ARCHIVED; v1.1 adds `FINAL_PRICING` and `CONTACTED`,
both documented below), `timestamp`, `details`, `external_reference`.

**Class rule — no consumer PII.** Event `details` MUST NOT carry consumer
PII — contact values travel only as per-pair keyed digests where a message
defines them. This holds for every event name and both reporting
directions (publisher and buyer). `details` is
`additionalProperties: true` by design, so this is a receiver ingest
rule, not a schema rule: a receiver MUST reject a plaintext contact value
anywhere in `details` (a phone-number-shaped run of 7-15 digits, or an
email address) as a validation rejection (`LCP-100`), not a store.
`LCP-008 PII_IN_PING` stays the ping-allowlist code; the event class has
no allowlist to violate.

For `CONVERTED` events, `details` SHOULD include:

| Field | Type | Notes |
|---|---|---|
| `conversion_type` | string | e.g. `loan_settled`, `policy_issued`, `appointment_booked`. |
| `conversion_value_cents` | integer | Deal/loan/policy value in cents. |
| `conversion_currency` | string | ISO 4217. |
| `converted_at` | string (datetime) | When the conversion occurred. |
| `buyer_reference` | string | Buyer's CRM deal/opp ID. |

For call lifecycle events such as `CALL_OFFERED`, `CALL_CONNECTED`,
`CALL_ENDED`, and `CALL_OUTCOME`, `details` SHOULD include `offer_id`,
`call_status`, `total_seconds`, `disposition`, and `transfer_status` where
available. A platform uses the buyer offer's `payable_rules` to classify the
outcome and update the monthly payable quota.

For `DISPUTED` events, `details` SHOULD include:

| Field | Type | Notes |
|---|---|---|
| `dispute_reason` | string | e.g. `invalid_phone`, `duplicate`, `out_of_scope`, `already_customer`. |
| `dispute_evidence_url` | string (uri) | Optional evidence (recording, screenshot). |

These are recommended shapes — `details` is `additionalProperties: true`,
so platforms can add custom fields without schema changes.

For `FINAL_PRICING` events (v1.1, LEP-0006 Track A — a publisher reports
what a ping actually sold for, including sales closed outside this
platform's auction), `details` SHOULD include:

| Field | Type | Notes |
|---|---|---|
| `ping_id` | string | The ping this report describes, as the platform issued it. Optional: a publisher that lost the auction may still know the winning external price. |
| `sale_scope` | string | Open enum, known values `external` (sold outside this platform's auction) and `internal_auction` (sold through it). Unknown values stored and passed through (§6). |
| `winning_price_cents` | integer | Winning price in integer minor units of `currency`. MUST be `> 0` and MUST NOT exceed `100000000` (typo guard: 1,000,000 major units); larger values are malformed reports. Float major units are never valid. |
| `currency` | string | ISO 4217 alpha-3, uppercase. |
| `duration_seconds` | integer | Contact duration of the winning sale when call-based; `0` for form sales. MUST be `>= 0` and `<= 86400`. |
| `sold_at` | string (date-time) | When the winning sale occurred. |

Deployment-specific additions ride **namespaced keys inside `details`**
under the registered `{org}.marketplace.final_pricing` namespace (§6,
[governance/EXTENSION-REGISTRY.md](governance/EXTENSION-REGISTRY.md)); the
event's `external_reference` MAY carry the external network's sale record
id. Malformed or out-of-bound reports are validation rejections
(`LCP-100`), not stores.

**Settlement neutrality (normative).** `post.pricing` (§4) remains the
sole billing and dispute record between platform and buyer. A
`FINAL_PRICING` report MUST NOT change any payable, refund a charged
price, adjust `post.pricing` fields, re-price an in-flight auction, or
assert a lifecycle transition — it MUST NOT move `status`. Receivers
consume it for **analytics and bid refinement only**. Values are
authenticated-sender attestations (format and bounds sanity only: the
platform cannot verify what a lead sold for on another network), scoped
to one `lead_id`; a report contradicting the platform's own auction
outcome is not an error, it is the point. Disputes about a report ride
the existing ack/rejection and dispute paths, not a new one. Replay
collapses on the event `message.id` (same bytes → `DUPLICATE`; reused id
with different bytes → `LCP-005`), and a receiver SHOULD treat a later
report for the same `(lead_id, ping_id)` with a newer payload `timestamp`
as superseding the earlier one for analytics.

**Capability gate (§8).** A publisher MUST send `FINAL_PRICING` only to a
platform whose `capabilities().events` advertisement lists it and whose
`lcp_versions` contains an element with `major == 1` and `minor >= 1`
(parsed full semver, never a string prefix). A platform that accepts
`FINAL_PRICING` MUST list it in that advertisement.

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

For `CONTACTED` events (a slot holder attempted contact with a party
on the lead; v1.1, LEP-0003 §4), `details` SHOULD include
`contacted_hashes` (same per-pair keyed rule as post §4),
`contacted_at` (datetime), and `via` (`phone` | `email` | `sms` |
`chat`; open enum). Buyers SHOULD report every first contact attempt
on purchased leads. The reporting direction is capability-gated like
the slot fields (§8). Normative ingest behavior (`event.details` stays
open by design, so this is a receiver rule, not a schema rule):

- Receivers MUST reject `CONTACTED` reports whose
  `details.contacted_hashes` contains an entry failing
  `^[a-f0-9]{64}$` or exceeds **32 entries per report**, and MUST
  treat oversized or malformed reports as a validation rejection, not
  a store.
- Beyond shape and count the trust model is explicit: keyed HMACs are
  unverifiable-by-design for the receiver (verification would require
  the pair's plaintext and normalization), so entries are
  authenticated-sender attestations scoped to one `lead_id`.
- Contact reports are **at-least-once**: exact re-sends of the same
  event `message.id` collapse receiver-side; retried reports with new
  ids are merged by digest set (the contacted set per lead accumulates
  as a set union; the wire array carries no `uniqueItems`, duplicates
  are tolerated but meaningless).
- The absent-vs-`[]` rule carries to the report direction: absent =
  nothing to report (don't send); `[]` MUST NOT be sent as an event
  detail (it asserts nothing).
- A platform that accepts `CONTACTED` reports MUST list `CONTACTED`
  in its §8 capability `events` advertisement — buyers gate the
  reporting direction on that surface.

The platform stores reported digests and re-keys them onto subsequent
posts **only from plaintext it holds** (the lead values it delivered);
a received digest cannot be re-keyed (re-HMAC of a report digest is
not the target pair's digest; §9).

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
| LCP-013 | TEST_TRAFFIC_POLICY | 400 |
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
defined in §9. `LCP-013 TEST_TRAFFIC_POLICY` is used by the HTTP
reference profile when the `test` envelope field and `X-LCP-Test` header
disagree, when a sandbox receives non-test traffic, or when a production
endpoint receives test traffic.

## 6. Versioning & extensibility

- Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes).
- Per-schema versions (`vertical_schemas.version`).
- Deprecation: N+2, 2-year grace, migration guidance.
- Extensions: `extensions` object, namespaced `{org}.{division}.{purpose}`,
  registered in [governance/EXTENSION-REGISTRY.md](governance/EXTENSION-REGISTRY.md).
- Binary evidence is out-of-band. `attachments[]` carries validated metadata
  and an opaque, authenticated `storage_ref`; raw file bytes MUST NOT be put
  in an LCP JSON envelope or ping.
- Unknown optional fields ignored; unknown message types → structured error.
  The ignore-unknown rule covers **open** objects (`event.details`,
  vertical `attributes` extensions); additions to **closed** objects
  (`additionalProperties: false` — ping, bid, post, lead, ack, core
  `$defs/exclusivity`) are not ignorable by a schema-validating receiver,
  so they rely on version negotiation: senders gate v1.1 fields on the
  receiver's §8 capability advertisement as specified in §4/§8.

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

**Version-string membership (v1.1, LEP-0003 F6, normative).** Every
`lcp_versions` element MUST be a full `major.minor.patch` semantic
version (`^\d+\.\d+\.\d+$`). A peer supports protocol extensions of
minor `m` when its advertised set contains an element with
`major == 1` and `minor >= m`; senders MUST compare parsed versions,
never string literals or prefixes (exact membership of `1.1` is
unsatisfiable by the full-semver forms the same section mandates, and
prefix matching on `1.1` wrongly matches `1.10.0`). Every endpoint
that initiates traffic to a counterparty (ping senders included) MUST
host `GET /v1/lcp/capabilities` for that counterparty. Consequences:
v1.1 slot fields (`slots_available`, `slots_claimed`,
`contacted_hashes`) and `CONTACTED` reports are gated on a receiver's
set containing a v1.1-or-higher minor element (e.g. `1.1.0`); a sender
MUST NOT send those fields to a receiver advertising v1.0 minors only,
because the receiver's closed-object allowlist rejects them (§4
capability gate, §6 closed-vs-open rule).

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
characteristics the buyer will not accept. The standardized discovery shape
is defined in [schemas/offer.json](schemas/offer.json). Offer creation,
updates, credentials, and buyer administration remain deployment-specific.
The `routing_mode` value is `auction` (ping/bid/post) or `direct` (the
platform sends a post at the offer floor price without bidding).

The platform filters against offer restrictions before pinging:

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
| `allowed_publisher_ids` | array<string> | Optional publisher sender-ID allowlist. |
| `allowed_brand_ids` | array<string> | Optional `provenance.brand_id` allowlist. |
| `attribute_equals` / `attribute_in` | object | Safe declarative vertical criteria; values are data, never executable expressions. |
| `monthly_minimum_payable` | integer | Operational monthly payable target; not a supply guarantee. |
| `monthly_maximum_payable` | integer | Optional hard ceiling for payable outcomes. |
| `monthly_quota_policy` | string | `monitor`, `pace`, or `hard_cap`. |
| `payable_rules` | object | Structured call answer, duration, and disposition rules. |
| `call_routing_mode` | string | `post_call` or `realtime_transfer`; live transfer remains telephony-binding work. |

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
> push delivery) are **not** part of v1.1. LEP-0001 is Deferred until its
> trigger is met — independent publishers need bulk intake, backfills, or
> materially lower request overhead, or polling at volume becomes costly —
> and revival requires a revised record with adopter evidence and
> vocabulary harmonisation with the shipped `capabilities().events`
> surface (see [LEP-REGISTRY.md](governance/LEP-REGISTRY.md)). The current
> single-item, pull-based tools remain the supported agent surface.

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
- [x] Write conformance runner (L1/L2/L3) — 30/30 pass, including the post-release sensitive-ping vectors.
- [x] Write reference MCP server (`implementations/mcp-server/`).
- [x] Add `governance/SECURITY.md` (responsible disclosure).
- [x] Add trademark/usage policy for "LCP compliant" claims.
- [x] Fill extension registry format (namespace, registration, payload location).
- [x] Write CLA full text (`governance/CLA.md`).
- [x] Publish decision — repository is public under Apache 2.0 (see [RELEASE.md](docs/RELEASE.md) for the tagged release record).
- [ ] Spec site (LEX-style) — trigger-gated: publish only once a maintained
  canonical-source workflow exists (see §14); not part of v1.1.

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

### Should-fix — open (trigger-gated, unscheduled)

v1.1.0 delivered the LEP-driven set (canonical timestamps and cross-SDK
interop, shared-lead slots, request history, extensions and
`FINAL_PRICING`, attestation verification). The findings below remain open
opportunities gated on the trigger in each row; none is a commitment to
v1.1 or v1.2.

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
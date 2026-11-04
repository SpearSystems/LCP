# LCP — Lead Context Protocol Specification

> Part of the normative LCP v1.0 specification: **Payloads and lifecycle**.
> Canonical core payload blocks, status transitions, lead expiry, exclusivity slots, and the consent-evidence appendix.
> Index: [SPEC.md](../SPEC.md) · File map: [spec/README.md](README.md).

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

Vertical-specific, JSON Schema per vertical (see [verticals/](../verticals/)).
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

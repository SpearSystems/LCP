# LCP Field Dictionary

> **Developer page · Page 3 of 6**
>
> **Status: v1.0 protocol; current implementation release v1.0.1.** This is a navigational field reference for implementers.
> The JSON Schemas in [`schemas/`](../schemas/) are normative for types,
> requiredness, formats, limits, and `additionalProperties` behavior. If this
> page and a schema disagree, the schema wins and this page should be corrected.

## How to read this dictionary

Field paths are relative to the JSON document unless stated otherwise. `req`
means required in that object; `conditional` means required only when the
containing message or rule requires it. `ping-safe` describes whether a value
may appear in a `ping`; the strict ping schema and the vertical schema's
`ping_safe` annotations remain the enforcement mechanism.

| Mark | Meaning |
|---|---|
| `req` | Required by the containing schema. |
| `optional` | May be omitted unless a deployment contract says otherwise. |
| `conditional` | Required by an `anyOf`, message type, or state-dependent rule. |
| `PII` | Personal or sensitive data; send only in an authorized `post` or other agreed PII flow. |
| `non-PII` | Designed for routing, deduplication, audit, or capability discovery; still protect operational metadata. |
| `vertical` | Defined by a vertical schema, not by the universal core. |

## 1. Envelope and message header

Every LCP message is wrapped in `lcp`.

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `lcp` | object | `req` | Transport-neutral protocol envelope. |
| `lcp.version` | string, SemVer | `req` | Protocol version, for example `1.0.0`. |
| `lcp.message` | object | `req` | Header identifying and routing the message. |
| `lcp.message.id` | UUID v4 | `req` | Unique message identifier. |
| `lcp.message.type` | closed string enum | `req` | `lead`, `call`, `ping`, `post`, `ack`, `event`, or `bid`. Unknown values trigger `LCP-006`. |
| `lcp.message.timestamp` | RFC 3339 date-time | `req` | Time at which the message was created. |
| `lcp.message.sender_id` | string | `req` | Publisher, platform, buyer, or agent identifier. |
| `lcp.message.receiver_id` | string | `req` | Intended recipient identifier. |
| `lcp.message.correlation_id` | string or null | optional | Correlates a response with an earlier message, such as a bid with a ping. |
| `lcp.message.idempotency_key` | string | `req` | Stable key used to make retries safe and return the original result. |
| `lcp.message.test` | boolean | optional | Test traffic indicator; defaults to false and mirrors `X-LCP-Test` over HTTP. |
| `lcp.message.security` | object | conditional | Transport-neutral HMAC security block; required for non-HTTP transports and optional when HTTP headers carry the binding. |
| `lcp.message.security.signature` | string | conditional | HMAC signature over the canonical message input. |
| `lcp.message.security.timestamp` | RFC 3339 date-time | conditional | Security timestamp used for freshness and replay protection. |
| `lcp.message.security.algorithm` | constant `HMAC-SHA256` | conditional | Algorithm identifier. |
| `lcp.payload` | object | `req` | Payload whose schema is selected by `lcp.message.type`. |

## 2. Universal core definitions

### 2.1 Consumer

`consumer` identifies the person the lead is about. At least one of email,
phone, first/last name, or full name is required by the schema.

| Path | Type / format | Presence | Privacy / meaning |
|---|---|---|---|
| `consumer.first_name` | string | conditional | `PII`; given name where a split name is appropriate. |
| `consumer.last_name` | string | conditional | `PII`; family name where a split name is appropriate. |
| `consumer.full_name` | string | conditional | `PII`; unstructured name for names that should not be split. |
| `consumer.email` | email string | conditional | `PII`; consumer email address. |
| `consumer.phone` | E.164 string | conditional | `PII`; international phone number beginning with `+`. |
| `consumer.secondary_phone` | E.164 string | optional | `PII`; alternate phone number. |
| `consumer.preferred_contact_method` | `phone` / `email` / `sms` / `any` | optional | Contact strategy hint; does not override consent. |
| `consumer.phone_hash` | 64-char lowercase hex | optional | `non-PII`; per-pair HMAC-SHA256 of normalized E.164. Not a global cross-buyer identifier. |
| `consumer.email_hash` | 64-char lowercase hex | optional | `non-PII`; per-pair HMAC-SHA256 of normalized email for deduplication. |
| `consumer.dob` | ISO date | optional | `PII` and sensitive; include only in an authorized full-data flow. |
| `consumer.locale` | BCP-47 string | optional | Language/locale hint such as `en-AU` or `es-US`. |

### 2.2 Location

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `location.country_code` | ISO 3166-1 alpha-2 | `req` | Country context, uppercase two-letter code. |
| `location.state_region` | string | conditional | State, province, or region in the country's validation system. |
| `location.postal_code` | string | conditional | Country-specific postal code. |
| `location.city` | string | optional | City or locality. |
| `location.timezone` | IANA timezone | optional | Timezone such as `Australia/Sydney`. |

At least `state_region` or `postal_code` is required. Street addresses are not
universal core fields; use an approved vertical or extension where permitted.

### 2.3 Compliance

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `compliance.consent_timestamp` | RFC 3339 date-time | optional | Time consent was captured. |
| `compliance.consent_source_url` | URI | optional | Source page or capture reference. |
| `compliance.consent_text_version` | string | optional | Version of the displayed consent language. |
| `compliance.consent_purposes[]` | string array | optional | Purposes actually consented to, such as calls, SMS, email, or sharing. |
| `compliance.consent_evidence[]` | object array | optional | Generic evidence records; do not add vendor-specific fields to the core. |
| `compliance.otp_verified` | boolean | optional | Whether an OTP flow verified the contact value. |
| `compliance.session_capture_id` | string | optional | Reference to a consent/session capture record. Treat as sensitive. |
| `compliance.call_recording_id` | string | optional | Reference to call evidence. Treat as sensitive. |
| `compliance.dnc_checked` | boolean | optional | Whether a do-not-call check was performed. |
| `compliance.consent_expires_at` | RFC 3339 date-time | optional | Time after which the recorded consent is no longer valid. |
| `compliance.scrubs[]` | object array | optional | DNC, litigator, blacklist, or other screening results. |

Each `consent_evidence` entry contains:

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `consent_evidence[].type` | string | `req` | Framework or evidence type, such as `tcpa_consent` or `gdpr_opt_in`. |
| `consent_evidence[].provider` | string | `req` | Vendor, publisher, or internal system. |
| `consent_evidence[].token_or_url` | string | `req` | Verification token, certificate URL, or opaque reference. |

An OTP block contains `channel` (`sms`, `email`, or `voice`), `verified_at`,
`verified_value_hash`, and positive integer `attempts`. An OTP code itself must
never be placed in an LCP message. A scrub entry contains `type`, optional
`provider`, `result` (`clean`, `flagged`, `blocked`, or `not_checked`), optional
`checked_at`, and optional `details`.

### 2.4 Provenance

Provenance describes origin without turning the core into a vendor-specific
market taxonomy. Values such as `source_type`, `acquisition_method`, and
`platform_source` are open enumerations.

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `provenance.received_at` | RFC 3339 date-time | optional | When a platform received the message. |
| `provenance.source_type` | open string | optional | Origin class, such as publisher, platform, or agent. |
| `provenance.acquisition_method` | open string | optional | Acquisition method, such as paid, organic, marketplace, or referral. |
| `provenance.is_incentivized` | boolean | optional | Whether an incentive was offered. |
| `provenance.incentive_type` | string | optional | Incentive category. |
| `provenance.source_id` | string | optional | Publisher/source identifier. |
| `provenance.source_url` | URI | optional | Source reference, subject to deployment privacy controls. |
| `provenance.ip_hash` | string | optional | Hashed IP signal; never send a raw IP in a ping. |
| `provenance.user_agent_hash` | string | optional | Hashed user-agent signal. |
| `provenance.funnel_key` | string | optional | Stable publisher funnel identifier. |
| `provenance.flow_key` | string | optional | Stable source-flow identifier. |
| `provenance.campaign_id` | string | optional | Campaign attribution identifier. |
| `provenance.creative_id` | string | optional | Creative attribution identifier. |
| `provenance.landing_page_url` | URI | optional | Landing-page reference. |
| `provenance.platform_source` | string | optional | Source platform attribution. |
| `provenance.brand_id` | string | optional | Publisher-owned brand identifier. |
| `provenance.form_id` | string | optional | Stable form or flow identifier. |
| `provenance.agent` | object | optional | Agent provenance when `channel` or `source_type` is agent. |

An agent block requires `agent_id`, `acting_for`, and a signed `attestation` JWT.
The issuer and verification key are deployment decisions; unknown issuers are
untrusted.

### 2.5 Shared routing blocks

| Block / path | Type / format | Presence | Meaning |
|---|---|---|---|
| `attributes.vertical` | string | `req` in attributes | Vertical schema identifier. |
| `attributes.schema_version` | string | `req` in attributes | Version of the selected vertical schema. |
| `status` | open string | message-dependent | Lifecycle status; unknown values are passed through. |
| `channel` | open string | message-dependent | Lead channel; `call` messages use the constant `call`. |
| `currency` | three uppercase letters | message-dependent | ISO 4217 currency code. |
| `exclusivity.exclusivity` | `exclusive` or `shared` | optional | Commercial sharing hint. |
| `exclusivity.max_buyers` | positive integer | optional | Maximum intended buyers when sharing. |
| `expiry.expires_at` | RFC 3339 date-time | conditional | Absolute expiry time. |
| `expiry.ttl_seconds` | positive integer | conditional | Relative time-to-live. |

### 2.6 Attachments

Binary files are never embedded in an LCP JSON envelope. An attachment entry
contains `attachment_id`, `purpose`, `filename`, `content_type`, `size_bytes`,
`sha256`, `storage_ref`, and `created_at`; `expires_at`, `encryption`,
`residency`, and `malware_scan` are optional. `size_bytes` is limited to 100 MiB
by the schema, and filenames may not contain path separators.

The optional `malware_scan` block contains `status` (`clean` or `not_scanned`),
`engine`, and `scanned_at`. Production delivery requires a clean scan under the
reference platform's fail-closed attachment policy.

## 3. Message payloads

### 3.1 `lead`

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `lead.lead_id` | string | `req` | Lead identifier. |
| `lead.external_id` | string | optional | Publisher's internal identifier. |
| `lead.submitted_at` | RFC 3339 date-time | optional | Consumer submission time, distinct from `provenance.received_at`. |
| `lead.status` | open string | `req` | Lifecycle status. |
| `lead.channel` | open string | `req` | Form, chat, click, API, agent, referral, or another open channel. |
| `lead.consumer` | consumer object | `req` | Consumer data subject. |
| `lead.location` | location object | `req` | Country and geographic context. |
| `lead.compliance` | compliance object | optional | Consent and screening evidence. |
| `lead.provenance` | provenance object | optional | Origin metadata. |
| `lead.attributes` | vertical object | `req` | Vertical-specific data. |
| `lead.exclusivity` | routing block | optional | Shared or exclusive delivery hint. |
| `lead.contact_window` | object | optional | Consumer availability timezone, local time range, and days. |
| `lead.lead_quality` | object | optional | Publisher-declared verification, duplicate, spam, and completeness signals. |
| `lead.expiry` | expiry object | optional | Lead expiry. |
| `lead.attachments[]` | attachment array | optional | Out-of-band evidence references. |

`lead.lead_quality.verification` can contain phone/email method, verified time,
and verifying provider. Quality values are publisher declarations, not an LCP
certification.

### 3.2 `call`

A call payload uses the lead core plus a required `call` detail block.

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `call.lead_id` | string | `req` | Lead identifier for the call. |
| `call.external_id` | string | optional | Publisher's call/lead identifier. |
| `call.submitted_at` | RFC 3339 date-time | optional | Call initiation time. |
| `call.status` | open string | `req` | Lifecycle status. |
| `call.channel` | constant `call` | `req` | Distinguishes telephony from form-based intake. |
| `call.consumer` | consumer object | `req` | Caller data where available. |
| `call.location` | location object | `req` | Country and geographic context. |
| `call.compliance` | compliance object | optional | Consent and screening evidence. |
| `call.provenance` | provenance object | optional | Origin metadata. |
| `call.attributes` | vertical object | optional | Vertical-specific data. |
| `call.expiry` | expiry object | optional | Call lead expiry. |
| `call.exclusivity` | routing block | optional | Shared or exclusive delivery hint. |
| `call.attachments[]` | attachment array | optional | Call evidence references. |
| `call.call` | call detail object | `req` | Telephony facts and outcomes. |

The call detail block requires `call_id`, `status`, and `started_at`. It may also
contain `hangup_cause`, `direction`, `did`, `forwarded_to`, `tracking_number`,
`caller_phone_hash`, `durations`, `recording`, `ivr`, `disposition`,
`transferred_from`, `queue`, and `agent`. Durations are non-negative seconds;
`direction` is `inbound` or `outbound`; IVR details may include selected language,
path, digits, abandonment, and menu options.

### 3.3 `ping`

A ping is a strict non-PII allowlist for buyer discovery and bidding.

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `ping.ping_id` | string | `req` | Ping identifier. |
| `ping.lead_reference` | string | `req` | Opaque reference, not the full lead ID. |
| `ping.publisher_id` | string | optional | Publisher routing/quality identifier. |
| `ping.offer_id` | string | optional | Buyer offer being evaluated. |
| `ping.phone_hash` | 64-char lowercase hex | `req` | Per-pair phone HMAC. |
| `ping.email_hash` | 64-char lowercase hex | optional | Per-pair email HMAC. |
| `ping.country_code` | ISO 3166-1 alpha-2 | `req` | Country. |
| `ping.state_region` | string | optional | Coarse region. |
| `ping.postal_code` | string | optional | Postal code where allowed by the deployment. |
| `ping.vertical` | string | `req` | Vertical identifier. |
| `ping.lead_age_minutes` | non-negative integer | optional | Freshness signal. |
| `ping.attributes` | object | optional | Only vertical fields tagged `ping_safe: true`; values should be banded/aggregated. |
| `ping.compliance_flags` | object | optional | Presence indicators only: consent, OTP, DNC, and consent validity. |
| `ping.quality_flags` | object | optional | Non-PII publisher-declared quality signals. |
| `ping.floor_price_cents` | non-negative integer | `req` | Minimum bid price in minor currency units. |
| `ping.currency` | ISO 4217 | `req` | Bid currency. |
| `ping.exclusivity` | `exclusive` or `shared` | optional | Commercial sharing hint. |
| `ping.dedup_window_hours` | positive integer | optional | Sender deduplication hint. |
| `ping.expires_at` | RFC 3339 date-time | optional | Ping expiry. |

The ping schema has `additionalProperties: false`. A vertical's `ping_safe`
annotations and the conformance runner provide the second safety check; a full
name, email, phone, address, consent token, recording, or other PII must not be
smuggled through `attributes`.

### 3.4 `post`

A post is full lead delivery to the winning buyer.

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `post.lead_id` | string | `req` | Lead identifier. |
| `post.delivered_at` | RFC 3339 date-time | `req` | Delivery time. |
| `post.submitted_at` | RFC 3339 date-time | optional | Original submission time. |
| `post.offer_id` | string | optional | Matched buyer offer. |
| `post.price_cents` | non-negative integer | `req` | Delivery price in minor currency units. |
| `post.currency` | ISO 4217 | `req` | Price currency. |
| `post.buyer_id` | string | `req` | Winning buyer. |
| `post.buyer_reference` | string | optional | Buyer's internal reference. |
| `post.pricing` | object | optional | Price, guardrails, payable, and dispute information. |
| `post.matched_preferences` | object | optional | Buyer criteria that matched; deployment-defined data. |
| `post.consumer` | consumer object | `req` | Full authorized consumer data. |
| `post.location` | location object | `req` | Full authorized location data. |
| `post.compliance` | compliance object | optional | Consent and screening evidence. |
| `post.attributes` | vertical object | `req` | Vertical-specific data. |
| `post.call` | call detail object | optional | Call facts when the post represents a call. |
| `post.attachments[]` | attachment array | optional | Evidence references. |
| `post.provenance` | provenance object | optional | Origin metadata. |
| `post.exclusivity` | routing block | optional | Sharing hint. |

`post.pricing` may contain floor, premium, final, uncapped, and maximum allowed
prices; `price_guardrails`; `is_duplicate_resub`; `payable_definition`;
`payable_status` (`pending`, `payable`, `not_payable`, or `disputed`);
and dispute-window fields. The exact payable definition remains bilateral.

### 3.5 `ack`

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `ack.original_message_id` | UUID | `req` | Message being acknowledged. |
| `ack.status` | closed string enum | `req` | `RECEIVED`, `VALIDATED`, `ACCEPTED`, `REJECTED`, `DUPLICATE`, or `ERROR`. |
| `ack.errors[]` | error array | optional | All validation or processing errors; do not collapse multiple errors into one. |
| `ack.lead_id` | string | optional | Assigned lead identifier. |
| `ack.request_id` | string | optional | Server-side trace identifier. |
| `ack.rejection_reason` | open string | optional | Structured rejection reason such as `invalid_phone`, `duplicate`, or `capacity_exceeded`. |

Each error contains required `code` (`LCP-###`) and `message`, with optional
`name`, `field`, and object `details`.

### 3.6 `event`

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `event.lead_id` | string | `req` | Affected lead. |
| `event.event` | open string | `req` | Lifecycle event such as `DELIVERED`, `ACCEPTED`, `REJECTED`, `DISPUTED`, `REFUNDED`, `EXPIRED`, `CONVERTED`, or `ARCHIVED`. |
| `event.timestamp` | RFC 3339 date-time | `req` | Event time. |
| `event.details` | object | optional | Event-specific non-normative details. |
| `event.external_reference` | string | optional | Partner or system reference. |

Unknown event values must be stored and passed through.

### 3.7 `bid`

| Path | Type / format | Presence | Meaning |
|---|---|---|---|
| `bid.ping_id` | string | `req` | Ping being answered. |
| `bid.decision` | `accept`, `reject`, or `pass` | `req` | Buyer's decision. |
| `bid.bid_price_cents` | non-negative integer | `req` | Bid in minor currency units; used when accepting. |
| `bid.currency` | ISO 4217 | `req` | Bid currency. |
| `bid.estimated_contact_seconds` | non-negative integer | optional | Expected time to contact. |
| `bid.buyer_reference` | string | optional | Buyer's internal bid reference. |
| `bid.reject_reason` | open string | optional | Reason for rejecting or passing. |
| `bid.capacity_remaining` | non-negative integer | optional | Buyer's remaining capacity. |

### 3.8 `offer`

Offers describe buyer acceptance and routing criteria. Offer administration is
deployment-specific; this is the interoperable discovery shape.

| Field group | Fields | Meaning |
|---|---|---|
| Identity | `offer_id`, `buyer_id`, `tenant_id` | Offer and ownership identifiers. |
| Activation | `active`, `routing_mode` | Whether active; `auction` or `direct` routing. |
| Scope | `vertical`, `schema_version`, `countries`, `state_regions`, `postal_codes`, `channels` | Geographic, vertical, and channel constraints. |
| Source filters | `excluded_source_types`, `excluded_acquisition_methods`, `allowed_publisher_ids`, `allowed_brand_ids` | Sender and acquisition allow/deny criteria. |
| Incentives | `reject_incentivized`, `excluded_incentive_types` | Incentive policy. |
| Quality | `require_verified_phone`, `require_verified_email`, `max_spam_risk_score`, `min_data_completeness`, `excluded_claim_language` | Minimum quality and claim requirements. |
| Compliance | `require_consent_evidence`, `reject_dnc_flagged`, `reject_litigator_flagged`, `reject_blacklist_flagged` | Compliance acceptance criteria. |
| Pricing | `floor_price_cents`, `currency`, `payable_definition` | Commercial floor and payable basis. |
| Capacity | `daily_cap`, `hourly_cap`, `monthly_minimum_payable`, `monthly_maximum_payable`, `monthly_quota_timezone`, `monthly_quota_policy` | Pacing and capacity controls. |
| Matching | `attribute_equals`, `attribute_in`, `extensions` | Safe declarative predicates and namespaced extensions; values are never executable code. |
| Calls | `call_routing_mode`, `connect_timeout_seconds`, `payable_rules` | Post-call or real-time transfer criteria. |
| Delivery | `delivery_windows`, `ping_timeout_seconds`, `webhook_url` | When and where delivery occurs. |

Offer `delivery_windows` contain `timezone`, `available_from`, `available_to`,
`days`, and optional vertical/channel filters. `payable_rules` can specify
`mode`, call-answer requirements, minimum call seconds, allowed dispositions,
and duplicate-resubmission handling.

## 4. Vertical attributes and extensions

Everything vertical-specific belongs under `attributes` and is typed by the
selected file in [`verticals/`](../verticals/). Current vertical schemas include
mortgage, insurance, solar, legal, home services, and motor-vehicle accident.
Every vertical field is explicitly tagged `ping_safe: true` or `false`, and
verticals must not redefine reserved universal core names such as `phone`,
`email`, `first_name`, `last_name`, `full_name`, or `country_code`.

Deployment-specific extensions use a registered namespace of the form
`{org}.{division}.{purpose}` and are recorded in the
[extension registry](../governance/EXTENSION-REGISTRY.md). An extension must not
silently alter the meaning or privacy guarantees of a v1.0 core field.

## 5. Transport and privacy reminders

- JSON Schemas define payload shape; the [OpenAPI 3.1 binding](../api/lcp-openapi.yaml)
  defines HTTP paths, headers, and transport behavior.
- HMAC signing covers the canonical raw request input described in
  [implementation decisions](IMPLEMENTATION-DECISIONS.md).
- HTTP messages use `X-LCP-Timestamp`, `X-LCP-Idempotency-Key`,
  `X-LCP-Signature`, and `X-LCP-Test` as specified by the HTTP binding.
- Pings contain hashes and permitted non-PII signals only. Full consumer PII is
  sent only in an authorized post/direct flow.
- Unknown optional fields are ignored; unknown `message.type` values are a
  structured error; open status/channel/event enumerations are passed through.

## 6. Normative sources

- [Canonical specification](../SPEC.md)
- [Envelope schema](../schemas/envelope.json)
- [Core definitions](../schemas/core.json)
- [Message schemas](../schemas/)
- [Vertical schemas](../verticals/)
- [Conformance vectors](../test-vectors/)

This page is intentionally human-edited for navigation. Generated SDK models,
the schema bundle, and the SHA-256 schema manifest are kept synchronized by the
repository's SDK checks.

---

**Previous:** [SDK program](SDK-ROADMAP.md) · **Next:** [Tagged releases and artifact verification](RELEASE.md)

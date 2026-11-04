# LCP — Lead Context Protocol Specification

> Part of the normative LCP v1.0 specification: **Envelope and message types**.
> The transport-agnostic envelope and the seven message types (lead, call, ping, bid, post, ack, event).
> Index: [SPEC.md](../SPEC.md) · File map: [spec/README.md](README.md).

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

Schema: [schemas/envelope.json](../schemas/envelope.json)

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
[governance/EXTENSION-REGISTRY.md](../governance/EXTENSION-REGISTRY.md)); the
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

# LCP Platform Integration Guide
> **Integration page · Page 2 of 6**

LCP is a **wire format** for exchanging lead data between parties. It
does not replace platform APIs — it sits between them. Each platform
needs a thin adapter that maps its data shape to/from LCP messages.

This document shows how common platforms map to LCP fields.

## Architecture

```
Capture platform          LCP wire                  Receiving platform
─────────────────         ─────────                 ──────────────────
Facebook Lead Ads    →    LCP lead message    →    Buyer CRM (HubSpot/Salesforce)
Web form (publisher) →    LCP lead message    →    Platform → ping/post → Buyer
Twilio (inbound call) →   LCP call message    →    Platform → ping/post → Buyer
                                               →    Buyer's dialer (Twilio outbound)
```

The publisher writes the **source adapter** (capture → LCP).
The buyer writes the **sink adapter** (LCP → CRM/dialer).
Each is typically 50-100 lines of code.

## Capability advertisement (v1.2)

Before writing an adapter, read `GET /v1/lcp/capabilities` and gate on what the
receiver actually advertises. A v1.2 surface is add-only and capability-gated:
if the peer does not advertise it, sending it is a protocol error, not a
forward-compatible nicety. The bundled reference platform serves the route
without authentication and builds the response in
`implementations/reference-platform/lcp_platform/router.py:capabilities`:

| Key | Type | What an adapter does with it |
|---|---|---|
| `lcp_versions` | array of full `major.minor.patch` strings | send a v1.2 minor surface only when the set contains `major == 1` with `minor >= 2`; compare parsed versions, never string prefixes |
| `core_schema_version` | string | the core schema version this receiver validates against, resolved from the build's `SDK_VERSION` (D3). Read it and compare it with the release you target; it is an advertisement, not a new envelope field, and not something to hardcode |
| `features` | object: surface name → minimum version | which additive v1.2 surfaces this deployment implements (table below) |
| `message_types` | array | the types accepted here; `batch`, `quote`, `counter`, and `subscription` are listed only when their switch is on |

`features` on the current build:

| Key | Present when | Surface it names |
|---|---|---|
| `processed_ack` | always in this build (no switch gates it) | `ack.status = "PROCESSED"` for a `post` the buyer received (D2) |
| `multi_currency` | always in this build (no switch gates it) | `post.pricing` settlement currency, original price, and FX fields; bid-side FX fields (D7) |
| `batch` | `LCP_FEATURE_BATCH` is enabled | `POST /v1/lcp/leads/batch`, 100 items / 1 MiB, `207` per-item receipts (LEP-0001 W2) |
| `negotiation` | `LCP_FEATURE_NEGOTIATION` is enabled | `quote` and `counter` on `/v1/lcp/quotes` and `/v1/lcp/counters` (LEP-0009) |
| `event_subscriptions` | `LCP_FEATURE_EVENTS` is enabled | subscription registration and replay: `POST /v1/lcp/events/subscribe`, `GET /v1/lcp/events/subscriptions`, `DELETE /v1/lcp/events/subscriptions/{id}`, and `GET /v1/lcp/events?cursor=` (LEP-0001 W3) |

The three off-by-default switches are read from the environment in
`implementations/reference-platform/lcp_platform/config.py:feature_batch` and
its negotiation and events siblings:

| Switch | Default | Off means | On means |
|---|---|---|---|
| `LCP_FEATURE_BATCH` | **off** | no `features.batch`, no `batch` in `message_types`, `404` from `/v1/lcp/leads/batch` | advertised, and batch intake live |
| `LCP_FEATURE_NEGOTIATION` | **off** | no `features.negotiation`, no `quote`/`counter` types, `404` from both negotiation routes | advertised; quote/counter accepted from a peer that advertises a `1.2.0`-or-higher minor |
| `LCP_FEATURE_EVENTS` | **off** | no `features.event_subscriptions`, no `subscription` in `message_types`, `404` from the registration, list, delete, and cursor routes | advertised; registration and signed at-least-once delivery live, with a cursor TTL of `LCP_EVENT_CURSOR_TTL_SECONDS` (default 86400 s) |

Three rules follow from that shape:

1. **Absence is the switch.** A missing `features` key means the surface does
   not exist on that deployment. Do not send a batch, quote, counter, or
   subscription to a receiver that does not advertise it, and do not treat the
   `404` as a retryable error.
2. **Advertisement is per deployment, not per protocol version.** A peer may
   advertise `1.2.0` in `lcp_versions` while omitting `batch`, because the
   operator left the switch off.
3. **`processed_ack` and `multi_currency` have no switch, but they are still
   version-gated.** Both extend **closed** objects — `ack.status` is a closed
   enum and `post.pricing` is `additionalProperties: false` — so a v1.0/1.1
   peer rejects the new wire shape rather than ignoring it. Require the
   `features` key and a `1.2.0`-or-higher member of `lcp_versions` before
   relying on either.

## Facebook Instant Lead Forms

Facebook sends lead data via the Leadgen webhook. Map to LCP `lead`:

| Facebook field | LCP field |
|---|---|
| `full_name` or `first_name` + `last_name` | `consumer.full_name` or `consumer.first_name` / `consumer.last_name` |
| `email` | `consumer.email` |
| `phone_number` | `consumer.phone` (normalize to E.164) |
| `city` / `state` / `zip` / `country` | `location.city` / `location.state_region` / `location.postal_code` / `location.country_code` |
| Custom questions | `attributes` (map to vertical schema fields) |
| `campaign_id` | `provenance.campaign_id` |
| `ad_id` | `provenance.creative_id` |
| `form_id` | `provenance.funnel_key` |
| Lead form URL | `provenance.source_url` |
| Platform | `provenance.platform_source` = `"facebook"` |
| Facebook TOS consent | `compliance.consent_evidence[]` with `{type: "platform_consent", provider: "facebook", token_or_url: form_id}` |

## Google Lead Form Extensions

Google Ads lead form extensions capture leads in Google's UI. Map to
LCP `lead`:

| Google field | LCP field |
|---|---|
| `Given Name` / `Family Name` | `consumer.first_name` / `consumer.last_name` |
| `Email` | `consumer.email` |
| `Phone Number` | `consumer.phone` (normalize to E.164) |
| `Postal Code` | `location.postal_code` |
| `Country` | `location.country_code` |
| Custom questions | `attributes` |
| `campaign_id` | `provenance.campaign_id` |
| `creative_id` | `provenance.creative_id` |
| Platform | `provenance.platform_source` = `"google_ads"` |
| Google consent | `compliance.consent_evidence[]` with `{type: "platform_consent", provider: "google"}` |

## Twilio (Inbound Calls)

Twilio sends call data via voice webhooks. Map to LCP `call`:

| Twilio field | LCP call field |
|---|---|
| `CallSid` | `call.call_id` |
| `CallStatus` | `call.status` (ringing→in-progress = answered; failed = failed; no-answer = no_answer; busy = busy) |
| `From` | `consumer.phone` (already E.164) |
| `To` | `call.did` |
| `Direction` | `call.direction` (inbound/outbound) |
| `CallDuration` | `call.durations.total_seconds` |
| `RecordingUrl` | `call.recording.url` |
| `TranscriptionUrl` | `call.recording.transcript_url` |
| `DialCallStatus` | `call.disposition` |
| IVR Gather digits | `call.ivr.digits` |
| Platform | `provenance.platform_source` = `"twilio"` |

## HubSpot (CRM Sink)

Buyer receives LCP `post` and creates a HubSpot contact:

| LCP field | HubSpot property |
|---|---|
| `consumer.first_name` | `firstname` |
| `consumer.last_name` | `lastname` |
| `consumer.full_name` | `firstname` (or custom `full_name`) |
| `consumer.email` | `email` |
| `consumer.phone` | `phone` |
| `consumer.dob` | `date_of_birth` |
| `location.city` | `city` |
| `location.state_region` | `state` |
| `location.postal_code` | `zip` |
| `location.country_code` | `country` |
| `attributes.*` | Custom properties (create matching HubSpot properties) |
| `lead_id` | Custom `lcp_lead_id` property |
| `buyer_reference` | Custom `lcp_buyer_reference` property |

## Salesforce (CRM Sink)

Buyer receives LCP `post` and creates a Salesforce Lead:

| LCP field | Salesforce Lead field |
|---|---|
| `consumer.first_name` | `FirstName` |
| `consumer.last_name` | `LastName` |
| `consumer.full_name` | `Name` (split as needed) |
| `consumer.email` | `Email` |
| `consumer.phone` | `Phone` |
| `location.city` | `City` |
| `location.state_region` | `State` |
| `location.postal_code` | `PostalCode` |
| `location.country_code` | `Country` |
| `attributes.*` | Custom fields |
| `lead_id` | Custom `LCP_Lead_ID__c` field |

## TikTok Lead Generation

TikTok Lead Gen captures leads in TikTok's UI. Map to LCP `lead`:

| TikTok field | LCP field |
|---|---|
| `full_name` / `first_name` / `last_name` | `consumer.full_name` or split |
| `email` | `consumer.email` |
| `phone_number` | `consumer.phone` (normalize to E.164) |
| `state` / `city` / `zip_code` | `location.*` |
| Custom questions | `attributes` |
| `campaign_id` | `provenance.campaign_id` |
| `ad_id` | `provenance.creative_id` |
| Platform | `provenance.platform_source` = `"tiktok"` |
| TikTok consent | `compliance.consent_evidence[]` with `{type: "platform_consent", provider: "tiktok"}` |

## Key Principles

1. **LCP is the interchange, not the platform.** Each platform maps
   to/from LCP. LCP never calls Facebook's API or HubSpot's API —
   adapters do.
2. **Core fields are universal.** Every platform has name, email, phone,
   location — these map to LCP core. Platform-specific data goes in
   `attributes` (vertical) or `extensions` (publisher-specific).
3. **Consent is structured.** Each platform's consent model maps to
   `consent_evidence[]` with a `type` and `provider`. New consent
   frameworks are additive — no core changes.
4. **Provenance carries attribution.** `platform_source` identifies the
   ad platform; `campaign_id`/`creative_id` carry the campaign data.
   Richer ad data goes in extensions.
5. **Adapters are thin.** Each adapter is ~50-100 lines of mapping code.
   No SDK is required — LCP is JSON over HTTP.
6. **Messy multi-brand sources use the mapping registry.** See
   [publisher mapping](PUBLISHER-MAPPING.md) for versioned normalization,
   OTP variation, and audit behavior.
7. **Calls and evidence have dedicated paths.** See
   [calls and telephony](CALLS-AND-TELEPHONY.md) and
   [secure attachments](ATTACHMENTS.md) for real-time transfer boundaries
   and secure out-of-band files.

---

**Previous:** [Buyer onboarding](BUYER-ONBOARDING.md) · **Next:** [SDK program](SDK-ROADMAP.md)
# LCP Platform Integration Guide

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
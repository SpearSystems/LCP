# LCP — Lead Context Protocol Specification

> Part of the normative LCP v1.0 specification: **Capabilities and markets**.
> Versioning and extensibility policy, capability discovery, capacity, offer restrictions, and country/market discovery.
> Index: [SPEC.md](../SPEC.md) · File map: [spec/README.md](README.md).

## 6. Versioning & extensibility

- Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes).
- Per-schema versions (`vertical_schemas.version`).
- Deprecation: N+2, 2-year grace, migration guidance.
- Extensions: `extensions` object, namespaced `{org}.{division}.{purpose}`,
  registered in [governance/EXTENSION-REGISTRY.md](../governance/EXTENSION-REGISTRY.md).
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
is defined in [schemas/offer.json](../schemas/offer.json). Offer creation,
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

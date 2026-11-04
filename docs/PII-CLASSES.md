# LCP PII sensitivity classes

> **Normative taxonomy · v1.2.0** (maintainer decision
> [D9](DECISIONS-v1.2.md#d9--pii-sensitivity-taxonomy), recorded by
> [LEP-0011](../governance/LEPs/LEP-0011-pii-sensitivity-taxonomy.md))
>
> This page is normative for **how a field is classed**. `schemas/*.json`
> remains normative for types, requiredness, formats, limits, and
> `additionalProperties`; SPEC.md remains normative for protocol
> behaviour. Where this page and a schema disagree about a field's class,
> this page is right and the schema's annotation is the defect.

## Purpose

LCP carried `ping_safe: true|false` on vertical attributes and nothing
else. That flag answers one question — "may this value go out in a
`ping`?" — and answers it per field, per vertical, by hand. It does not
say what a field *is*, so three things were impossible to check:

- a vertical author could tag a health or financial attribute
  `ping_safe: true` and no gate would notice (the reference platform
  only verifies the tag is present and true — `_ping_safe_errors` in
  `implementations/reference-platform/lcp_platform/validation.py`);
- `lead`/`post` could grow a field carrying a government identifier or a
  payment card number, because prose was the only prohibition;
- the SPEC §4 message-class claim (`spec/02-envelope.md`; `ping`: "None
  (hashes only)", `event`: "None", `ack`: "None") was a statement about
  curators, not a check.

This page fixes the vocabulary: five classes, one annotation per schema
property, one emission matrix, one refusal rule. Everything enforceable
downstream — the ping allowlist, the extension policy, the erasure path,
retention schedules — keys off the class instead of re-deriving it.

## The five classes

Ordered from least to most protective. The order is normative: it is what
"downgrade" is defined against (see [No downgrade](#no-downgrade)).

| Class | Definition | Emitted in a `ping`? |
|---|---|---|
| `public` | Carries no consumer data and no commercial secret. Safe to publish in documentation, telemetry, public registries, and third-party aggregates. | Yes |
| `internal` | No consumer data, but confidential to the parties or the deployment: prices, routing metadata, quality signals, opaque record identifiers. | Yes |
| `contact` | Identifies, locates, or can reach a natural person — the consumer or a staff member named in the record. Includes per-pair pseudonyms derived from such values. | Only as a declared hash projection |
| `restricted` | About the person or their record in a way that is sensitive, evidentiary, or a capability: date of birth, health/financial/legal indicators, free-form intake answers, consent evidence values, recording references, session-bearing URLs, filenames. | Never |
| `prohibited` | A value LCP will not carry at all. See [Prohibited values](#prohibited-values). | Never |

### `public`

Protocol vocabulary and coarse geography: `vertical`, `schema_version`,
`currency`, `event.event`, `ack.status`, `history.message_type`,
`history.direction`, `lcp.version`, `message.type`, `message.test`,
`security.algorithm`, `error.code`, `error.name`.

Test: could this value be printed on the protocol's public website without
disclosing anything about any party's business or any person? If yes,
`public`.

### `internal`

Opaque identifiers and commercial facts: `lead_id`, `ping_id`,
`offer_id`, `buyer_id`, `price_cents`, `pricing.*` (except
`pricing.currency`),
`ping.floor_price_cents`, `lead_quality.*`, `compliance_flags.*`,
`quality_flags.*`, `status`, `channel`, `exclusivity.*`, `expiry.*`.

Test: does disclosure tell a competitor or an unauthorized party something
about the transaction, without telling them anything about a person? If
yes, `internal`.

### `contact`

Direct identifiers and everything that reaches a person:
`consumer.first_name`, `consumer.last_name`, `consumer.full_name`,
`consumer.email`, `consumer.phone`, `consumer.secondary_phone`,
`consumer.preferred_contact_method`, `location.street_address` (D4),
`provenance.ip_address` (D5, post-only), staff names
(`call.agent.name`), contact-availability windows
(`lead.contact_window.*`), and the per-pair pseudonyms
`consumer.phone_hash`, `consumer.email_hash`, `ping.phone_hash`,
`ping.email_hash`, `call.caller_phone_hash`, `post.contacted_hashes[]`,
`history.caller_phone_hash`, `provenance.ip_hash`,
`provenance.user_agent_hash`, `compliance.otp.verified_value_hash`.

A hash of a contact value is `contact`, not `public`: it is a stable
pseudonym for one person within one pair, and a pinged buyer can join
every ping it receives on it.

### `restricted`

Data that describes the person or the record rather than reaching the
person, where exposure or cross-context reuse causes harm:
`consumer.dob`, the `compliance` block (evidence values, session and
recording references), `provenance.source_url`,
`provenance.landing_page_url`, `provenance.agent.attestation`,
`call.recording.*`, `call.ivr.digits`, `attachments[].filename`,
`attachments[].storage_ref`, `event.details`, `lead/post.extensions`,
`compliance.scrubs[].details`.

`restricted` is where a vertical's sensitive attributes land: health
condition, smoking status, injury and treatment, fault, attorney status,
claim and bankruptcy history, veteran status, credit classification. Those
are the fields `docs/PRIVACY-DATA-GOVERNANCE.md` already calls
`ping_safe: false`; this class is the reason, not a second opinion.

Test: would you object to this value being copied into a buyer's CRM, a
support ticket, or a log line? If yes, `restricted`.

### `prohibited`

Values with no legal home in LCP. They are refused, not scrubbed — see
[Prohibited values](#prohibited-values).

## No downgrade

The five classes form a ladder:

```
public  <  internal  <  contact  <  restricted  <  prohibited
```

**Rule.** A vertical, registered extension, deployment overlay, or
capability-filtered field map MUST NOT assign a class lower than the class
the core (or the extension's registry record) assigns to the same field.
**Upgrades are always allowed**: a vertical MAY class its own attribute
`restricted` when the core floor for `attributes` is `internal`.

**Enforcement.** The validator refuses a downgrade when it loads a schema,
not when a message arrives, so an operator learns at deploy time rather
than after a disclosure:

- the schema is refused, the process refuses to start (or the schema is
  refused in CI), and the error names the field;
- the error is `LCP-100 VALIDATION_ERROR` with `field` set to the
  fully-qualified property path and `details` naming both classes:

```json
{
  "errors": [
    {
      "code": "LCP-100",
      "name": "VALIDATION_ERROR",
      "message": "x-lcp-pii downgrades core class for consumer.phone: core requires contact, vertical mortgage declares internal",
      "field": "consumer.phone",
      "details": {
        "class_required": "contact",
        "class_declared": "internal",
        "source": "verticals/mortgage.json"
      }
    }
  ]
}
```

Two consequences worth stating plainly:

1. **A class is not a ping decision.** Class by disclosure harm; the
   emission matrix and the message allowlist decide eligibility. A field
   that must appear in a ping but classes `restricted` is a design error:
   send a banded `public`/`internal` derivative instead (that is exactly
   why `budget_band` exists and `budget_cents` does not).
2. **`ping_safe: true` cannot buy a downgrade.** The flag is an
   eligibility statement, bounded by the class: `ping_safe: true` on a
   `contact` or `restricted` property is a validator error unless the
   property is a declared hash projection (see
   [Hashes and hash projections](#hashes-and-hash-projections)).

## Emission matrix

Read a row as "a value of this class, in this message class". `allow`
means the value is emitted when the message's own allowlist or
registration admits the field — the class never widens an allowlist.
`refuse` means the message is rejected.

| Class | `ping` | `bid` | `lead` | `post` | `event` | history row | logs, metrics, traces |
|---|---|---|---|---|---|---|---|
| `public` | allow | allow | allow | allow | allow | allow | allow |
| `internal` | allow | allow | allow | allow | allow | allow | allow |
| `contact`, plaintext | **refuse** (`LCP-008`) | **refuse** (`LCP-100`) | allow | allow | **refuse** (`LCP-100`) | **refuse** (`LCP-100`) | **never** |
| `contact`, hash projection | allow (declared projections only) | **never** (no projection is defined on `bid`) | allow | allow | allow (declared projections, e.g. `CONTACTED` digests) | allow (`caller_phone_hash`) | **never** |
| `restricted` | **refuse** (`LCP-008`) | **refuse** (`LCP-100`) | allow | allow | **refuse** (`LCP-100`) | **refuse** (`LCP-100`) | **never** |
| `prohibited` | **refuse** (`LCP-100`, naming the field; `LCP-008` also applies) | refuse | refuse | refuse | refuse | refuse | **never** |

Notes that the matrix does not show:

- **`ping` is the strict allowlist.** `schemas/ping.json` is
  `additionalProperties: false`; a field that is not in it does not travel
  in a ping however it is classed. `LCP-008 PII_IN_PING` is the code for a
  class violation *and* for an unknown field. The one container with
  open keys is `ping.extensions`, and it is bounded twice: the namespace
  must be registered for ping admission, and every field the registration
  contributes must be classed `public` or `internal`.
- **`lead` and `post` are the full-PII classes** (SPEC §4). They are the
  only messages where plaintext `contact` and `restricted` values are
  legal, and they remain subject to `docs/PRIVACY-DATA-GOVERNANCE.md`.
- **`event` carries no consumer data.** SPEC §4 says so; the matrix makes
  it mechanical: `event.details` is an open object classed `restricted`,
  and the `event` column refuses `restricted`. A `FINAL_PRICING` or
  `CONVERTED` report carries `public`/`internal` values and `contact`
  digests only.
- **History rows carry hashes, not values.** `schemas/history-request-row.json`
  admits `caller_phone_hash` (a `contact` hash projection keyed to the
  requesting credential) and no other consumer field.
- **Logs are the strictest surface, not a side channel.** A `contact` hash
  in a log line is a stable pseudonym that correlates every event of one
  consumer across services; it MUST NOT be logged, even though a dedup
  table inside the pair's trust boundary is a legitimate home for the same
  value. `restricted` values MUST NOT be logged at all — not truncated, not
  hashed, not "for debugging". `docs/PRIVACY-DATA-GOVERNANCE.md` §"Required
  operational controls" already forbids raw PII in logs, URLs, metrics,
  traces, exception messages, and support tickets; this row is its
  machine-checkable form.
- **Attachments.** `attachments[]` carries metadata only; the bytes are
  out of band behind `storage_ref`. `storage_ref` and `filename` are
  `restricted` (a capability and a possible name), so an `attachments`
  container is `restricted` and never approaches a ping.

## Hashes and hash projections

A **hash projection** is a declared field that carries
`HMAC-SHA256(pair_secret, plaintext)` for one plaintext field. Only
declared projections may cross into a `ping`, an `event`, or a history row.
Hashing a field does not create a projection.

**Algorithm and key.**

- `HMAC-SHA256`, lowercase hex, 64 characters — the same construction as
  `phone_hash` (SPEC §9, `spec/01-transport.md`).
- The secret is **per pair**: the publisher holds one secret per receiver
  (buyer or platform), so the same person yields a different digest for
  every pair. A digest computed under pair A is meaningless to pair B.
- The protocol does not define key distribution; it is a bilateral
  deployment concern (SPEC §9).
- Unsalted digests and SHA-1 anywhere in a contact-hash path are
  conformance failures (SPEC §9, §14 blocker B3).

**Preimages.**

| Plaintext | Projection field(s) | Preimage rule |
|---|---|---|
| `consumer.phone` | `consumer.phone_hash`, `ping.phone_hash`, `call.caller_phone_hash`, `post.call.caller_phone_hash`, `post.contacted_hashes[]`, `history.caller_phone_hash` | The wire value verbatim, byte for byte — no folding, stripping, or reformatting (E.164 by core schema on conforming traffic). Pinned by `test-vectors/sdk/contact-hash.json`. |
| `consumer.email` | `consumer.email_hash`, `ping.email_hash` | Canonical email: trim, ASCII-lowercase the domain, keep local-part bytes, plus-addresses distinct. **Pin-in-progress** — no email-form `contacted_hashes` entry ships until SPEC §9's pin is confirmed and the executed vectors publish. |
| `provenance.ip_address` (D5, post-only) | `provenance.ip_hash` | The raw address as carried. Normalization is not vector-pinned today, so an implementation MUST NOT assume `ip_hash` is comparable across pairs or SDKs. |
| raw user agent | `provenance.user_agent_hash` | Not carried in plaintext; normalization is deployment-defined and not vector-pinned. |
| one-time passcode value | `compliance.otp.verified_value_hash` | The code itself MUST NOT be carried (`compliance.otp` says so); only the digest may appear. |

**Everything else has no projection.** In particular:

- names (`consumer.first_name`, `consumer.last_name`, `consumer.full_name`)
  and dates of birth MUST NOT be hashed into a "safe" field. Their
  keyspaces are small enough to enumerate, so the digest inverts by
  lookup — the same defect that removed unsalted contact digests.
- `consumer.secondary_phone` has no projection: dedup is scoped to the
  primary phone, and adding a second key would silently change the dedup
  universe.
- a `restricted` value cannot be made emittable by hashing it. Only fields
  listed in the projection table have digests, and only those digests may
  be ping-emitted.

**Never to a party without the key.** A projection MUST NOT be sent to any
party that does not hold the pair secret it was computed under. A digest is
not anonymized data; it is a join key for whoever can recompute it. This is
why the platform cannot re-key a *reported* digest — it can only re-key
from plaintext it holds (SPEC §9, LEP-0003 §3).

## Prohibited values

LCP will not carry these, in any message, in any extension, at any
version:

| Family | Examples |
|---|---|
| Government identifiers | SSN, SIN, TFN, national ID or personal tax ID, passport number, driver's licence number, immigration or visa file number |
| Full payment credentials | PAN (card number), CVV/CVC, magstripe or track data, PIN block, full bank account number with routing/sort data |
| Biometric templates and captures | Fingerprint templates, face templates or images, voice-print templates, iris scans |
| Authentication secrets | One-time passcode values (`compliance.otp` already forbids the code), pair secrets, private keys, API keys |

**Refusal, never scrub.** A prohibited value is a validator error naming
the field, and the message is rejected. A scrub — silently dropping or
masking the value and accepting the message — is the one behaviour
explicitly forbidden here: it hides a non-conforming sender, destroys the
audit fact that a prohibited value was on the wire, and leaves the
receiver unable to decide whether the sender is trustworthy.

- **At intake:** `LCP-100 VALIDATION_ERROR`, `field` set to the path where
  the value was found (for open containers, `event.details.<key>` or
  `extensions.<namespace>.<key>`), and the message is not stored.
- **At load:** a schema property whose declared class is `prohibited` is a
  refusal: a field LCP will not carry may not be added. The class exists
  so the coverage check can distinguish "deliberately refused" from "not
  yet classified".
- **Detection is the receiver's job.** The protocol defines the refusal
  behaviour, not a detector algorithm; a deployment MAY use any
  deterministic DLP or pattern check. A conformant sender simply never
  produces such a value, so a detector's false negatives cost nothing on
  conforming traffic.

No property in the v1.2.0 core is classed `prohibited` — the mapping table
below shows none, which is a statement about the core, not about what
arrives at runtime in an open object.

## Choosing a class

For a vertical attribute, a registered extension, or a new core field, work
the list in order and stop at the first match:

1. **Is the value, or anything it is derived from, a prohibited value?**
   Then there is no field. Refuse the requirement and point at the family
   in [Prohibited values](#prohibited-values).
2. **Does it identify, locate, or reach one person** — consumer or staff —
   **or uniquely distinguish them within a small group?** → `contact`.
   Add a hash projection only if dedup or matching genuinely needs one.
3. **Does it describe the person or the record in a sensitive,
   commercial-in-confidence-personal, or evidentiary way?** → `restricted`.
   Health, financial, legal, insurance, employment, family, and free-form
   answers all land here; so do capabilities (`storage_ref`), recording
   references, and URLs that carry session or click identifiers.
4. **Is it about the transaction, the routing, or the record rather than
   the person, and is it not publishable?** → `internal`.
5. **Is it safe on the public website, in a public registry, or in
   third-party telemetry?** → `public`.

Tie-breaks, all mandatory:

- **When two classes both fit, choose the more protective.** Over-classing
  costs a ping field; under-classing costs a disclosure.
- **A container takes the maximum of its descendants**, evaluated within a
  single schema document. An object with one `restricted` child is
  `restricted`; an array takes its items' class. So `lead.consumer` is
  `restricted` even though most of it is `contact`, because `consumer.dob` is
  `restricted`. **Structural containers are the exception**: `lcp`, `lcp.message`,
  and `lcp.payload` are envelope scaffolding with no data of their own — their
  class is `internal` and each child carries its own class, so a nested
  `lcp.message.security` stays `restricted` without escalating the envelope.
- **An open object with no registered shape is `restricted`.** That is
  `event.details` and `lead/post.extensions`. An open object whose
  documented contract forbids consumer values (`error.details`) may be
  `internal`, and then MUST NOT carry `contact` or `restricted` values.
- **Never classify by what the ping needs.** Classify by disclosure harm.
  If the routing needs it and the class forbids it, change the field to a
  banded or aggregate form.

Verticals and extensions must also keep `ping_safe` consistent with the
class: `ping_safe: true` requires `public`/`internal`, or a declared
`contact` hash projection. `ping_safe: false` implies nothing about the
class and remains legal on any class.

## Annotation: `x-lcp-pii`

Every schema property in `schemas/*.json`, every vertical attribute, and
every registered extension field carries exactly one annotation:

```json
"consumer": {
  "type": "object",
  "x-lcp-pii": "restricted"
}
```

- Key: `x-lcp-pii`. Value: one of the five class names, lowercase — plus
  `computed`, which is legal only on `envelope.json`'s `payload`.
- Placement: on every property definition, including nested objects
  (`compliance.otp.channel`), array item objects
  (`attachments[].filename`), `$defs` entries in `core.json`, and the
  `payload` property of `envelope.json`, which is `computed` (see below).
- Missing annotation is a coverage defect, not a default. CI refuses an
  unannotated property once the rollout in LEP-0011 reaches its third
  step.
- `x-lcp-pii` is a JSON Schema **annotation**: validators that do not know
  the keyword ignore it, exactly as they already ignore `ping_safe` and
  `$comment`. It changes no validation outcome, and it is not a wire
  field.

`payload` in `envelope.json` is annotated `computed`: its class is the
maximum over the payload schema selected by `message.type`.

## Core field mapping

Every property in `schemas/` at v1.2.0, classed. 371 `properties` entries
were enumerated across the eleven schema documents on 2026-11-04, counting
each definition once at the path it is declared (a `$ref` consumer such as
`lead.consumer` takes the class of its definition) and counting the
repeated entries inside `if`/`then` conditionals as the entries they
repeat. `Ping` is `allow` (in the ping allowlist), `hash` (hash projection
only), `no` (not ping-emittable), `computed` (from the selected vertical),
or `container` (never directly; its children decide).

### `schemas/core.json`

**`$defs/consumer`**

| Field | Class | Ping | Notes |
|---|---|---|---|
| `first_name` | `contact` | no | Given name. No hash projection: the keyspace inverts. |
| `last_name` | `contact` | no | Family name. No projection, same reason. |
| `full_name` | `contact` | no | Unstructured name. |
| `email` | `contact` | hash | Projection: `email_hash`. |
| `phone` | `contact` | hash | Projection: `phone_hash`. E.164. |
| `secondary_phone` | `contact` | no | Deliberately unprojected; dedup is primary-phone scoped. |
| `preferred_contact_method` | `contact` | no | A contact channel choice; classed with the channel it selects. |
| `phone_hash` | `contact` | hash | The projection itself; still `contact`. |
| `dob` | `restricted` | no | Sensitive; high re-identification value; never projected. |
| `email_hash` | `contact` | hash | The projection itself. |
| `locale` | `internal` | no | BCP-47 tag; coarse, ping-carried only via attributes. |

**`$defs/location`**

| Field | Class | Ping | Notes |
|---|---|---|---|
| `country_code` | `internal` | allow | ISO 3166-1 alpha-2; required in a ping. |
| `state_region` | `internal` | allow | Coarse geography; ping-carried by design. |
| `postal_code` | `internal` | allow | Coarse geography; ping-carried by design. |
| `city` | `internal` | no | Not in the ping allowlist. |
| `timezone` | `internal` | no | IANA zone; coarse. |
| `street_address` | `contact` | no | Residential address; post-only, `ping_safe: false` (D4). A household identifier, unlike the coarse geography above. |

**`$defs/consent_evidence_entry`**

| Field | Class | Ping | Notes |
|---|---|---|---|
| `type` | `internal` | no | Consent framework name. |
| `provider` | `internal` | no | Evidence vendor. |
| `token_or_url` | `restricted` | no | Evidence value; may resolve to a capture containing PII. |

**`$defs/compliance`**

| Field | Class | Ping | Notes |
|---|---|---|---|
| `consent_timestamp` | `internal` | no | |
| `consent_source_url` | `restricted` | no | May carry session or click identifiers. |
| `consent_text_version` | `internal` | no | |
| `consent_purposes` | `internal` | no | What was consented to. |
| `consent_evidence` | `restricted` | container | Max of `token_or_url`. |
| `otp_verified` | `internal` | no | Presence flag; ping carries `compliance_flags.otp_verified`. |
| `otp` | `restricted` | container | Verification block; max of `verified_value_hash`. |
| `otp.channel` | `internal` | no | |
| `otp.verified_at` | `internal` | no | |
| `otp.verified_value_hash` | `contact` | hash | Projection of the verified value. |
| `otp.attempts` | `internal` | no | |
| `session_capture_id` | `restricted` | no | Reference to a session that may contain PII. |
| `call_recording_id` | `restricted` | no | Reference to audio. |
| `dnc_checked` | `internal` | no | |
| `consent_expires_at` | `internal` | no | |
| `scrubs` | `restricted` | container | Max of `details`. |
| `scrubs[].type` | `internal` | no | |
| `scrubs[].provider` | `internal` | no | |
| `scrubs[].result` | `internal` | no | Tri-state enum; ping carries the derived `quality_flags.*_status`. |
| `scrubs[].checked_at` | `internal` | no | |
| `scrubs[].details` | `restricted` | no | Free text; may quote the matched value. |

**`$defs/provenance`**

| Field | Class | Ping | Notes |
|---|---|---|---|
| `received_at` | `internal` | no | |
| `source_type` | `internal` | no | Open enum. |
| `acquisition_method` | `internal` | no | Ping carries the derived `quality_flags.acquisition_method`. |
| `is_incentivized` | `internal` | no | |
| `incentive_type` | `internal` | no | |
| `source_id` | `internal` | no | |
| `source_url` | `restricted` | no | URLs commonly carry click IDs and session tokens. |
| `ip_hash` | `contact` | no | Pair-keyed pseudo-identifier; `provenance` has no ping slot. |
| `ip_address` | `contact` | no | Raw IPv4/IPv6 for fraud and geo validation; post-only (D5). `ip_hash` is its projection and remains the ping-safe form. |
| `user_agent_hash` | `contact` | no | Device pseudonym. |
| `funnel_key` | `internal` | no | |
| `flow_key` | `internal` | no | |
| `campaign_id` | `internal` | no | |
| `creative_id` | `internal` | no | |
| `landing_page_url` | `restricted` | no | Session-bearing URL. |
| `platform_source` | `internal` | no | |
| `brand_id` | `internal` | no | |
| `form_id` | `internal` | no | |
| `agent` | `restricted` | container | Max of `attestation`. |
| `agent.agent_id` | `internal` | no | |
| `agent.acting_for` | `internal` | no | Principal identifier. |
| `agent.attestation` | `restricted` | no | Signed authority token; leakage enables replay. |

**`$defs/attributes`, `$defs/attachments`, scalar definitions**

| Field | Class | Ping | Notes |
|---|---|---|---|
| `attributes` | `internal` (floor) | computed | Vertical-defined; the effective class is the maximum over the selected vertical's declared classes. |
| `attributes.vertical` | `public` | allow | |
| `attributes.schema_version` | `public` | allow | |
| `attachments` | `restricted` | container | Max of `filename`/`storage_ref`. |
| `attachments[].attachment_id` | `internal` | no | |
| `attachments[].purpose` | `internal` | no | |
| `attachments[].filename` | `restricted` | no | Filenames embed names and document titles. |
| `attachments[].content_type` | `internal` | no | |
| `attachments[].size_bytes` | `internal` | no | |
| `attachments[].sha256` | `internal` | no | Content fingerprint, not a contact projection. |
| `attachments[].storage_ref` | `restricted` | no | Bearer capability to the bytes. |
| `attachments[].created_at` | `internal` | no | |
| `attachments[].expires_at` | `internal` | no | |
| `attachments[].encryption` | `internal` | no | |
| `attachments[].residency` | `internal` | no | |
| `attachments[].malware_scan` | `internal` | container | Non-sensitive evidence by contract. |
| `attachments[].malware_scan.status` | `internal` | no | |
| `attachments[].malware_scan.engine` | `internal` | no | |
| `attachments[].malware_scan.scanned_at` | `internal` | no | |
| `status` | `internal` | no | Lifecycle state; no consumer data. |
| `channel` | `internal` | no | Acquisition channel. |
| `currency` | `public` | allow | ISO 4217 code. |
| `error` | `internal` | container | Contractually non-PII. |
| `error.code` | `public` | no | `LCP-NNN`. |
| `error.name` | `public` | no | Taxonomy name. |
| `error.message` | `internal` | no | MUST NOT interpolate `contact`/`restricted` values. |
| `error.field` | `internal` | no | Property path. |
| `error.details` | `internal` | no | Open object whose error contract forbids consumer values; a `contact` or `restricted` value here is a contract violation. |
| `errors_array` | `internal` | container | |
| `errors_array.errors` | `internal` | no | |
| `exclusivity` | `internal` | container | |
| `exclusivity.exclusivity` | `internal` | no | |
| `exclusivity.max_buyers` | `internal` | no | |
| `expiry` | `internal` | container | |
| `expiry.expires_at` | `internal` | no | |
| `expiry.ttl_seconds` | `internal` | no | |
| `security` | `restricted` | container | Max of `signature`. |
| `security.signature` | `restricted` | no | Secret-derived; MUST NOT be logged. |
| `security.timestamp` | `internal` | no | |
| `security.algorithm` | `public` | no | `HMAC-SHA256`. |

### `schemas/lead.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `lead_id` | `internal` | no | A ping carries `lead_reference`, not this. |
| `external_id` | `internal` | no | Publisher's own handle. |
| `submitted_at` | `internal` | no | |
| `status` | `internal` | no | |
| `channel` | `internal` | no | |
| `consumer` | `restricted` | no | Max of `consumer.dob`. |
| `location` | `contact` | no | Container; max of `street_address`. |
| `compliance` | `restricted` | no | |
| `provenance` | `restricted` | no | |
| `attributes` | `internal` (floor) | computed | Effective class from the vertical. |
| `exclusivity` | `internal` | no | |
| `contact_window` | `contact` | no | Availability to reach the consumer. |
| `contact_window.timezone` | `internal` | no | |
| `contact_window.available_from` | `contact` | no | |
| `contact_window.available_to` | `contact` | no | |
| `contact_window.days` | `contact` | no | |
| `lead_quality` | `internal` | no | Publisher-declared, non-PII. |
| `lead_quality.verified_phone` | `internal` | no | |
| `lead_quality.verified_email` | `internal` | no | |
| `lead_quality.verification` | `internal` | container | |
| `lead_quality.verification.phone_method` | `internal` | no | |
| `lead_quality.verification.phone_verified_at` | `internal` | no | |
| `lead_quality.verification.phone_verified_by` | `internal` | no | Vendor name. |
| `lead_quality.verification.email_method` | `internal` | no | |
| `lead_quality.verification.email_verified_at` | `internal` | no | |
| `lead_quality.verification.email_verified_by` | `internal` | no | |
| `lead_quality.duplicate_risk` | `internal` | no | |
| `lead_quality.spam_risk_score` | `internal` | no | |
| `lead_quality.data_completeness` | `internal` | no | |
| `expiry` | `internal` | no | |
| `attachments` | `restricted` | no | |
| `extensions` | `restricted` | no | Unregistered open object; registered namespaces may not class lower. |

### `schemas/call.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `lead_id` | `internal` | no | |
| `external_id` | `internal` | no | |
| `submitted_at` | `internal` | no | |
| `status` | `internal` | no | |
| `channel` | `internal` | no | Const `call`. |
| `consumer` | `restricted` | no | |
| `location` | `contact` | no | Container; max of `street_address`. |
| `compliance` | `restricted` | no | |
| `provenance` | `restricted` | no | |
| `attributes` | `internal` (floor) | computed | |
| `expiry` | `internal` | no | |
| `exclusivity` | `internal` | no | |
| `attachments` | `restricted` | no | |
| `call` | `restricted` | no | Max of `recording`, `ivr.digits`. |
| `call.call_id` | `internal` | no | |
| `call.status` | `internal` | no | Open enum. |
| `call.hangup_cause` | `internal` | no | |
| `call.direction` | `internal` | no | |
| `call.did` | `internal` | no | Business number; not a consumer value. |
| `call.forwarded_to` | `internal` | no | Buyer's destination number. |
| `call.tracking_number` | `internal` | no | |
| `call.caller_phone_hash` | `contact` | hash | Projection of the caller's number. |
| `call.started_at` | `internal` | no | |
| `call.durations` | `internal` | container | |
| `call.durations.total_seconds` | `internal` | no | |
| `call.durations.ivr_seconds` | `internal` | no | |
| `call.durations.hold_seconds` | `internal` | no | |
| `call.durations.agent_seconds` | `internal` | no | |
| `call.recording` | `restricted` | container | |
| `call.recording.url` | `restricted` | no | Capability to audio. |
| `call.recording.transcript_url` | `restricted` | no | Capability to a transcript. |
| `call.recording.storage_ref` | `restricted` | no | Capability. |
| `call.ivr` | `restricted` | no | Max of `digits`. |
| `call.ivr.digits` | `restricted` | no | DTMF can carry account or identifier digits. |
| `call.ivr.path` | `internal` | no | |
| `call.ivr.language` | `internal` | no | |
| `call.ivr.abandoned` | `internal` | no | |
| `call.ivr.menu_options_selected` | `internal` | no | |
| `call.disposition` | `internal` | no | |
| `call.transferred_from` | `internal` | container | |
| `call.transferred_from.original_call_id` | `internal` | no | |
| `call.transferred_from.transfer_reason` | `internal` | no | |
| `call.queue` | `internal` | container | |
| `call.queue.id` | `internal` | no | |
| `call.queue.name` | `internal` | no | Business queue label. |
| `call.agent` | `contact` | no | Max of `name`: a staff member is a natural person. |
| `call.agent.id` | `internal` | no | |
| `call.agent.name` | `contact` | no | Staff personal name. |

### `schemas/ping.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `ping_id` | `internal` | allow | |
| `lead_reference` | `internal` | allow | Opaque; deliberately not `lead_id`. |
| `publisher_id` | `internal` | allow | |
| `offer_id` | `internal` | allow | |
| `phone_hash` | `contact` | hash | Declared projection. |
| `email_hash` | `contact` | hash | Declared projection. |
| `country_code` | `internal` | allow | |
| `state_region` | `internal` | allow | |
| `postal_code` | `internal` | allow | |
| `vertical` | `public` | allow | |
| `lead_age_minutes` | `internal` | allow | |
| `attributes` | `internal` (floor) | computed | Only fields tagged `ping_safe: true` in the selected vertical: class `public`/`internal`, or a declared hash projection. |
| `compliance_flags` | `internal` | allow | Presence indicators only. |
| `compliance_flags.consent` | `internal` | allow | |
| `compliance_flags.otp_verified` | `internal` | allow | |
| `compliance_flags.dnc_checked` | `internal` | allow | |
| `compliance_flags.consent_valid` | `internal` | allow | |
| `quality_flags` | `internal` | allow | |
| `quality_flags.verified_phone` | `internal` | allow | |
| `quality_flags.verified_email` | `internal` | allow | |
| `quality_flags.is_incentivized` | `internal` | allow | |
| `quality_flags.incentive_type` | `internal` | allow | |
| `quality_flags.duplicate_risk` | `internal` | allow | |
| `quality_flags.spam_risk_score` | `internal` | allow | |
| `quality_flags.acquisition_method` | `internal` | allow | |
| `quality_flags.dnc_status` | `internal` | allow | |
| `quality_flags.litigator_status` | `internal` | allow | |
| `quality_flags.blacklist_status` | `internal` | allow | |
| `floor_price_cents` | `internal` | allow | |
| `currency` | `public` | allow | |
| `exclusivity` | `internal` | allow | |
| `slots_available` | `internal` | allow | |
| `dedup_window_hours` | `internal` | allow | |
| `expires_at` | `internal` | allow | |
| `extensions` | `internal` | allow | Registered namespaces only, and only when the registration declares ping safety; every other namespace is refused by name. A registration that admits a ping must class every contributed field `public` or `internal` — ping admission is a class promise, not a bypass. |

### `schemas/bid.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `ping_id` | `internal` | no | |
| `decision` | `internal` | no | |
| `bid_price_cents` | `internal` | no | |
| `currency` | `public` | no | |
| `estimated_contact_seconds` | `internal` | no | |
| `buyer_reference` | `internal` | no | |
| `reject_reason` | `internal` | no | |
| `capacity_remaining` | `internal` | no | |
| `slots_claimed` | `internal` | no | |

### `schemas/post.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `lead_id` | `internal` | no | |
| `delivered_at` | `internal` | no | |
| `submitted_at` | `internal` | no | |
| `offer_id` | `internal` | no | |
| `price_cents` | `internal` | no | |
| `currency` | `public` | no | |
| `buyer_id` | `internal` | no | |
| `buyer_reference` | `internal` | no | |
| `pricing` | `internal` | container | Billing record; no consumer data. |
| `pricing.floor_price_cents` | `internal` | no | |
| `pricing.premium_cents` | `internal` | no | |
| `pricing.final_price_cents` | `internal` | no | |
| `pricing.uncapped_price_cents` | `internal` | no | |
| `pricing.max_allowed_price_cents` | `internal` | no | |
| `pricing.currency` | `public` | no | Settlement currency (D7); a code, not a commercial fact. |
| `pricing.original_price_cents` | `internal` | no | Buyer's native minor-unit price (D7). |
| `pricing.fx_rate` | `internal` | no | Rate and provenance are commercially sensitive (D7). |
| `pricing.fx_source` | `internal` | no | |
| `pricing.fx_timestamp` | `internal` | no | |
| `pricing.fx_expires_at` | `internal` | no | |
| `pricing.price_guardrails` | `internal` | no | |
| `pricing.is_duplicate_resub` | `internal` | no | |
| `pricing.payable_definition` | `internal` | no | |
| `pricing.payable_status` | `internal` | no | |
| `pricing.dispute_window_hours` | `internal` | no | |
| `pricing.dispute_window_expires_at` | `internal` | no | |
| `matched_preferences` | `internal` | no | Echoed buyer criteria; no consumer identity. |
| `consumer` | `restricted` | no | Max of `consumer.dob`. |
| `location` | `contact` | no | Container; max of `street_address`. |
| `compliance` | `restricted` | no | |
| `attributes` | `internal` (floor) | computed | |
| `call` | `restricted` | no | Same shape and classes as `call.call`. |
| `attachments` | `restricted` | no | |
| `provenance` | `restricted` | no | |
| `exclusivity` | `internal` | no | |
| `contacted_hashes` | `contact` | hash | Array of declared projections (LEP-0003). |
| `extensions` | `restricted` | no | Unregistered open object; registered namespaces may not class lower. |

### `schemas/ack.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `original_message_id` | `internal` | no | |
| `status` | `public` | no | Closed-ish protocol vocabulary. |
| `errors` | `internal` | no | |
| `lead_id` | `internal` | no | |
| `request_id` | `internal` | no | |
| `rejection_reason` | `internal` | no | Open enum of routing reasons. |

### `schemas/event.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `lead_id` | `internal` | no | |
| `event` | `public` | no | Open enumeration. |
| `timestamp` | `internal` | no | |
| `details` | `restricted` | no | Open object; classed for the worst case, so the `event` column refuses it. Known shapes (`CONVERTED.conversion_value_cents`, `FINAL_PRICING`) are `internal` with `contact` digests only. |
| `external_reference` | `internal` | no | External sale or system record id. |

### `schemas/history-request-row.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `request_id` | `internal` | no | The stored envelope's `message.id`. |
| `lead_id` | `internal` | no | Charset-pinned to refuse contact shapes. |
| `message_type` | `public` | no | |
| `direction` | `public` | no | |
| `status` | `internal` | no | Open pass-through. |
| `sender_id` | `internal` | no | |
| `receiver_id` | `internal` | no | |
| `vertical` | `public` | no | |
| `test` | `public` | no | |
| `price_cents` | `internal` | no | |
| `currency` | `public` | no | |
| `caller_phone_hash` | `contact` | hash | Keyed to the requesting credential. |
| `expires_at` | `internal` | no | |
| `created_at` | `internal` | no | |
| `updated_at` | `internal` | no | |

### `schemas/offer.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `offer_id` | `internal` | — | |
| `buyer_id` | `internal` | — | |
| `tenant_id` | `internal` | — | Deployment metadata. |
| `active` | `internal` | — | |
| `routing_mode` | `internal` | — | |
| `vertical` | `public` | — | |
| `schema_version` | `public` | — | |
| `countries` | `internal` | — | Service area, not a consumer location. |
| `state_regions` | `internal` | — | |
| `postal_codes` | `internal` | — | |
| `channels` | `internal` | — | |
| `excluded_source_types` | `internal` | — | |
| `excluded_acquisition_methods` | `internal` | — | |
| `reject_incentivized` | `internal` | — | |
| `excluded_incentive_types` | `internal` | — | |
| `require_verified_phone` | `internal` | — | |
| `require_verified_email` | `internal` | — | |
| `max_spam_risk_score` | `internal` | — | |
| `excluded_claim_language` | `internal` | — | |
| `require_consent_evidence` | `internal` | — | |
| `reject_dnc_flagged` | `internal` | — | |
| `reject_litigator_flagged` | `internal` | — | |
| `reject_blacklist_flagged` | `internal` | — | |
| `min_data_completeness` | `internal` | — | |
| `floor_price_cents` | `internal` | — | |
| `currency` | `public` | — | |
| `payable_definition` | `internal` | — | |
| `daily_cap` | `internal` | — | |
| `hourly_cap` | `internal` | — | |
| `allowed_publisher_ids` | `internal` | — | |
| `allowed_brand_ids` | `internal` | — | |
| `attribute_equals` | `internal` | — | Predicate values are criteria, never consumer data. |
| `attribute_in` | `internal` | — | |
| `monthly_minimum_payable` | `internal` | — | |
| `monthly_maximum_payable` | `internal` | — | |
| `monthly_quota_timezone` | `internal` | — | |
| `monthly_quota_policy` | `internal` | — | |
| `payable_rules` | `internal` | — | |
| `payable_rules.mode` | `internal` | — | |
| `payable_rules.require_call_answered` | `internal` | — | |
| `payable_rules.minimum_call_seconds` | `internal` | — | |
| `payable_rules.allowed_call_dispositions` | `internal` | — | |
| `payable_rules.exclude_duplicate_resubmissions` | `internal` | — | |
| `call_routing_mode` | `internal` | — | |
| `connect_timeout_seconds` | `internal` | — | |
| `delivery_windows` | `internal` | — | |
| `delivery_windows[].timezone` | `internal` | — | |
| `delivery_windows[].available_from` | `internal` | — | |
| `delivery_windows[].available_to` | `internal` | — | |
| `delivery_windows[].days` | `internal` | — | |
| `delivery_windows[].verticals` | `public` | — | |
| `delivery_windows[].channels` | `internal` | — | |
| `ping_timeout_seconds` | `internal` | — | |
| `webhook_url` | `restricted` | — | Bearer capability; a leaked URL delivers leads to an attacker. |
| `extensions` | `internal` | — | Offer-side criteria; MUST NOT carry consumer data. |

`offer.json` has no ping column: an offer is not a wire message. Its class
matters because an offer is a capability — `webhook_url` is the reason the
`restricted` class is not limited to consumer values.

### `schemas/envelope.json`

| Field | Class | Ping | Notes |
|---|---|---|---|
| `lcp.version` | `public` | — | |
| `lcp.message` | `internal` | container | |
| `lcp.message.id` | `internal` | — | |
| `lcp.message.type` | `public` | — | Closed enumeration. |
| `lcp.message.timestamp` | `internal` | — | |
| `lcp.message.sender_id` | `internal` | — | |
| `lcp.message.receiver_id` | `internal` | — | |
| `lcp.message.correlation_id` | `internal` | — | |
| `lcp.message.idempotency_key` | `internal` | — | |
| `lcp.message.test` | `public` | — | |
| `lcp.message.security` | `restricted` | container | Max of `signature`. |
| `lcp.message.security.signature` | `restricted` | — | |
| `lcp.message.security.timestamp` | `internal` | — | |
| `lcp.message.security.algorithm` | `public` | — | |
| `lcp.payload` | `computed` | — | Class of the payload schema selected by `message.type`. |

### Verticals

The six vertical schemas declare 125 properties, 108 of them tagged
`ping_safe` (measured 2026-11-04; the untagged ones are `vertical`,
`schema_version`, and each vertical's remaining non-ping-safe metadata
fields). Each one also needs an `x-lcp-pii`
class, and the class is what makes the tag reviewable: a `restricted`
attribute may be `ping_safe: false` only, and a `contact` attribute may be
`ping_safe: true` only if it is a declared hash projection.

A vertical's attributes sort into the classes the same way core fields do,
and the tags already in the tree show it. `insurance.json` declares
`health_condition_band`, `smoker`, `marital_status`, `gender`,
`occupation_class`, and `height_band` as non-ping-safe — all `restricted`.
`legal.json` declares `injury_severity`, `medical_treatment_received`,
`at_fault`, `has_attorney`, and `claim_status` non-ping-safe —
`restricted`.
`mortgage.json` declares `bankruptcy_history`, `foreclosure_history`,
`veteran_status`, `credit_rating_band`, `annual_income_cents`, and
`loan_amount_cents` non-ping-safe — `restricted` — while
`property_address` is `contact`: the first vertical attribute that is a
plaintext contact value rather than a sensitive description.
`mva.json` declares `fault_position`, `injury_severity_band`,
`represented_by_attorney`, and `police_report_available` non-ping-safe —
`restricted`. `home_services.json` declares `exact_budget` and `free_text`
non-ping-safe — `restricted`; `budget_band` and the rest of its controlled
taxonomy stay `public`, which is why they are the banded derivatives that
exist so the exact values never travel. `solar.json` declares no
non-ping-safe attribute.

Most of the non-ping-safe attributes above are `restricted`;
`property_address` is `contact`; and a few describe the transaction rather
than the person (`insurance_company`, `time_to_contact_deadline_days`) and
stay `internal`. `ping_safe: false` never forces a class — it removes ping
eligibility and nothing else. The rule that makes the tags reviewable is
the same in every case: `ping_safe: false` is legal on any class, and
`ping_safe: true` is legal only on `public`/`internal` or a declared
`contact` hash projection.

## Worked refusals

**A vertical downgrades a core field.** `verticals/mortgage.json` declares
`consumer.phone` as `x-lcp-pii: internal`. Load is refused: `LCP-100`,
`field: "consumer.phone"`, `details.class_required: "contact"`. The
vertical cannot opt a phone number out of `contact`.

**A ping carries a contact value.** A ping is submitted with
`consumer.full_name` in the payload. It is not in the ping allowlist and
its class forbids the surface: `LCP-008 PII_IN_PING`, naming the field.

**An event carries a restricted value.** A publisher sends an
`ERASURE_REQUEST`-adjacent event with `details.dob` set. `event.details`
is `restricted` on an `event`, which the matrix refuses: `LCP-100`,
`field: "details.dob"`.

**A prohibited value reaches an open container.** An intake form posts an
SSN inside an answer carried by `extensions.lcp.intake.raw_form`. The
refusal is `LCP-100` naming the path inside that namespace — never a
namespace-level "rejected" with the field unnamed, because the sender has
to know which answer to fix. The message is not stored and the value is not
scrubbed: the sender is told it sent something LCP will not carry.

**A vertical claims a new attribute is `public` to make it pingable.**
Permitted only if it truly is: the class is reviewed against this page, and
`ping_safe: true` alone proves nothing. A `restricted` attribute marked
`ping_safe: true` is a validator error at load, before any ping exists.

## Change control

- **Adding or removing a class, or changing their order:** MAJOR — every
  annotation and every downstream gateway would need reinterpretation.
- **Reclassifying a field to a more protective class:** MINOR (additive;
  no wire change). It can only remove emission eligibility, never add it,
  so a peer that reads no annotations is unaffected.
- **Reclassifying a field to a less protective class:** requires an LEP
  with a security/privacy co-sign. Widening disclosure is never a patch.
- **Annotating a previously unannotated property:** PATCH, gated by the
  coverage check.
- **A vertical or registered extension declaring its own classes:** a
  MINOR of that schema document; the load-time no-downgrade rule applies
  immediately.

---

**See also:** [Privacy and data governance](PRIVACY-DATA-GOVERNANCE.md) ·
[Consent and evidence guide](CONSENT-GUIDE.md) ·
[Field dictionary](FIELD-DICTIONARY.md) ·
[LEP-0011](../governance/LEPs/LEP-0011-pii-sensitivity-taxonomy.md)

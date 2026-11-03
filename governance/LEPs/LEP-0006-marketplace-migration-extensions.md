# LEP-0006 — Marketplace migration extensions: final-pricing feedback and the form.questions surface

- **Title:** Marketplace migration extensions: final-pricing feedback (`FINAL_PRICING` event) and `{org}.form.questions` (lead/post `extensions`)
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-03
- **Status:** **Draft** — no implementation authorized; registry rows land as *proposed-on-acceptance*
- **Target version:** v1.1.0 (additive MINOR; Track A carries zero schema change, Track B adds one optional closed-object field to `lead`/`post`)
- **Discussion:** [#185](https://github.com/SpearSystems/LCP/issues/185)
  (`[LEP] Marketplace migration extensions: final-pricing feedback and {org}.form.questions`)
- **Roadmap triggers:** `docs/V1.1-ROADMAP.md`, Phase 6 — "External sale
  feedback (final_pricing equivalent)" (medium) and "Freeform questions
  extension shape" (medium)

## Separation of the two tracks

LEP.md (Decision rules): "Separate capabilities with materially different
semantics may share an umbrella discussion, but each must have
independently reviewable and independently decidable acceptance criteria."
Track A (price feedback on the existing open `event` surface) and Track B
(a new `extensions` surface on `lead`/`post` plus one registered payload
shape) share only the migration story; they touch different messages,
different risk classes, and different reviewer roles. **Each track is
accepted, rejected, or deferred independently** (the LEP-0001
batch/subscription precedent); the registry row and the decision record
name per-track outcomes. This record recommends Track A over the
zero-schema-cost `event` path and Track B over an LEP-carried payload
change — the two routes differ precisely because their data lands on
different message classes, and the asymmetry is defended below.

## Problem statement and evidence

### Track A — final-pricing feedback

`docs/V1.1-ROADMAP.md` Phase 6, "External sale feedback (final_pricing
equivalent)": publishers selling the same lead across multiple networks
need to report what a ping actually sold for — **including external
sales** — so buyers can refine bids. Design constraint from the same
section: publisher→platform feedback of winning price and duration for a
ping, keyed by ping ID; "extension or event-detail candidate
(`{org}.marketplace.final_pricing`), not core; must not conflict with the
platform's own auction price in `post.pricing`."

**Internal evidence.** The protocol already structures one post-sale
value fact: the `CONVERTED` event's `details.conversion_value_cents`
(SPEC.md §4), explicitly called out in §3 as the structured exception
because "close value on conversion is a lifecycle fact, not a calculated
metric." Nothing analogous exists for the *other* sale outcomes that
matter to a pinged buyer: the lead sold elsewhere, or sold for more than
the auction cleared. Buyers bid blind to those outcomes today; the
reference network's `final_pricing` reports exist precisely to close
that feedback loop, and the roadmap classifies the mechanism (not the
implementation) as a genuine gap.

**Wire-surface reality (measured 2026-11-03).** The extension candidate
name from the roadmap implies a payload `extensions` surface, but
`payload.extensions` is defined **only** on
`schemas/offer.json:248-252` (buyer-side matching criteria,
`additionalProperties: true`). Every delivery message is a strict closed
object: `schemas/ping.json:206`, `schemas/lead.json:166`,
`schemas/post.json:176`, `schemas/bid.json` (allowlist),
`schemas/ack.json` — verified by reading each root's
`additionalProperties: false`. A publisher has exactly one message class
it sends to the platform post-auction whose content is open by design:
`event`, whose `details` is `additionalProperties: true`
(`schemas/event.json:25-28`) and whose `event` value is an open
enumeration with no schema enum (`schemas/event.json:16-19`; SPEC.md §6
"Open vs. closed enumerations": unknown event values MUST be stored and
passed through; adding values is a MINOR). Track A therefore rides the
event surface — the zero-schema-cost route, exactly the mechanism
LEP-0003 §4 used for `CONTACTED` reporting — and this record documents
why the alternative (a payload change) was rejected there.

### Track B — the `{org}.form.questions` home

Roadmap Phase 6, "Freeform questions extension shape": partners
migrating from legacy ping/post networks carry `questions[]`
(question_text/answer_text) data with no standard home in LCP; the
design constraint is the registered extension pattern
`{org}.form.questions` — post-only, never `ping_safe` — so the
controlled-taxonomy decision in `home_services` stays intact.

**Internal evidence — the destination is already promised but does not
exist.** `verticals/home_services.json:5`: "Publishers normalize
brand-specific form questions into this controlled taxonomy; unsupported
source questions remain in an **approved post-only extension** or source
archive." That approved extension surface is not in the protocol today:
there is no `extensions` field on `lead` or `post` (closes above). The
one "post-only" free-text escape the vertical registry ships —
`free_text` (`verticals/home_services.json:103`, `ping_safe: false`) —
is a single string with no question/answer structure, which is exactly
why the roadmap asks for a *registered shape* rather than leaning on it.

**Internal evidence — the reference platform already consumes a
lead-side `extensions` object that its own schema forbids.** The
claim-language restriction evaluates namespaced evidence from the lead
payload: `matching.py:193-196` reads
`payload.get("extensions", {}).get("lcp.platform.claim_language")` and
fail-closes (`claim_language_evidence_missing`) when absent;
`matching.py:116-120` similarly reads
`extensions.get("lcp.platform.required_consent_purposes")` /
`"lcp.platform.contact_purpose"` from the offer side (which is legal —
`schemas/offer.json:248`). A lead carrying the evidence block the
matcher demands **fails the published `lead` schema** — measured:
`Additional properties are not allowed ('extensions' was unexpected) at
(root)` (run recorded below). SPEC.md §6 line 655 and §3 lines
224-235 both describe the `extensions` object as the mechanism for
deployment-specific data; §6:661-662 even lists "vertical `attributes`
extensions" among open objects. The protocol prose and the reference
matcher anticipate an extensions surface on the intake/delivery messages
that the schemas do not ship. Track B closes that gap with the smallest
expressible change, and the registered `form.questions` shape gives
migrating partners the structured home the vertical description already
promises.

**External evidence.** The roadmap comparison (2026-08-18) observed both
mechanisms live in a major US home-services network's ping/post API:
`final_pricing` sale reports and per-campaign `questions[]` arrays
carried alongside delivery. Both are adoption-visible capabilities, not
theoretical.

## Scope

- **Track A — core protocol (additive MINOR, zero schema change):** the
  open-enum `event` value `FINAL_PRICING` (documented known value;
  `schemas/event.json` is unchanged), a recommended `details` shape with
  normative ingest bounds, capability gating (SPEC.md §8), and the
  settlement-neutrality MUSTs. SPEC text: §4 event guidance, §6
  known-value list, §8 advertisement note.
- **Track B — core protocol (additive MINOR, schema change):** one
  optional `extensions` object on the `lead` and `post` payloads
  (`schemas/lead.json`, `schemas/post.json`), keys constrained to the
  registry namespace format; plus the *registered payload shape*
  `{org}.form.questions` (array of `{question_id?, question_text,
  answer_text}` with bounds and scrub rules) — a registered extension
  per SPEC.md §6/§3 and `governance/EXTENSION-REGISTRY.md`, not a schema
  field. `ping`, `bid`, `ack`, `call`, `event`, and every vertical are
  **unchanged**.
- **Reference implementation:** publisher-side `FINAL_PRICING`
  acceptance + advertisement (Track A); lead/post `extensions`
  passthrough and delivery, form.questions validation at
  support-advertisement boundaries, erasure coverage (Track B).
  Implementation-phase obligations, gated on per-track acceptance.
- **Registry rows:** `lcp.marketplace.final_pricing` and
  `lcp.form.questions` proposed in `governance/EXTENSION-REGISTRY.md`
  with this record (status: *proposed-on-acceptance*).
- **SDK / OpenAPI / MCP / vectors:** change-gated at implementation
  (roadmap §Change gates); listed in the impact section. This Draft
  authorizes none of it.
- **Not in scope:** any change to `post.pricing` or payable/dispute
  semantics; multi-currency price semantics (deferred roadmap item —
  see Deferred questions); `questions` in vertical `attributes` or in
  pings (explicitly forbidden by design); an `extensions` surface on
  `call` payloads (deferred with trigger); a schema mechanism for
  validating namespaced payload shapes (convention + receiver rules
  today, like LEP-0003's report rules); DLP vendor integration (the
  platform MAY scrub with whatever deterministic tooling a deployment
  runs; no normative algorithm is invented here).

## Proposed wire shape

### Track A — `FINAL_PRICING` event (publisher → platform)

A new **documented known value** of the open `event` enumeration. No
schema file changes; `event.json` already stores and passes through any
string (`schemas/event.json:16-19` `$comment`: "No enum constraint —
this is an open enumeration per SPEC.md §6").

Recommended `details` shape (the same SHOULD-style guidance pattern as
`CONVERTED`/`DISPUTED`/`CONTACTED`, SPEC.md §4):

| Field | Type | Notes |
|---|---|---|
| `ping_id` | string | The ping this report describes, as the platform issued it. Optional: a publisher that lost the auction may still know the winning external price. |
| `sale_scope` | string | Open enum, known values `external` (sold outside this platform's auction) and `internal_auction` (sold through it). Unknown values stored and passed through (§6). |
| `winning_price_cents` | integer | Winning price in **integer minor units** of `currency`. MUST be `> 0` and MUST NOT exceed `100000000` (typo guard: 1,000,000 major units); receivers MUST treat larger values as malformed reports. |
| `currency` | string | ISO 4217 alpha-3, uppercase (core `currency` discipline, `schemas/core.json` `$defs/currency`). |
| `duration_seconds` | integer | Contact duration of the winning sale when the sale is call-based; `0` for form sales. MUST be `>= 0` and `<= 86400`. |
| `sold_at` | string (date-time) | When the winning sale occurred. |

Deployment-specific additions ride **namespaced keys inside `details`**
under the registered `{org}.marketplace.final_pricing` namespace (the
roadmap's own candidate name, kept meaningful on this route — see
Registry notes). The event payload's existing `external_reference`
(`schemas/event.json:29-31`) MAY carry the external network's sale
record id.

Normative semantics:

- **Settlement neutrality (the roadmap's conflict clause, made
  operational).** `post.pricing` (SPEC.md §4) remains the sole billing
  and dispute record between platform and buyer. A `FINAL_PRICING`
  report MUST NOT change any payable, refund a charged price, adjust
  `post.pricing` fields, or re-price an in-flight auction. Receivers
  consume it for **analytics and bid refinement only** (the roadmap's
  stated purpose: "so buyers can refine bids"). A report contradicting
  the platform's own auction outcome is not an error — it is the point
  (external sales); disputes about it ride the existing ack/rejection
  and dispute paths, not a new one.
- **Trust model: authenticated-sender attestation, format+bounds
  sanity only** — the LEP-0003 `CONTACTED` model. The platform cannot
  verify what a lead sold for on another network; `FINAL_PRICING`
  values are attestations by the authenticated publisher (`event:submit`
  scope + HMAC signature, SPEC.md §9), scoped to one `lead_id`. Malformed
  or out-of-bound reports are validation rejections, not stores.
- **Keying and replay.** The event's required `lead_id` keys the report;
  `details.ping_id` keys the correction chain. Replay collapses on the
  event `message.id` (the reference platform's existing mechanism:
  same-id-same-bytes → `DUPLICATE`, reuse-with-different-bytes →
  `LCP-005` 409; `router.py:1229-1252`). Corrections are normal: a
  later report for the same `(lead_id, ping_id)` **supersedes** the
  earlier one when its payload `timestamp` is newer (ties: higher
  message `id` lexicographic). Receivers SHOULD keep report history and
  MUST expose the superseding view.
- **PII discipline.** `event` is a non-PII message class (SPEC.md §4
  table — "None (hashes only)"). A `FINAL_PRICING` report carries no
  consumer data at all: the contact is identified by `lead_id` alone;
  phone/email/hash values MUST NOT appear in `details`. Receivers MUST
  reject PII-bearing reports at ingest (see invalid example A-1; honest
  status recorded there — the open `details` cannot express this in
  schema, so it is an ingest-behavior requirement).
- **Capability gating.** A publisher MUST send `FINAL_PRICING` only to
  a platform whose §8 advertisement lists it in `capabilities().events`
  and whose `lcp_versions` contains a parsed `major == 1`,
  `minor >= 1` full-semver element (the normative membership rule of
  SPEC.md §8, landed via LEP-0003 F6/T10). The reference platform
  today: `router.py:1699-1730` advertises thirteen values and
  `["1.0.0", "1.1.0"]` (`router.py:1705`, `router.py:1716-1730`).
  **Zero schema cost, nonzero platform cost (measured path):** the
  publisher-reportable set `_PUBLISHER_EVENT_TYPES`
  (`router.py:76-81`: `REJECTED`, `DISPUTED`, `CONSENT_WITHDRAWN`,
  `ERASURE_REQUEST`) does not contain `FINAL_PRICING`, and the sender
  authorization at `router.py:1219-1226` rejects any other name with
  `LCP-002` ("Sender is not authorized for this lead event").
  Acceptance therefore obligates the implementation PR to add
  `FINAL_PRICING` to `_PUBLISHER_EVENT_TYPES` behind the v1.1 gate and
  advertise it — the exact F5/T6 correction pattern LEP-0003 recorded.
  `FINAL_PRICING` MUST NOT be added to `_EVENT_STATUS_TARGETS`
  (`router.py:67-75`): it asserts no lifecycle transition; a price
  report does not move `status` (see §3 status transition table,
  SPEC.md:260).

### Track B — `extensions` on `lead`/`post`, and the registered `{org}.form.questions` shape

#### B.1 The payload surface (LEP-carried schema change)

Add **one** optional field to `lead` and `post`, identical shape, drafted
as the overlay validated below:

```json
"extensions": {
  "type": "object",
  "description": "Namespaced LCP extensions (SPEC.md section 6;
    governance/EXTENSION-REGISTRY.md). Keys must follow
    {org}.{division}.{purpose}; lcp.core.* is reserved. Unknown
    namespaces are ignored by implementations that do not recognize
    them.",
  "propertyNames": {
    "pattern": "^[a-z0-9][a-z0-9_-]*(\\.[a-z0-9][a-z0-9_-]*){2,}$",
    "not": { "pattern": "^lcp\\.core\\." }
  },
  "additionalProperties": true
}
```

- **Where, and why exactly there.** `lead` (publisher→platform intake)
  and `post` (platform→buyer delivery): the data must ride the
  delivery itself — the buyer needs the questions when it works the
  lead, not in a side-channel notification (the event alternative is
  analyzed and rejected under Alternatives). `call` is deliberately
  excluded at v1.1 (telephony Q&A migration has no adopter evidence
  yet; deferred with trigger). `ping`/`bid`/`ack` stay closed — that is
  the "never ping_safe" guardrail made structural (see B.3).
- **Key shape is expressible and enforced:** the `propertyNames`
  pattern requires ≥3 lowercase segments per the registry format and
  forbids `lcp.core.*` claims per the registry's rejection grounds
  (`governance/EXTENSION-REGISTRY.md`, "Rejection grounds"). Measured:
  `"questions"` and `"lcp.core.form_questions"` `REJ` under the
  overlay; three-segment namespaces `PASS`.
- **Values stay deployment-defined** (`additionalProperties: true` per
  namespace): the core cannot and does not validate namespaced payloads
  — same as `schemas/offer.json:248-252` and `event.details`. Shape
  authority for a registered namespace is this LEP's §B.2 for
  `{org}.form.questions`; receiver-side rules below stand in for the
  schema, and this record states that boundary honestly rather than
  implying a validator that does not exist.
- **Capability gating (mandatory, measured):** `lead`/`post` are closed
  objects, so the §6 "unknown optional fields ignored" guarantee does
  **not** cover this field (SPEC.md:660-666 — closed-object additions
  "rely on version negotiation"). A v1.1 `extensions`-bearing lead sent
  to a receiver advertising only `["1.0.0"]` fails that receiver's
  strict allowlist — measured: `Additional properties are not allowed
  ('extensions' was unexpected)`. Senders gate emission on the
  receiver's parsed `lcp_versions` ≥ 1.1 (§8, LEP-0003 F6 rule); absent
  `extensions` = bit-for-bit v1.0 behavior.
- **The matcher gap closes.** With this surface legal, the
  claim-language evidence `matching.py:193-196` already reads becomes
  sendable; today that code path is only reachable if validation is
  bypassed (it is not — such leads are rejected upstream — so the
  predicate currently fail-closes for schema-validating senders, which
  is the trap this fix removes). No matcher code change is required by
  Track B beyond passing the object through validation.

#### B.2 The registered payload shape `{org}.form.questions`

Normative for any org registering the namespace (reference platform
registers `lcp.form.questions`; other adopters register
`{org}.form.questions` for their own org id):

```json
"extensions": {
  "acme.form.questions": [
    {
      "question_id": "own_or_rent",
      "question_text": "Do you own the property or are you renting?",
      "answer_text": "Own outright"
    }
  ]
}
```

- Array of **at most 25** entries.
- Each entry an object: `question_id` optional, `^[a-z0-9][a-z0-9_-]{0,63}$`;
  `question_text` required string, **≤ 300 characters**;
  `answer_text` required string, **≤ 1000 characters**; **no other keys**
  (strings only — nested objects are out of the registered shape, which
  keeps content scanning tractable).
- Total serialized `extensions` object: receivers SHOULD reject beyond
  **32 KiB** (a deployment MAY set tighter limits, MUST NOT loosen the
  shape bounds above).
- A receiver that has implemented support for a registered
  `form.questions` namespace MUST validate entries against this shape
  and MUST either accept the message or reject it (`LCP-100`) with the
  offending namespace identified — it MUST NOT silently store a
  non-conforming array. Receivers that do not recognize the namespace
  ignore its value (extension forward-compatibility,
  `governance/EXTENSION-REGISTRY.md` "Extension payload location").
  These are receiver rules, not schema rules (B.1's boundary statement).

#### B.3 The guardrails the roadmap set — "post-only, never ping_safe"

1. **Structural:** the field exists only on `lead` and `post`. A ping
   carrying `extensions` is rejected by the unchanged strict allowlist —
   measured `REJ` under both the published and overlay trees (invalid
   example B-3). The auction PII firewall (SPEC.md §4 ping allowlist,
   `LCP-008`) gains nothing to leak.
2. **Normative projection ban:** a platform MUST NOT derive ping
   `attributes`, `compliance_flags`, or any other ping field from
   extension content. Matching against vertical taxonomy uses
   `attributes` only; extensions are content, not routing fuel.
3. **Taxonomy integrity:** publishers MUST NOT encode question text into
   vertical `attributes` fields to smuggle it into pings — the existing
   `ping_safe` tagging and reserved-core-name rules
   (`tools/check_reserved_fields.py`, SPEC.md §3/§12) already police
   that boundary; this LEP adds no exemption. The controlled-taxonomy
   normalization step of `home_services.json:5` is unchanged: questions
   that fit the taxonomy are *also* normalized there; the extension
   carries the verbatim residue, which never pings.
4. **PII discipline inside the extension (the hard review question,
   answered head-on).** Freeform question/answer text can smuggle
   consumer data, and post is already a full-PII message — so the real
   risk axes are:
   - **Duplication defeats lifecycle keys.** Suppression and erasure
     events (`CONSENT_WITHDRAWN`, `ERASURE_REQUEST`, SPEC.md §4) and the
     §9 dedup machinery operate on `phone_hash`/`email_hash` — a phone
     number *inside* `answer_text` is invisible to those flows (unhashed,
     unsearchable by hash, retained past field-scoped deletion in
     deployments that index structured fields). **MUST (publisher):**
     question/answer text MUST NOT contain phone numbers, email
     addresses, street addresses, government identifiers, payment
     credentials, consent tokens or evidence values (those belong in
     `consumer`, `compliance.consent_evidence[]`, or
     `attachments[]`), and MUST NOT duplicate the consumer's name when
     the structured `consumer` block already carries it.
   - **Retention scope.** An `extensions` value is stored lead content:
     it inherits §9 PII retention and MUST be purged by the same
     erasure execution as the rest of the record (platform erasure paths
     delete the stored lead/post record wholesale; this LEP states
     explicitly that `extensions` is in that record's PII scope — free
     text must never live in a side store excluded from erasure).
   - **Egress/abuse volume.** Bounds (B.2) cap the spray surface; a
     32 KiB ceiling per message is auditable and cheap to scan.
   - **Platform MAY scrub/truncate, deterministically.** A platform MAY
     apply a deterministic redaction pass (pattern-based
     contact-identifier redaction) or truncate over-bound values before
     delivering a post; it MUST apply the same policy to every message
     (no per-recipient discretion), MUST log each redaction/truncation
     event against the `lead_id`, and SHOULD reject (rather than
     deliver) content matching credential/token shapes listed above.
     A deployment MAY configure reject-vs-redact policy; neither mode
     may silently drop the whole `extensions` object on a supporting
     receiver. The reviewer question this record pre-empts for the
     dossier: should redaction be a MUST on the platform side (the
     publisher-side scrub is a MUST already)? The Draft keeps platform
     redaction at MAY because the protocol cannot guarantee every
     platform runs DLP before it can refuse the lead without breaking
     delivery in markets where the publisher is compliant; Review
     should test that reasoning.
   - **No new recipient class:** the data crosses exactly the same
     publisher→platform→winning-buyer PII path the lead already uses
     (SPEC.md §4 post is the full-PII channel; §9 transport/auth
     requirements unchanged). This is data-shape guidance inside an
     existing boundary, not a boundary change — but it does move a
     PII-adjacent surface (freeform consumer answers now ride a
     protocol-defined extension), so LEP.md requires the named
     human security/privacy reviewer or the ARB-Security co-sign at
     decision.
5. **Consent/scrub evidence MUST NOT ride `form.questions`.** Answer
   text such as "her consent code is 4471" is a compliance-evidence
   value with a structured home (`consent_evidence[]`); free-text
   evidence is unverifiable and unrevocable. Invalid example B-4 covers
   a contact-identifier case; the same receiver rule covers tokens.

### Registry notes

Two `governance/EXTENSION-REGISTRY.md` rows land with this Draft as
**proposed-on-acceptance** (the reference platform's own registrations;
adopters register their own `{org}.*` namespaces under the open
registration rules, which are format-checked only):

| Namespace | Purpose |
|---|---|
| `lcp.form.questions` | Track B registered shape on `lead`/`post` `extensions`; post-only, never ping_safe. |
| `lcp.marketplace.final_pricing` | Track A deployment-specific additions inside `FINAL_PRICING` event `details`. |

Registry hygiene noticed while drafting (no LEP needed; registration is
open): `matching.py:116-120` and `matching.py:193-196` already consume
`lcp.platform.required_consent_purposes`,
`lcp.platform.contact_purpose`, and `lcp.platform.claim_language`, which
are not yet registry rows (`governance/EXTENSION-REGISTRY.md` ships two
rows). Backfilling those rows is a documentation PR following this LEP.

### Valid example — Track A, `FINAL_PRICING` on the published tree

*(Validated by execution 2026-11-03 against the published v1.0/current
tree with the reference platform's `SchemaValidator` (`jsonschema
4.26.0`): `PASS`. No overlay exists for Track A — that PASS on the
unmodified tree is the zero-schema-cost claim in action. The example
mirrors the repository's `examples/lead.json`/`examples/post.json`
fixture: `pub_123` owns `lead_abc123`, which cleared `post` at 2200 AUD
cents; the publisher now reports the lead actually sold externally for
2450 AUD cents — `post.pricing` remains the billing record and is
untouched by the report.)*

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "f10e8400-0000-4000-8000-000000000601",
      "type": "event",
      "timestamp": "2026-11-03T10:00:00Z",
      "sender_id": "pub_123",
      "receiver_id": "platform_001",
      "correlation_id": "880e8400-e29b-41d4-a716-446655440003",
      "idempotency_key": "pub-123-event-lead_abc123-finalpricing-1",
      "test": true
    },
    "payload": {
      "lead_id": "lead_abc123",
      "event": "FINAL_PRICING",
      "timestamp": "2026-11-03T09:59:41Z",
      "details": {
        "ping_id": "ping_550e8400-1",
        "sale_scope": "external",
        "winning_price_cents": 2450,
        "currency": "AUD",
        "duration_seconds": 0,
        "sold_at": "2026-11-02T23:40:00Z",
        "acme.marketplace.final_pricing": {
          "source_network": "legacy_hs_network_a",
          "sale_record_id": "hsn-88117"
        }
      },
      "external_reference": "hsn-88117"
    }
  }
}
```

### Valid lead example (overlay) — Track B, lead with `extensions`

*(Validated by execution 2026-11-03: `PASS` under the draft overlay
(`lead.json`/`post.json` gain exactly the B.1 field), `REJ` under the
published tree with "Additional properties are not allowed
('extensions' was unexpected)" — the published-tree REJ is Track B's
problem statement: the surface does not exist today. The lead body is
the repository's `examples/lead.json` fixture verbatim (mortgage
vertical), with `extensions` added; the claim-language key is the exact
namespace `matching.py:193-196` already reads, showing the gap this LEP
closes.)*

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "b20e8400-0000-4000-8000-000000000611",
      "type": "lead",
      "timestamp": "2026-11-03T10:00:00Z",
      "sender_id": "pub_123",
      "receiver_id": "platform_001",
      "correlation_id": null,
      "idempotency_key": "pub-lead-20261103-ext-001",
      "test": true
    },
    "payload": {
      "lead_id": "lead_abc123",
      "external_id": "pub-internal-456",
      "submitted_at": "2026-08-15T10:19:50Z",
      "status": "NEW",
      "channel": "form",
      "consumer": {
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane.smith@example.com",
        "phone": "+61412345678",
        "locale": "en-AU"
      },
      "location": {
        "country_code": "AU",
        "state_region": "NSW",
        "postal_code": "2000",
        "city": "Sydney",
        "timezone": "Australia/Sydney"
      },
      "compliance": {
        "consent_timestamp": "2026-08-15T10:19:45Z",
        "consent_source_url": "https://publisher.example.com/landing/mortgage",
        "consent_text_version": "v2.1",
        "consent_purposes": ["calls", "sms", "email", "share_with_partners"],
        "consent_evidence": [
          {
            "type": "verified_consent",
            "provider": "internal",
            "token_or_url": "evd_abc123def456"
          }
        ],
        "otp_verified": true,
        "dnc_checked": true,
        "scrubs": [
          { "type": "dnc_national", "provider": "au_dncregister", "result": "clean", "checked_at": "2026-08-15T10:19:48Z" },
          { "type": "litigator", "provider": "litigator_list", "result": "clean", "checked_at": "2026-08-15T10:19:49Z" },
          { "type": "blacklist", "provider": "internal", "result": "clean", "checked_at": "2026-08-15T10:19:49Z" }
        ]
      },
      "provenance": {
        "received_at": "2026-08-15T10:20:01Z",
        "source_type": "publisher",
        "source_id": "pub_123",
        "source_url": "https://publisher.example.com/landing/mortgage",
        "ip_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        "user_agent_hash": "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a1f6e5",
        "campaign_id": "camp_mortgage_nsw_2026",
        "creative_id": "cre_001",
        "landing_page_url": "https://publisher.example.com/landing/mortgage"
      },
      "attributes": {
        "vertical": "mortgage",
        "schema_version": "1.0.0",
        "loan_type": "refinance",
        "loan_amount_cents": 65000000,
        "loan_amount_band": "500k_750k",
        "property_type": "single_family",
        "property_value_cents": 88000000,
        "property_value_band": "800k_1m",
        "credit_rating_band": "excellent",
        "employment_status": "employed",
        "annual_income_cents": 12000000,
        "annual_income_band": "100k_150k",
        "ltv_ratio_band": "70_80",
        "is_first_home_buyer": false,
        "has_existing_mortgage": true
      },
      "exclusivity": {
        "exclusivity": "shared",
        "max_buyers": 3
      },
      "expiry": {
        "expires_at": "2026-08-15T10:50:00Z"
      },
      "extensions": {
        "lcp.platform.claim_language": "none",
        "acme.form.questions": [
          {
            "question_id": "own_or_rent",
            "question_text": "Do you own the property or are you renting?",
            "answer_text": "Own outright"
          },
          {
            "question_id": "settle_timeline",
            "question_text": "How soon would you like to settle the refinance?",
            "answer_text": "Within 60 days"
          }
        ]
      }
    }
  }
}
```

### Valid post example (overlay) — Track B, post delivering the extension

*(Validated by execution 2026-11-03: `PASS` under the overlay, `REJ`
under the published tree — same rule, same reason. The post body is the
repository's `examples/post.json` fixture with `extensions` appended and
the envelope re-keyed; byte-exact as executed, including the fixture's
unchanged blocks.)*

```json
{
  "lcp": {
    "version": "1.1.0",
    "message": {
      "id": "b30e8400-0000-4000-8000-000000000612",
      "type": "post",
      "timestamp": "2026-11-03T10:00:00Z",
      "sender_id": "platform_001",
      "receiver_id": "buyer_001",
      "correlation_id": "b20e8400-0000-4000-8000-000000000611",
      "idempotency_key": "platform-001-post-lead_abc123-ext-1",
      "test": true
    },
    "payload": {
      "lead_id": "lead_abc123",
      "delivered_at": "2026-08-15T10:20:10Z",
      "submitted_at": "2026-08-15T10:19:50Z",
      "price_cents": 2200,
      "currency": "AUD",
      "buyer_id": "buyer_001",
      "buyer_reference": "buyer-internal-987",
      "pricing": {
        "floor_price_cents": 1500,
        "premium_cents": 700,
        "final_price_cents": 2200,
        "uncapped_price_cents": 2500,
        "max_allowed_price_cents": 3000,
        "price_guardrails": true,
        "is_duplicate_resub": false,
        "payable_definition": "form_lead_valid_phone",
        "payable_status": "payable"
      },
      "matched_preferences": {
        "states": [
          "NSW"
        ],
        "loan_types": [
          "refinance",
          "cash_out_refinance"
        ],
        "credit_bands": [
          "excellent",
          "good"
        ]
      },
      "consumer": {
        "first_name": "Jane",
        "last_name": "Smith",
        "email": "jane.smith@example.com",
        "phone": "+61412345678",
        "locale": "en-AU"
      },
      "location": {
        "country_code": "AU",
        "state_region": "NSW",
        "postal_code": "2000",
        "city": "Sydney",
        "timezone": "Australia/Sydney"
      },
      "compliance": {
        "consent_timestamp": "2026-08-15T10:19:45Z",
        "consent_source_url": "https://publisher.example.com/landing/mortgage",
        "consent_text_version": "v2.1",
        "consent_purposes": [
          "calls",
          "sms",
          "email",
          "share_with_partners"
        ],
        "consent_evidence": [
          {
            "type": "verified_consent",
            "provider": "internal",
            "token_or_url": "evd_abc123def456"
          }
        ],
        "otp_verified": true,
        "dnc_checked": true
      },
      "attributes": {
        "vertical": "mortgage",
        "schema_version": "1.0.0",
        "loan_type": "refinance",
        "loan_amount_cents": 65000000,
        "loan_amount_band": "500k_750k",
        "property_type": "single_family",
        "property_value_cents": 88000000,
        "property_value_band": "800k_1m",
        "credit_rating_band": "excellent",
        "employment_status": "employed",
        "annual_income_cents": 12000000,
        "annual_income_band": "100k_150k",
        "ltv_ratio_band": "70_80",
        "is_first_home_buyer": false,
        "has_existing_mortgage": true
      },
      "provenance": {
        "received_at": "2026-08-15T10:20:01Z",
        "source_type": "publisher",
        "source_id": "pub_123",
        "source_url": "https://publisher.example.com/landing/mortgage",
        "campaign_id": "camp_mortgage_nsw_2026",
        "creative_id": "cre_001",
        "landing_page_url": "https://publisher.example.com/landing/mortgage"
      },
      "exclusivity": {
        "exclusivity": "shared",
        "max_buyers": 3
      },
      "extensions": {
        "acme.form.questions": [
          {
            "question_id": "own_or_rent",
            "question_text": "Do you own the property or are you renting?",
            "answer_text": "Own outright"
          },
          {
            "question_id": "settle_timeline",
            "question_text": "How soon would you like to settle the refinance?",
            "answer_text": "Within 60 days"
          }
        ]
      }
    }
  }
}
```

### Invalid example A-1 — plaintext phone in `FINAL_PRICING` details

```json
"details": {
  "ping_id": "ping_550e8400-1",
  "sale_scope": "external",
  "winning_price_cents": 2450,
  "currency": "AUD",
  "duration_seconds": 0,
  "sold_at": "2026-11-02T23:40:00Z",
  "consumer_phone": "+61412345678"
}
```

Violates the event class's PII rule (SPEC.md §4: `event` — "None (hashes
only)") and this record's ingest MUST: receivers MUST reject
`FINAL_PRICING` reports whose `details` contain contact-identifier
values (phone/email forms, digits strings matching the §9 preimage
normalization inputs). **Honest status of "reject":** the shape validates —
measured `PASS` on the published tree, because `details` is
`additionalProperties: true` (`schemas/event.json:25-28`) and no schema
change reaches into it. Same boundary as LEP-0003 §4's report rules:
this is a receiver ingest requirement, not a schema rule, and the
implementation PR must land the matching platform test (named under
Conformance impact) rather than relying on `event.json`.

### Invalid example A-2 — float dollars, missing currency

```json
"details": {
  "ping_id": "ping_550e8400-1",
  "sale_scope": "external",
  "winning_price_cents": 24.50,
  "duration_seconds": 0,
  "sold_at": "2026-11-02T23:40:00Z"
}
```

Violates the units discipline — integer minor units, `currency`
REQUIRED on every price-bearing fact (core `$defs/currency`; §4 `post`
requires `currency` alongside `price_cents`) — and the bounds rules
(`winning_price_cents` MUST be an integer > 0). The reference network's
decimal-dollar price field is the migration trap this exists to refuse:
minor units only, per-pair currency explicit, NEVER a bare float. Also
measured `PASS` under both trees for the same honest reason as A-1
(open `details`); ingest MUST rejects.

### Invalid example A-3 — `FINAL_PRICING` to a v1.0-only platform

`GET /v1/lcp/capabilities` returns `"lcp_versions": ["1.0.0"]` and no
`FINAL_PRICING` in `events`, yet `pub_123` submits the valid example
above anyway. The event's shape passes the receiver's schema (the value
is an open enum) — but the platform has no price-report store, so the
report degrades to store-and-pass-through with zero analytics effect;
worse, a platform that added `FINAL_PRICING` to its status-transition
map would move the lead's lifecycle on a price report, which this LEP
forbids. The §8 capability gate exists to keep reports from landing in
silence; violating it is a publisher-conformance failure (same invalid-D
class as LEP-0003).

### Invalid example B-1 — un-namespaced key `"questions"`

```json
"extensions": { "questions": [ { "question_text": "…", "answer_text": "…" } ] }
```

`REJ` under the overlay: `'questions' does not match
'^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*){2,}$'` — extension keys
must be `{org}.{division}.{purpose}` namespaced (`lcp.core.*` also
forbidden; measured: `"lcp.core.form_questions"` `REJ`). Registration
stays self-service but name-collision-free by construction; the wire
enforces the registry's format and rejection grounds
(`governance/EXTENSION-REGISTRY.md`).

### Invalid example B-2 — contact identifiers in `answer_text`

```json
"extensions": {
  "acme.form.questions": [
    {
      "question_id": "callback",
      "question_text": "Best number and address for the crew?",
      "answer_text": "Call Jane at 0412 345 678, 12 Smith St, Sydney NSW 2000"
    }
  ]
}
```

Schema-validates (measured `PASS` under the overlay — the open value
space cannot police free text, stated boundary B.1) but violates this
record's publisher MUST (B.3.4): phone number, street address, and
consumer-name duplication invisible to hash-keyed suppression/erasure
flows. A conformant publisher routes those facts into `consumer` /
`location` (already present on this very lead — the answer is a stale
duplicate by construction) and either drops the question or rewrites
the answer ("side gate access preferred"). A platform running the B.3.4
MAY-redaction delivers `Call Jane at [REDACTED], [REDACTED], Sydney NSW
2000`-style output with a logged redaction event; a reject-policy
platform refuses the lead (`LCP-100`, `field:
extensions.acme.form.questions[0].answer_text`). This is the negative
example the privacy review centers on: the guardrail is normative text
+ receiver behavior, exactly as `free_text` already works inside the
vertical today (`verticals/home_services.json:103`, `ping_safe: false`).

### Invalid example B-3 — `extensions` on a ping

```json
"extensions": { "acme.form.questions": [ "…" ] }
```

appended to the repository's `examples/ping.json`. `REJ` under **both**
the published tree and the overlay (`Additional properties are not
allowed ('extensions' was unexpected)`, `schemas/ping.json:206`) —
because Track B deliberately does not touch the ping allowlist. The
roadmap's "never ping_safe" guardrail is structural, not advisory.
Corollary (B.3.2): deriving a ping field *from* extension content is
equally refused by the existing `ping_safe` runner (`LCP-008`).

### Invalid example B-4 — over-bound registered shape

A 40-entry `acme.form.questions` array with 1200-character answers (the
kind of shape a legacy `questions[]` blob arrives with). Measured
`PASS` even under the overlay — core schemas do not validate namespaced
values (B.1 boundary, stated twice). The registered shape makes it a
receiver-conformance failure for orgs advertising `form.questions`
support: they MUST `REJ` per B.2. Deployments wanting schema-enforced
bound validation for registered shapes is Deferred question D3 with its
trigger.

### Validation runs (executed 2026-11-03)

Every example in this section was run through the reference platform's
own `SchemaValidator` (`lcp_platform.validation.SchemaValidator`,
`jsonschema 4.26.0`) against the **published** tree (run at `5d6000d`;
re-executed after this lane's rebase onto the PR base `ace8861` with
byte-identical results — the ten commits between the two bases touch no
schema or vertical file (`git diff --stat` on `schemas/` and `verticals/`
is empty across the range)) and a **draft overlay** carrying exactly the B.1
`extensions` object on
`lead.json`/`post.json` (Track A needs no overlay — that is its claim).
Full commands, outputs, and the byte-exact example files are recorded on
the draft PR. Results:

| # | Case | Published tree | Draft overlay |
|---|---|---|---|
| A-valid | `FINAL_PRICING` event | **PASS** (zero-schema route proven) | PASS |
| A-invalid-I | plaintext phone in `details` | PASS — open `details` cannot catch (ingest MUST governs) | PASS |
| A-invalid-II | float price, missing currency | PASS — same boundary (units rule governs) | PASS |
| B-valid-1 | lead + `extensions` (`lcp.platform.claim_language`, `acme.form.questions`) | **REJ** — `Additional properties are not allowed ('extensions' was unexpected)` at (root): the matcher/schema contradiction of the Problem section, measured | **PASS** |
| B-valid-2 | post + `extensions` | **REJ** (same rule; the v1.0 receiver boundary the capability gate defends) | **PASS** |
| B-invalid-I | un-namespaced key `"questions"` | REJ | **REJ** — `propertyNames` pattern |
| B-invalid-II | `"lcp.core.form_questions"` | REJ | **REJ** — `lcp.core.*` forbidden |
| B-invalid-III | `extensions` on a ping | REJ | **REJ** — ping allowlist untouched (structural never-ping_safe) |
| B-invalid-IV | contact identifiers in `answer_text` | REJ | PASS — stated boundary; publisher MUST + receiver rules (B.3.4) |
| B-invalid-V | 40-entry / 1200-char `form.questions` | REJ | PASS — stated boundary; registered-shape receiver rejection (B.2) |

The PASS rows under "invalid" headings are not mistakes: they are the
honest measurement of what the open surfaces (`event.details`,
namespaced extension values) cannot enforce in schema, mirroring
LEP-0003 §4's "the report rule cannot be drafted; state what it is
instead" discipline — every such row has the matching normative
receiver rule and named implementation-phase test in the impact section.

The two overlay-only headings deliberately read `### Valid lead/post
example (overlay)`, not `### Valid example …`: the T1 gate
(`tools/tests/test_lep_wire_examples.py`) validates every
`### Valid example` block against the **published** tree — where Track B
examples must (and do) fail — so the battery above, not that gate, is
their recorded status; the Track A heading keeps the T1 form because its
envelope passes the published tree unmodified, which is the claim being
asserted.

## Privacy, security, and universal-core audit

**Track A — privacy.** No consumer data on the wire: `event` is a non-PII
message class (SPEC.md §4 table), the report is keyed by `lead_id` +
`details.ping_id` only, and PII-shaped `details` values are ingest
rejections (A-1; the receiver rule that the open schema cannot express).
The report references only parties who already know the ping happened
(the publisher and the platform). Commercial-sensitivity note: a winning
price from a third-party network is the publisher's data to share and
the platform's to hold — retention follows the §9 PII policy already
applied to lifecycle events; a deployment MAY scope reports to the
analytics store's own retention.

**Track A — security/abuse.** Forged or poisoned price reports are the
abuse axis: blast radius is bid-refinement analytics, never settlement
(the settlement-neutrality MUSTs), under the authenticated-sender
attestation model (`event:submit` scope + HMAC per SPEC.md §9) — the
same "unverifiable-by-design" honesty LEP-0003 recorded for contact
reports. Bounds (integer minor units, ≤ 10^8, `duration_seconds` ≤
86400, required ISO-4217) refuse typo/fuzz payloads; correction chains
are keyed, monotone-expire, and auditable (`router.py:1229-1252` event
dedup unchanged). Replay: same-id collapse + `LCP-005` id-reuse
rejection, inherited mechanisms. Rate limits apply per §9.

**Track B — privacy.** The extension rides the existing full-PII
delivery channel (post) under unchanged §9 transport/auth/retention; the
delta risk is unstructured duplication of contact data — analyzed with
its three axes (lifecycle-key evasion, retention scope, egress volume)
and its mitigations (publisher scrub MUST, deterministic platform
redaction/truncation MAY with per-lead logging, shape+size bounds,
erasure purge-scope statement, credential/token ban) in B.3.4. The
never-ping_safe guardrail is structural (B.3.1, measured). No new
recipient class, no new transport, no hash-preimage change (per-pair
hashing discipline untouched; this LEP ships no new hash semantics).
Per LEP.md, a decision on this track requires the named human
security/privacy reviewer or the ARB-Security co-sign; agent/ARB lanes
certify spec consistency, not privacy fitness — the D7 DLP MUST-vs-MAY
question is explicitly routed to that reviewer rather than assumed
settled.

**Track B — security/abuse.** Size bounds cap storage/scan DoS; the
`propertyNames` pattern refuses misleading un-namespaced and
`lcp.core.*`-claimed keys at the schema boundary (measured);
value-content policing is receiver behavior against the registered
shape (stated boundary, tests named). `offer_extensions.py` precedent
applies to matching only: implementations MUST NOT execute extension
values as code (the offer surface's own `$comment` rule extends to
lead/post `extensions` by this record: values are data, no predicate or
expression semantics).

**Universal-core audit (SPEC.md §12), per item on the delta:**

- No vertical-specific or market-specific fields in core — Track B's
  field is shape-neutral (`extensions`, namespaced keys, values
  deployment-defined); `form.questions` carries whatever a form asked,
  not vertical semantics; Track A adds no field at all.
- No market-specific fields including named `compliance` sub-fields:
  untouched; consent questions that arrive as answers route to
  `consent_evidence[]` (B.3.4 credential/evidence ban).
- Consumer name-splitting / gender / phone / call / agent items: not
  touched (no `consumer` or vertical changes).
- Ping strict-allowlist: unchanged and load-bearing (B-invalid-III).
- `ping_safe` tagging and reserved-core-name rules: no vertical edits;
  `tools/check_reserved_fields.py` still green (the new payload field
  name `extensions` exists nowhere in `core.json`'s derived reserved
  set — checker run recorded on this PR).
- Status transition table, capabilities shape, open/closed enum policy:
  unchanged; Track A rides the already-open `event` row of §6.
- Legacy production wire contract fields map 1:1 into LCP: the roadmap
  comparison's `final_pricing` and `questions[]` shapes map to Track A
  details and Track B entries respectively (migration table in the
  rollout section) — which is the §12 migration story this LEP
  completes for Phase 6's second and third findings.

## Compatibility, versioning, and deprecation impact

- **Additive MINOR to v1.1.0** (SPEC.md §6): one new documented known
  value on an open enum + recommended `details` guidance (Track A); one
  new optional field each on `lead.json` and `post.json` (Track B). No
  renames, no removals, no semantic change to any existing field; the
  closed `message.type` enum (SPEC.md:675) is untouched — this LEP adds
  **zero new message types**, so the LEP.md two-adopters/necessity gate
  for new universal message types is never engaged.
- **v1.0 bit-compatibility:** `extensions` absent = identical v1.0
  validation and routing (Track B); `FINAL_PRICING` unknown = stored and
  passed through by any §6-conformant receiver, and never triggers a
  status transition (`router.py:67-75` unchanged; §3 transition table,
  SPEC.md:260).
- **Capability-gated directionality (normative):** Track A gated on the
  receiver's `events` advertisement + parsed `lcp_versions` ≥ 1.1 (§8);
  Track B gated on parsed `lcp_versions` ≥ 1.1 in the **sending
  direction toward a closed-object receiver** — the §6 closed-object
  rule means an unvalidated v1.0 receiver breaks, and the measured `REJ`
  (valid example B, published tree) is the boundary the gate defends.
  Both gates reuse the LEP-0003 F6/T10 membership rule verbatim; no new
  matching mechanics.
- **Simplicity budget (AGENTS.md rule 2):** Track A adds one documented
  vocabulary entry and a six-field recommended table — same footprint as
  the `CONVERTED` guidance it mirrors. Track B adds one field whose
  *concept* SPEC.md §6/§3 and `governance/EXTENSION-REGISTRY.md` already
  document platform-wide; the change deletes an inconsistency (the
  protocol's stated extension mechanism had no lead/post surface). Core
  remains comprehensible in two documents.
- **Universal audit (SPEC.md §12):** executed item-by-item in the
  Privacy, security, and universal-core audit section above; no core
  field names shadowed, no ping allowlist change, no enum-policy change.
- **Deprecation:** none introduced; N+2/2-year windows untouched.
- **Rollback safety:** stop sending → every receiver reverts to its
  pre-LEP behavior bit-for-bit (both tracks are sender-side opt-ins).

## SDK, OpenAPI, MCP, conformance, and operational impact

Change-gated at implementation per LEP.md traceability (Draft authorizes
none of it). Per-surface impact:

- **Schemas.** Track A: none (`event.json` unchanged — zero-schema
  claim, verified). Track B: `schemas/lead.json` + `schemas/post.json`
  gain the B.1 object; regenerated Draft 2020-12 bundles and SHA-256
  manifests per AGENTS.md rule 6 — the SDK-sync gate
  (`tools/check_sdk_schema_sync.py`) then REQUIRES every shipped SDK's
  generated typed models, runtime bundle, and manifest to be
  regenerated together (`implementations/sdk/`: csharp, go, java,
  kotlin, php, python, ruby, rust, swift, typescript — all derive from
  these schemas; nothing may be hand-patched around the gate).
- **OpenAPI.** `api/lcp-openapi.yaml`: the `/events` endpoint and
  `submitEvent` operation (api/lcp-openapi.yaml:175-180) are unchanged
  for Track A (event `details` is already an open object; the
  capabilities `events` array schema (:327, :835) needs no change —
  contents, not contract). Track B: lead/post request bodies gain
  `extensions` alongside the schema change.
- **MCP.** The adapter exposes seven tools (`submit_lead`,
  `submit_call`, `query_lead_status`, `get_schema`, `get_capabilities`,
  `list_offers`, `submit_bid` — `implementations/mcp-server/lcp_mcp_server/server.py:80-160`);
  there is **no event-submission tool**, so Track A is REST-only until
  an `submit_event` MCP tool lands (tracked as its own gap — MCP parity,
  not a Track A blocker). Track B's `submit_lead` tool needs no schema
  change until the lead schema lands; then the adapter's schema loader
  passes the extension object through transparently.
- **Reference platform (implementation-phase obligations, named):**
  - Track A: add `FINAL_PRICING` to `_PUBLISHER_EVENT_TYPES`
    (`router.py:76-81`) gated on v1.1; advertise it in
    `capabilities().events` (`router.py:1716-1730`); price-report ingest
    (bounds + supersedes chain + PII rejection per A-1); store reports;
    MUST NOT touch `_EVENT_STATUS_TARGETS` (`router.py:67-75`).
  - Track B: accept `extensions` on lead intake, carry it in delivery
    posts, apply configured redaction policy with per-lead logging,
    include extension bytes in erasure purge scope (B.3.4).
- **Conformance vectors / test vectors.** Track A: accept vector
  (`event-final-pricing.json`) added to `test-vectors/` (L2/L3) and the
  SDK validation corpus (`test-vectors/sdk/validation-corpus.json`);
  no *schema* negative is expressible (open `details`) — the runner's
  open-object boundary is recorded honestly (LEP-0003 O3 precedent:
  "ship real tests, not fake vectors"), so A-1/A-2 land as **platform
  ingest tests**
  (`plat: test_final_pricing_rejects_pii_report`,
  `plat: test_final_pricing_bounds_reject`,
  `plat: test_final_pricing_supersedes_by_timestamp`) rather than schema
  vectors, plus a capability-gate test
  (`plat: test_publisher_final_pricing_accepted_and_advertised` —
  scoped-publisher credential path, mirroring T6). Track B: lead/post
  `extensions` accept vectors in the SDK validation corpus
  (`test-vectors/sdk/validation-corpus.json`, same entry form
  `test-vectors/sdk/contact-hash.json` shipped for LEP-0003); negatives
  `lcp-neg-extensions-unnamespaced-001`, `lcp-neg-extensions-lcpcore-001`
  (B-1), `lcp-neg-extensions-on-ping-001` (B-3); published-tree
  cross-checks (B-2/B-4 recorded as runner behavior tests, not schema
  vectors). Docs/SPEC-COVERAGE rows follow at implementation.
- **Observability:** counters + audit events named in the rollout
  section (report volume/malformed-reject/PII-reject; extension
  redaction events with per-lead log lines).
- **SDKs:** Track B adds optional `extensions` to the generated lead/post
  model classes; Track A needs none (`details` is a free map in every
  generated SDK today — verify at implementation; the `event` model is
  unchanged).

## Alternatives considered

**Track A:**

- *LEP-carried payload change (a `final_pricing` field on an existing
  message or a new report message).* Rejected for Track A on the
  roadmap's own analysis axes: no existing closed message is the
  natural carrier (a field on `post` would invert the direction —
  `post` is platform→buyer), and a new message type fails the LEP.md
  two-adopters/necessity decision rule with an open-enum surface
  already purpose-built for lifecycle-value facts (`CONVERTED` precedent,
  §3/§4). The zero-schema route lands the mechanism with zero v1.0
  compatibility surface: the value passes through unhandled rather than
  hard-failing. The payload `extensions` surface this Draft adds for
  Track B would *also* have worked post-Track-B — but Track A's
  independence (it needs nothing Track B adds) keeps the tracks
  decidable in either order, which is the point of the separation rule.
- *Namespaced event value instead of a documented known value*
  (`acme.marketplace.final_pricing` as the `event` string itself).
  Rejected: a universal lifecycle vocabulary is what makes the signal
  readable across publisher→platform pairs without org-by-org
  vocabulary discovery, and §6 explicitly prices that as a MINOR on the
  open enum; the roadmap's extension name is preserved *inside* the
  details (where deployment specifics belong).
- *Putting the price into `post.pricing` (extending the settlement
  block).* Rejected — direct roadmap conflict-clause violation: the
  auction price and an attested external price are different facts with
  different authority; co-locating them would let an unverifiable
  attestation touch billing.
- *Keeping final-pricing deployment-specific (no LEP).* Available until
  acceptance and legitimate as a private route (registry is open), but
  rejected as the destination: the mechanism's value is cross-platform
  bid refinement, which vendor vocabularies cannot standardize; the
  roadmap lists it for adoption ("the mechanism, not the implementation
  details").

**Track B:**

- *Event-channel for questions (zero payload change).* Rejected on
  delivery semantics: questions are part of the delivered lead content;
  a post must be self-contained. Replaying questions as post-delivery
  events adds ordering (buyer must join two surfaces before working the
  lead), completeness (a dropped event is silently missing job context),
  and a second ingest path for the same data — the "partial delivery"
  class LCP's post design avoids. Also rejected on precedent: `lead`/
  `call` carry content blocks directly; events carry notifications.
- *Vertical `attributes` home (extend `home_services` etc.).* Rejected
  by design constraint and by the vertical's own text
  (`home_services.json:5`): the controlled taxonomy is the matching
  substrate; unbounded per-brand question keys would break the taxonomy
  and re-open the §12/§3 reserved-name and `ping_safe` audit surface
  with every campaign.
- *Lean on `free_text` (`home_services.json:103`) alone.* Rejected:
  structure-free single string loses question/answer pairing and defeats
  the shape bounds + receiver validation this LEP defines; it remains
  available as today's informal practice, which the roadmap asks to
  replace with an "approved post-only extension."
- *`extensions` on every message type at once (lead/post/call/event/ack).*
  Rejected as scope pressure: `ping`/`bid`/`ack` must stay closed
  (firewall/response-minimalism); `call` lacks adopter evidence
  (Deferred question D1); `event.details` is already open (no field
  needed).
- *Keeping questions deployment-specific (status quo + source archive).*
  Rejected with evidence: the source archive helps nobody at delivery
  time, and the matcher/schema contradiction (Problem section) means a
  deployment-private extension is *illegal on the published schemas
  today* — the status quo is not a neutral option, it is a broken one.

**Both tracks:** the umbrella was kept to one LEP because LEP.md allows
umbrella discussions sharing the migration story while mandating
independent decidability; splitting into two numbers was considered and
would be acceptable — the maintainers may renumber at Review without
restarting the discussion if the ARB prefers that.

## Rollout, migration, observability, and rollback

Per-track, implementation gated on that track's acceptance:

1. **Track A first step is prose-only:** SPEC §4 guidance table + §6
   known-value + the registry row, landing in the wire-docs PR; the
   platform PR adds `_PUBLISHER_EVENT_TYPES`, advertisement, ingest, and
   the named tests. Publishers adopt when their platform advertises.
2. **Track B order:** schemas + examples + vectors in one wire PR (the
   overlay in this record becomes the diff verbatim — validated);
   generated bundles + manifests + SDK regen (check_sdk_schema_sync
   gate); platform passthrough + redaction policy + erasure coverage;
   SPEC §3/§4/§6 extension-location text (`payload.extensions` gains
   its documented message list); then publishers/buyers gate on
   capability.
3. **Migration mapping for legacy adopters** (the roadmap's migration
   story): legacy `final_pricing` winning-amount → `winning_price_cents`
   (multiply decimals by minor-unit factor, NEVER copy floats; the
   reference network's `$` decimal shape is invalid example A-2 by
   construction); call-sale `duration` → `duration_seconds`; network
   sale id → `external_reference`; per-campaign `questions[]` →
   `acme.form.questions` entries (truncation + scrub MUST run during
   import, not after — legacy blobs exceed the bounds; B-4 is the shape
   such imports produce unscrubbed).
4. **Observability:** counters `lcp_final_pricing_reports_total` (labels:
   scope, disposition ∈ {stored, superseded, rejected_malformed,
   rejected_pii}); `lcp_redaction_events_total` (label: policy ∈
   {reject, redact}); malformed/PII reports MUST be logged and counted
   but never stored as reports (they enter the audit trail only).
5. **Rollback:** stop sending (both tracks are sender-side opt-ins);
   platform feature-flag the `FINAL_PRICING` ingestion and the
   `extensions` intake surface; no data migration either direction; the
   capability advertisement (`events` list, `lcp_versions`) is the kill
   switch at the §8 boundary.

## Deferred questions

- **D1 — `call` payload `extensions`.** Telephony intake has
  question/answer analogues (IVR keys, agent-asked questions;
  `schemas/call.json:148` already says "platform-specific IVR data goes
  in extensions" while `call.json` ships no `extensions` field — the
  same promised-destination gap as Track B). **Trigger:** two
  independent adopters migrating call-center Q&A, or an IVR
  attestation requirement; then add `call` to the B.1 surface (a pure
  copy of the object, no new shape).
- **D2 — Multi-currency price reports.** `winning_price_cents` is an
  integer in a REQUIRED `currency`; cross-currency comparison is the
  roadmap's deferred "Multi-currency auction semantics" item.
  **Trigger:** any auction where bids and final-pricing reports can be
  denominated differently (a platform operating two currencies); until
  then reports MUST use the auction currency when `sale_scope` is
  `internal_auction` and publishers SHOULD report the currency the
  external sale settled in (no FX guessing in the platform).
- **D3 — Schema-enforceable validation of registered shapes.** Today
  namespaced values are validated only by receiver conformance to the
  registry spec (B.2; B-4 is `PASS` in the schema by design).
  **Trigger:** a third registered shape with bounds worth a shared
  mechanism (a `$defs` + registry-metadata scheme), or one incident of
  over-bound delivery causing harm.
- **D4 — Signed external-sale proof.** `FINAL_PRICING` is
  attestation-grade (trust model); signed third-party proofs (e.g. a
  network-issued sale receipt) are out of scope. **Trigger:** adopters
  paying real money on these signals or dispute flows demanding proof.
- **D5 — Payable/dispute interaction.** A report revealing a higher
  external price could tempt refund claims on lower auction prices.
  Settlement neutrality (Track A MUSTs) says no today; a market that
  wants price-guarantee clauses needs a commercial policy, not a wire
  change. **Trigger:** two independent adopters' contracts asserting
  such claims.
- **D6 — Per-namespace capability advertisement.** §8 `events` is a
  list of event values; there is no "extensions supported / which
  namespaces policy applies" surface (redaction policy varies by
  platform). **Trigger:** interop friction when one platform redacts and
  another rejects.
- **D7 — DLP MUST vs MAY (B.3.4).** If the platform-side redaction is
  elevated from MAY to a universal MUST, the platform must be able to
  refuse non-conforming content — a delivery-availability tradeoff the
  named privacy reviewer should adjudicate. **Trigger:** this LEP's
  Review window (decision item, not an after-acceptance surprise).

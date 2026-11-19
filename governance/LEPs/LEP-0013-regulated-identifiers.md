# LEP-0013 — Regulated identifiers: a gated lane for government ID and bank account data

- **Title:** Regulated identifiers: a capability-gated, never-persisted lane for government ID and bank account fields in allowlisted lending verticals
- **Author(s):** LCP maintainers (draft from SpearPointX adopter evidence)
- **Date:** 2026-11-19
- **Status:** **Implemented** — shipped in v1.3.0 (PR
  [#249](https://github.com/SpearSystems/LCP/pull/249), released
  2026-11-19); Accepted 2026-11-19 (maintainer;
  [v1.3 decision record](../../docs/DECISIONS-v1.3.md) D1–D4: review
  window and independent review waived, maintainer security/privacy
  sign-off under D3).
- **Target version:** v1.3.0 (additive MINOR: one schema annotation
  keyword, one capability feature, and a bounded exception to the
  `prohibited` list. No class is added, so the change is not MAJOR under
  LEP-0011.)
- **Discussion:** [#255](https://github.com/SpearSystems/LCP/issues/255)
- **Reviewers needed at Review:** **a named human security/privacy reviewer
  is required.** This record relaxes a PII boundary, and LEP-0011 requires
  an LEP with a security/privacy co-sign for any less-protective class
  change. Legal review by the adopting deployment (GLBA safeguards, state
  privacy law, FCRA permissible purpose) is recommended. It is out of this
  protocol's scope.
- **Companion records:** [LEP-0012](LEP-0012-sale-lifecycle.md) and
  [LEP-0014](LEP-0014-personal-loans-vertical.md). LEP-0014 is the first
  and only vertical this record admits.

## Decision (maintainer, 2026-11-19)

**Accepted** under [D1–D4](../../docs/DECISIONS-v1.3.md) of the v1.3 decision
record. The review window closed early with no comments, and no reviewer
independent of the author took part (D1, D2).

Conditions (D3):

- The reference platform implements only the fail-closed side. It refuses
  every regulated value with `LCP-100` and never advertises
  `features.regulated_identifiers`.
- A deployment that enables the lane MUST first record a sign-off from a
  **named human** security/privacy reviewer for that deployment
  ([Rollout](#rollout-migration-observability-and-rollback) step 2). This
  record certifies consistency with the specification, not legality or
  privacy fitness.
- Any comment with new evidence on
  [#255](https://github.com/SpearSystems/LCP/issues/255) reopens this record.
  Re-evaluation: 2027-05-19.

## Problem statement and evidence

**Lending buyers need these values at post time.** US short-term
personal-loan buyers underwrite, verify identity and fund by direct deposit
within the consumer's session. The buyer API used by the reference
deployment requires all of these on every post:

- Social Security number;
- driver's licence number and state;
- bank account number;
- ABA routing number.

A lead without them is unsellable to that buyer.

**LCP 1.2 forbids them outright.** `docs/PII-CLASSES.md` lists government
identifiers and "full bank account number with routing/sort data" as
`prohibited`: "in any message, in any extension, at any version". So a
lending lead cannot be both LCP-conformant and sellable.

**What that has cost, measured in the first adopter:**

1. **A silent fork.** SpearPointX classes SSN, licence and routing number
   `restricted` in its own code and vertical, against the published
   taxonomy.
2. **A broken schema.** Its `personal_loans` vertical v1.0.0 later had the
   SSN, licence and routing properties removed to match LCP 1.2. They were
   still listed as `required` inside an `additionalProperties: false`
   object. Run through the adopter's own validator, every possible
   `us_details` value is refused, either for `attributes.us_details.ssn is
   not an allowed field` or for `attributes.us_details.ssn is required`. No
   personal-loans lead can validate.
3. **Pressure toward worse flows.** With no sanctioned lane, the values
   travel in bespoke side channels or open containers (free-text answers,
   extensions), where detection is weakest.

A bounded, explicit lane with stricter handling than ordinary `restricted`
data is more protective in practice than a prohibition the market routes
around.

## Scope

- **Core (additive MINOR):**
  - the schema annotation `x-lcp-regulated` (§1);
  - the runtime gate (§2);
  - forwarding rules (§3);
  - handling rules (§4);
  - test-traffic rules (§5);
  - capability `features.regulated_identifiers` = `"1.3.0"`.
- **`docs/PII-CLASSES.md`:**
  - an exception paragraph under Prohibited values naming this record;
  - a new row in the emission matrix.
- **`docs/REQUIREMENT-PROFILES.md`:** one profile key,
  `regulated_fields` (§3).
- **Vertical allowlist:** `personal_loans` only, via LEP-0014. Adding a
  vertical requires its own LEP with a security/privacy co-sign.
- **Not in scope. These stay `prohibited` with no exception:**
  - payment card data (PAN, CVV, track data, PIN block);
  - biometric templates;
  - authentication secrets;
  - passport, immigration and visa numbers;
  - every non-US identifier (deferred).

## Proposed wire shape

### 1. The `x-lcp-regulated` annotation

The annotation goes on a vertical property and names the kind of
identifier.

| Kind | Admitted fields (closed list) | Format |
|---|---|---|
| `government_id` | `ssn` | US SSN or ITIN, `^\d{9}$`, no separators |
| `government_id` | `license_number` | US driver's licence number, `^[A-Za-z0-9*-]{1,20}$` |
| `bank_account` | `account_number` | `^\d{4,17}$` |
| `bank_account` | `routing_number` | US ABA routing number, `^\d{9}$` with a valid ABA checksum |

`license_state` and `account_type` are ordinary `restricted` fields, not
regulated ones.

**Load-time rules.** A schema that breaks one is refused at load, like a
class downgrade:

- the property is classed `x-lcp-pii: "restricted"` and `ping_safe: false`;
- it sits inside a country-scoped container (`us_details`), and that
  container is classed `restricted`;
- the property name and kind appear together in the table above;
- the vertical is on the allowlist (§Scope).

`x-lcp-regulated` is an annotation. Validators that do not know it ignore
it, which is why §2 is a receiver obligation, not a schema property.

### 2. The intake gate (receiver)

A value in an `x-lcp-regulated` field is accepted only when **all** of the
following hold. Otherwise the message is refused with `LCP-100`, `field`
naming the path, and is not stored. This is exactly the treatment of a
prohibited value today.

1. The receiver advertises `features.regulated_identifiers`.
2. The message is a `lead` or a `post`, including a batch item of either.
   It never appears in a `ping`, `bid`, `quote`, `counter`, `event`, `ack`,
   `subscription`, or history row.
3. The message's `lcp.version` is `1.3.0` or later.
4. The sender's credential is **enrolled** for regulated identifiers in this
   vertical. Enrollment is a deployment record created after the
   deployment's own due diligence. The protocol requires that it exists and
   is checked, not what it contains.
5. The value sits in an annotated field. Anywhere else (an extension,
   `lcp.intake.raw_form`, event `details`, a free-text attribute) the value
   is still `prohibited` and refused.

### 3. Forwarding (platform to buyer)

A platform forwards the regulated container only to a buyer that:

1. advertises `features.regulated_identifiers`;
2. is enrolled for regulated identifiers in this vertical; and
3. names the field in the matched requirement profile's `regulated_fields`.

```json
"lcp.platform.requirements": {
  "profile_id": "pl-buyer-us",
  "version": "3",
  "regulated_fields": { "us_details.ssn": "required", "us_details.routing_number": "required", "us_details.account_number": "required", "us_details.license_number": "optional" }
}
```

- **Required and optional.** A `required` regulated field absent from the
  lead makes the offer non-matching, with reason `regulated_field_missing`.
  An `optional` field is forwarded when present.
- **Minimum necessary.** Every other buyer receives the post **without**
  the regulated fields, and only the fields a buyer names are forwarded.
  This is per-buyer projection, not a scrub: the inbound message was
  accepted whole.
- **The on/off switches, all off by default:**
  - the deployment feature;
  - sender enrollment;
  - buyer enrollment;
  - the buyer's profile, field by field.

### 4. Handling (normative)

- **Transport.** TLS 1.2 or later on every hop. Regulated values are never
  carried in a URL, query string, header, cookie or redirect.
- **No durable storage.** A receiver MUST NOT write a regulated value to
  durable storage, including databases, queues, retry stores, backups,
  caches that outlive the process, and attachment stores.
  - A delivery retry that needs the value holds it in memory.
  - A retry that outlives the process fails as undeliverable and is
    reported as such. Persisting the value to retry is not allowed.
- **No observability leakage.** Never in logs, traces, metrics, error
  `message` or `details`, history rows, events, postbacks, or analytics.
  Errors name the field path only.
- **Audit by name, not value.** A delivery record MAY list which regulated
  field names were forwarded, and to which buyer.
- **No hashing route.** Hashing does not make a regulated value emittable,
  as with every `restricted` value.
- **Sender obligations:**
  - collect over HTTPS;
  - post server-to-server;
  - never place the values in browser storage, page URLs, analytics or
    tag-manager data layers;
  - keep them out of logs.

### 5. Test traffic

- A test message (`message.test` = `true`) that carries `ssn` MUST use the
  SSA advertising-reserved range `987654320`–`987654329`.
- A receiver refuses, with `LCP-013 TEST_TRAFFIC_POLICY`:
  - a test message whose `ssn` is outside that range;
  - a live message whose `ssn` is inside it.
- Other regulated fields in test traffic MUST be synthetic. A receiver MAY
  refuse values it recognises as real.

#### Valid 1 — enrolled sender, test lead (v1.3.0 shape, `personal_loans` 2.0.0)

```json
{
  "lcp": {
    "version": "1.3.0",
    "message": {
      "id": "d1300000-0000-4000-8000-000000001301",
      "type": "lead",
      "timestamp": "2026-11-19T10:00:00Z",
      "sender_id": "pub_example",
      "receiver_id": "platform_001",
      "idempotency_key": "pub-example-lead-1301",
      "test": true
    },
    "payload": {
      "lead_id": "lead_1301",
      "submitted_at": "2026-11-19T09:59:58Z",
      "status": "NEW",
      "channel": "form",
      "consumer": { "first_name": "Test", "last_name": "Applicant", "email": "test.applicant@example.com", "phone": "+12025550143", "dob": "1990-01-15" },
      "location": { "country_code": "US", "state_region": "TX", "postal_code": "75001" },
      "compliance": { "consent_timestamp": "2026-11-19T09:59:50Z", "consent_source_url": "https://lp.example.com/apply", "consent_text_version": "consent-v1" },
      "attributes": {
        "vertical": "personal_loans", "schema_version": "2.0.0", "amount_currency": "USD",
        "loan_amount": 1000, "monthly_income": 3000, "income_source": "employment", "income_frequency": "biweekly",
        "account_type": "checking",
        "us_details": { "country_code": "US", "ssn": "987654321", "license_number": "TEST1234", "license_state": "TX",
                        "account_number": "000123456789", "routing_number": "123456780" }
      }
    }
  }
}
```

#### Invalid A — receiver does not advertise the feature

The same message sent to a receiver without `features.regulated_identifiers`
is refused with `LCP-100`, `field` = `attributes.us_details.ssn`, and is
not stored.

#### Invalid B — SSN in a free-text container

`extensions["lcp.intake.raw_form"]` contains an answer whose value is a
9-digit SSN. It is refused with `LCP-100`, `field` =
`extensions.lcp.intake.raw_form`. The exception covers annotated fields
only.

#### Invalid C — regulated field in a ping

A `ping` carrying `attributes.us_details` is refused with `LCP-008
PII_IN_PING` (and `LCP-100`), as for every `restricted` value.

#### Invalid D — real-looking SSN in test traffic

Valid 1 with `"ssn": "123456789"` is refused with `LCP-013
TEST_TRAFFIC_POLICY`.

#### Invalid E — vertical not on the allowlist

`verticals/insurance.json` declaring `"x-lcp-regulated": "government_id"`
is refused at load time.

#### Invalid F — regulated value in an event

`event.details.routing_number` is refused with `LCP-100`. Events never
carry regulated values.

## Privacy, security, and universal-core audit

**This record widens disclosure, and says so.** Values LCP refused at any
version become carriable in one vertical, under a gate. It is a
less-protective class change in the sense of LEP-0011, and it requires the
security/privacy co-sign named in the header.

**Why the widening is bounded:**

- **Off four ways by default:** deployment, sender, buyer, and per-field
  profile.
- **Narrow scope:**
  - one vertical;
  - four named fields of two kinds;
  - one country container;
  - `lead`/`post` only.
- **Stricter than `restricted` on handling:** no durable storage at all,
  and no URL or header carriage.
- **Refusal semantics unchanged everywhere else:** the same value in any
  unannotated place is still `prohibited`, refused, never scrubbed.
- **Test-range enforcement** stops real identifiers leaking through test
  traffic and sandboxes.

**Threat-model rows affected:**

- PII in non-PII messages: unchanged, because regulated values never appear
  in non-PII messages.
- Logging leakage: tightened, because regulated values are named in the
  "never" column.
- Insider and storage compromise: tightened, because nothing persists.
- Mis-routing to an unenrolled buyer: new row. It is mitigated by the
  three-condition forwarding rule and a conformance vector.

**Legal position.** The protocol makes no claim that carrying these values
is lawful in a given flow. Permissible purpose, consent disclosures and
safeguards programmes belong to the deployment and its buyers. Enrollment
(§2.4, §3.2) is the hook where a deployment records that diligence.

**Universal-core audit (SPEC §12).**
- No field enters the core: the regulated fields live in a vertical's
  country container.
- The annotation is vertical-neutral and the allowlist is the control.
- Ping is untouched.

## Compatibility, versioning, and deprecation impact

- **MINOR.**
  - An unknown annotation keyword is ignored by schema validators.
  - The capability is additive.
  - No class is added, removed or reordered, which would be MAJOR under
    LEP-0011.
- **Fail-closed toward old peers.** A 1.2 receiver refuses these fields,
  because they are prohibited or undeclared. That is correct.
  - A sender MUST NOT send regulated fields to a receiver that does not
    advertise the feature.
  - SDKs enforce this from `capabilities()`.
- **Deprecation:** none.

## SDK, OpenAPI, MCP, conformance, and operational impact

- **SDKs:**
  - typed fields only in the `personal_loans` models;
  - redaction of annotated fields in every SDK logger and `repr`;
  - a pre-send capability check that refuses locally;
  - a `regulated_fields(schema)` helper.
- **OpenAPI:** the vertical's schema carries the annotation. Example
  payloads use reserved test values only.
- **MCP:** MCP tools MUST NOT accept or return regulated fields. Agent
  surfaces are out of scope for this lane, and a deferred question covers
  revisiting that.
- **Conformance:** suite `test-vectors/regulated-identifiers.json`
  (`REG-001`…). It covers every Invalid example, the forwarding rule
  (enrolled versus unenrolled buyer), the no-persistence assertion
  (storage inspected after delivery) and the test-range rule.
- **Validator:** the load-time checks in §1, beside LEP-0011's downgrade
  check.
- **Operations:**
  - metrics count regulated messages accepted and refused, by reason, never
    values;
  - `lcp-doctor` warns when the feature is on without any enrolled sender
    or buyer;
  - flag `LCP_FEATURE_REGULATED_IDENTIFIERS`, off by default.

## Alternatives considered

- **Keep them prohibited.** Rejected on the evidence: it produced a fork
  and a broken vertical in the first lending adopter, and it pushes values
  into weaker channels.
- **A sixth class, `regulated`.** Rejected: adding a class is MAJOR under
  LEP-0011. An annotation on `restricted` gives the same control without a
  version break.
- **Tokenisation through a vault.** The sender posts a token, and the
  enrolled buyer redeems it from a vault. This has the strongest privacy
  property, but it needs a vault service, a redemption API and every
  buyer's integration. **Deferred as the long-term direction.** This lane
  is shaped so a token field can later replace the value without moving the
  gate.
- **Hashes.** Rejected. Underwriting and funding need the value.
- **Extension-only** (`{org}.lending.identifiers`). Rejected. It places the
  values in an open container, where the protocol's detection is weakest.

## Rollout, migration, observability, and rollback

1. **Validator annotation checks and vectors land with the feature off.**
   The reference platform implements the fail-closed side only: it
   validates the annotation at load, refuses every regulated value with
   `LCP-100`, and never advertises `features.regulated_identifiers`,
   because it has no in-memory delivery lane that satisfies §4. The
   enabled lane (§2.4 enrollment, §3 projection, §4 handling) is a
   deployment implementation; SpearPointX is the first.
2. **The reference deployment (SpearPointX) enables it** for
   `personal_loans` after the security/privacy co-sign. It starts with one
   enrolled publisher and one enrolled buyer, and replaces its fork (local
   `restricted` classing and per-buyer flag) with this gate.
3. **Migration for the adopter's v1.0.0 vertical** is covered by LEP-0014.
4. **Observability:** accepted and refused counts by reason, and forwarded
   field names per buyer. There are no values anywhere.
5. **Rollback:** turning the feature off makes every regulated value
   refused again, as prohibited. Nothing is stored, so nothing needs
   purging.

## Deferred questions

- **Encrypted, short-lived retry state.** A strict no-persistence rule
  turns transient buyer outages into lost sales. Trigger: a measured loss
  rate from process restarts that a deployment cannot accept, plus a
  security reviewer's sign-off on a bounded encrypted store.
- **Vault tokenisation.** Trigger: a second lending adopter, or a buyer
  that can redeem tokens.
- **Non-US identifiers** (TFN, SIN, NI number). Trigger: a lending
  vertical outside the US with buyer evidence. Each country needs its own
  LEP.
- **Agent and MCP surfaces.** Trigger: an agent-submitted lending lead
  with a verified attestation (LEP-0008) and a buyer requesting it.
- **Standard enrollment attestation format.** Trigger: two deployments
  needing to exchange enrollment evidence.

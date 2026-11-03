# LEP-0011 — PII sensitivity taxonomy and schema-level classification

- **Title:** PII sensitivity taxonomy and schema-level classification (`x-lcp-pii`)
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-04
- **Status:** **Accepted** — Accepted 2026-11-04 (maintainer; [D9](../../docs/DECISIONS-v1.2.md#d9--pii-sensitivity-taxonomy) of the signed v1.2 decision record, under the [D1](../../docs/DECISIONS-v1.2.md#d1--trigger-waiver-batch-of-2026-11-04) trigger waiver)
- **Target version:** v1.2.0 (additive MINOR; **zero wire change** — schema annotations, a load-time validator, and one normative document)
- **Discussion:** maintained in [DECISIONS-v1.2.md](../../docs/DECISIONS-v1.2.md) D9 (the maintainer signed the decision at the M0 pass; no separate `[LEP]` discussion issue was opened)
- **Roadmap / work items:** [`docs/V1.2-EXECUTION-PLAN.md`](../../docs/V1.2-EXECUTION-PLAN.md) W0-02 (this record) and W6-04 (taxonomy, annotations, validator, per-class vectors)

## Decision (maintainer, 2026-11-04)

**Accepted.** [`docs/PII-CLASSES.md`](../../docs/PII-CLASSES.md) is adopted as
the normative taxonomy, and every schema property is annotated with
`x-lcp-pii` using one of five classes — `public`, `internal`, `contact`,
`restricted`, `prohibited`.

The rules D9 records, which this proposal makes operational:

- **Ping emission** allows `public`, `internal`, and hash-only projections of
  `contact`. `restricted` and `prohibited` never appear in a ping.
- **No downgrade.** A vertical, extension, or deployment may never assign a
  class lower than the core's. The validator refuses it at load time, so the
  failure lands on the operator's deploy and not on a consumer's data.
- **`prohibited` means refused, not scrubbed.** Government identifiers, full
  payment credentials, and biometric templates have no home in LCP; a value
  of that kind is a validator error naming the field.

Basis: D9 in the signed v1.2 decision record, and the measurement in
[Problem statement and evidence](#problem-statement-and-evidence) below.
Implementation authorization is scoped to
[Scope](#scope): schema annotations, a load-time validator, a CI coverage
check, and per-class vectors. This record authorizes no wire change, no new
error code, and no retention policy.

## Problem statement and evidence

### Ad-hoc classification

The protocol has exactly one machine-readable classification, and it answers
one question. `ping_safe: true|false` appears on vertical attributes
(108 of the 120 declared vertical properties across the six verticals at
2026-11-04; the untagged two per vertical are `vertical` and
`schema_version`). It is a per-field eligibility flag, not a class: it says
"may this value go in a ping", not "what is this value, and what may I do
with it".

Consequences, each measurable in the tree today:

1. **The tag is trusted, not checked.** The reference platform verifies only
   that a pinged attribute exists in the vertical and that its definition
   says `ping_safe: true`
   (`_ping_safe_errors` in
   `implementations/reference-platform/lcp_platform/validation.py`; the
   failure message reads "is not tagged ping_safe: true"). A vertical
   that tagged a health or credit attribute `ping_safe: true` would pass:
   nothing compares the tag against the field's meaning, because the
   protocol stores no meaning.
2. **The prohibition on prohibited values is prose.** `schemas/lead.json`
   and `schemas/post.json` carry a long `description` on `extensions`
   stating that question and answer text "MUST NOT carry contact values,
   government identifiers, payment credentials, consent tokens, or evidence
   values". It is the only place those four families are named, and it
   governs exactly one field. There is no check behind it, and no way for a
   reader to learn whether `consumer.dob` or `provenance.source_url` is in
   the same family.
3. **The SPEC's message-class PII table is unenforced prose.** SPEC §4
   (`spec/02-envelope.md`) states `ping`: "None (hashes only)", `bid`:
   "None", `ack`: "None", `event`: "None". The ping column has a real
   allowlist behind it (`schemas/ping.json` is `additionalProperties: false`,
   and `LCP-008 PII_IN_PING` exists); the other three columns have nothing,
   so "event carries no consumer data" is a statement about curators.
4. **`docs/PRIVACY-DATA-GOVERNANCE.md` asks for a class per field and gets
   none.** Its "Data lifecycle" section requires that "For every field and
   message type, define: … Encryption and access class". There is no
   enumeration of access classes to choose from, so every deployment invents
   its own and two conformant deployments classify the same field
   differently.
5. **Extension authors guess.** `governance/EXTENSION-REGISTRY.md` records
   classification decisions in prose per row ("classed `restricted` under
   the PII taxonomy", "no contact or restricted fields"). Those phrases
   reference a taxonomy that did not exist when they were written, so each
   row asserted consistency with a document that could not be read — and
   could not be checked against the field's own annotation, because the
   field has none.

Measured 2026-11-04: `x-lcp-pii` appears in **no** file under `schemas/`,
`verticals/`, or `implementations/`. The string exists only in the decision
records that mandate it (`docs/DECISIONS-v1.2.md:138`,
`docs/V1.2-EXECUTION-PLAN.md:80,166`), this record's registry row, and the
LEP records that cite the taxonomy. The annotation pass and the validator
are the implementation this record authorizes; neither exists yet.

### Why a class and not a bigger boolean

Every failure above has the same shape: a downstream mechanism needs to know
what a field *is*, and the field does not say. A boolean cannot express the
difference between "no consumer data but commercially confidential" (a
price), "identifies the consumer but may be pseudonymized for dedup" (a
phone hash), and "sensitive attribute that may never leave the pair" (a
diagnosis). Each of those needs a different rule on a different surface, and
the rules are already written — in SPEC §4, §9, `PRIVACY-DATA-GOVERNANCE.md`,
`SECURITY-ARCHITECTURE.md`, and the extension registry's prose. A five-value
ladder is the smallest vocabulary that lets those existing rules be checked
instead of remembered.

## Scope

**In scope.**

- [`docs/PII-CLASSES.md`](../../docs/PII-CLASSES.md) — the normative taxonomy: five classes and their
  order, the emission matrix per class and surface, hashing rules and the
  projection table, the no-downgrade rule, the prohibited list with its
  refusal behaviour, guidance for vertical and extension authors, and the
  field-by-field mapping of the current core.
- `x-lcp-pii` on **every** property definition in `schemas/*.json`
  (371 `properties` entries across eleven documents, enumerated 2026-11-04),
  on every vertical attribute in `verticals/*.json`, and on every field a
  registered extension declares.
- A **load-time validator** that refuses a class downgrade, refuses an
  unannotated property once rollout step 3 is reached, and refuses a schema
  that declares a `prohibited` property.
- A **CI coverage check** that fails on any unannotated schema property.
- Conformance vectors per class: one accepted and one refused case at
  minimum, plus the projection vectors already pinned in
  `test-vectors/sdk/contact-hash.json`.

**Out of scope.**

- Any wire field, message type, event value, capability key, or error code.
  `x-lcp-pii` is not transmitted.
- Retention periods, erasure behaviour, encryption keys, or residency. This
  record supplies the vocabulary those policies key off; it does not set the
  policies (see Deferred questions).
- A prohibited-value detector algorithm. The record fixes the *refusal
  behaviour*; detection is deployment DLP.
- Reclassifying any field relative to today's behaviour. Every class here is
  consistent with an existing rule or `ping_safe` tag; where a field had no
  rule, the class records it, it does not change it.
- Generated artifacts beyond the annotation pass: SDK models, OpenAPI
  documents, and the schema bundle regenerate from the annotated schemas by
  the repository's existing checks.

## Proposed wire shape

**No wire change.** This proposal adds no message type, field, enum value,
error code, header, capability, or version-negotiation rule. A v1.0 or v1.1
peer that reads none of this record interoperates exactly as before, and a
conforming reader that ignores the annotation validates identically.

What does change is the schemas' metadata shape — annotations on property
definitions — plus a load-time refusal. Both are specified below because they are the only
artefacts a reviewer can check.

### The classification model

Five classes, ordered least to most protective. The order is the definition
used by the no-downgrade rule; it is not a suggestion.

| Class | What it is | Ping | Logs |
|---|---|---|---|
| `public` | No consumer data, no commercial secret. Safe to publish. | allow | allow |
| `internal` | No consumer data, confidential to the parties or deployment. | allow | allow |
| `contact` | Identifies, locates, or reaches a natural person; includes per-pair pseudonyms derived from such values. | hash projections only | never |
| `restricted` | Describes the person or record in a sensitive, evidentiary, or capability sense. | never | never |
| `prohibited` | A value LCP will not carry at all. | never | never |

Three properties of the model carry most of its weight, and each is
normative in `docs/PII-CLASSES.md`:

- **A container takes the maximum of its descendants.** `lead.consumer` is
  `restricted` because `consumer.dob` is, even though `consumer.email` is
  `contact` and `consumer.locale` is `internal`. The alternative — annotating
  only leaves — makes the parent's class depend on a runtime walk that no
  reader can verify by eye.
- **An open object with no registered shape is `restricted`.** This is what
  makes "event carries no consumer data" mechanical: `event.details` is
  `restricted`, and the `event` column refuses `restricted`. A known event
  shape (`CONVERTED`, `FINAL_PRICING`) carries `public`/`internal` values and
  `contact` digests, so it passes; an event carrying a name, a date of birth,
  or a recording URL does not.
- **A hash is `contact`, not `public`.** A per-pair digest is a stable
  pseudonym for one person within one pair; a buyer receiving repeated pings
  joins on it. Treating it as anonymous would repeat the mistake SPEC §9 and
  §14 blocker B3 already corrected for unsalted digests.

### The annotation mechanism

The key is `x-lcp-pii`; the value is one of the five class names (plus
`computed`, legal only on `envelope.payload`). Placement is on every
property definition, at the level it is declared. **Valid annotation** —
each of these must load:

```json
"$defs": {
  "consumer": {
    "properties": {
      "email": { "type": "string", "format": "email", "x-lcp-pii": "contact" },
      "dob": { "type": "string", "format": "date", "x-lcp-pii": "restricted" }
    },
    "x-lcp-pii": "restricted"
  }
}
```

```json
"pricing": {
  "type": "object",
  "x-lcp-pii": "internal",
  "properties": {
    "currency": { "type": "string", "x-lcp-pii": "public" },
    "fx_rate": { "type": "string", "x-lcp-pii": "internal" }
  }
}
```

```json
"payload": {
  "x-lcp-pii": "computed",
  "description": "Class of the payload schema selected by message.type."
}
```

Normative details:

- `x-lcp-pii` appears **once** per property definition: on the `$defs` entry
  or the inline definition, never on a `$ref` site. A `$ref` inherits its
  definition's class, so `lead.consumer` and `post.consumer` cannot drift.
  If a message genuinely needs a different class for the same shape, that is
  a new definition, not an override.
- Valid values are the five class names plus `computed`, which is legal only
  on `envelope.json`'s `payload`.
- The keyword is a JSON Schema **annotation**: validators that do not know it
  ignore it, exactly as they already ignore `ping_safe` and `$comment`.
- Verticals and registered extension records must stay consistent with the
  boolean they already carry: `ping_safe: true` requires `public`/`internal`,
  or a declared `contact` hash projection.

**Validator behaviour, load time.** The validator walks every schema
document it would validate against, plus each configured vertical and each
registered extension it implements, and:

1. **refuses a downgrade.** A declared class lower than the core's (or the
   extension registry record's) for the same field is `LCP-100
   VALIDATION_ERROR`, `field` naming the property path, `details` carrying
   `class_required` and `class_declared`. Upgrades are accepted silently —
   more protection never needs permission.
2. **refuses `prohibited` as a property class.** A field LCP will not carry
   may not be added; the class exists so the coverage check can tell
   "deliberately refused" from "not yet annotated".
3. **refuses an unannotated property** once rollout step 3 is reached, so a
   new field cannot arrive unclassified.

The refusal is per-deploy and loud: the process does not start with a schema
set it cannot classify. That is the whole point of putting the check at load
rather than at message time — an operator finds out before a ping exists.

**Validator behaviour, message time.** Two existing codes cover the runtime
half, and neither is new:

- `LCP-008 PII_IN_PING` for a class violation on a ping (already the code
  for an unknown ping field).
- `LCP-100 VALIDATION_ERROR` naming the field for a `contact`/`restricted`
  value on a non-PII surface (`bid`, `event`, history row) or a
  `prohibited` value anywhere, including inside an open container
  (`event.details.<key>`, `extensions.<namespace>.<key>`, free-form intake
  answers).

### Invalid cases (what the validator must refuse)

- **A vertical downgrades a core field.** `verticals/mortgage.json` declares
  `"consumer.phone": { "x-lcp-pii": "internal" }`. Refused at load:
  `LCP-100`, `field: "consumer.phone"`,
  `details.class_required: "contact"`.
- **A vertical widens ping eligibility with a flag.** A `restricted`
  attribute tagged `ping_safe: true`. Refused at load, naming the attribute:
  the flag is bounded by the class.
- **A ping carries plaintext contact.** `consumer.full_name` in a ping
  payload: `LCP-008`, naming the field.
- **An event carries a restricted value.** `details.dob` on an `event`:
  `LCP-100`, `field: "details.dob"`.
- **A prohibited value arrives in an open object.** An SSN inside an answer
  carried by `extensions.lcp.intake.raw_form`: `LCP-100` naming the path
  inside that namespace, message not stored. The value is refused, never
  scrubbed.
- **A name is hashed into a "safe" field.** A publisher emits
  `consumer.last_name` as an HMAC digest to reach a ping. There is no
  declared projection for a name; the keyspace enumerates, so the digest
  inverts by lookup. Refused: only declared projections may cross.

## Privacy, security, and universal-core audit

**Does this create a new PII surface?** No. Every property already exists and
already travels exactly as far as it did. The change makes an existing
distribution auditable and, where the tree disagrees with its own prose,
refuses it.

**Does it move a PII boundary?** It narrows two, in the only direction that
needs no compatibility consideration:

- a `restricted` field can no longer be ping-emitted by tagging it
  `ping_safe: true` — the tag alone is no longer sufficient;
- a prohibited value can no longer be persisted because nothing checked it.

**Does it widen anything?** Only in one direction that a reviewer should
weigh. SPEC §4 already imposes an event class rule: `details` MUST NOT carry
consumer PII, and a receiver rejects a plaintext contact value there with
`LCP-100` (`spec/02-envelope.md`). Annotating `event.details` as `restricted`
generalizes that rule mechanically, and the generalization reaches values the
prose did not name — a date of birth, a recording URL, a filename. That is
the intent, and it is the one behaviour change in this record: a sender that
was putting sensitive values in `details` gets a rejection it did not get
before. The implementation PR must state it in the release note rather than
shipping it as a silent tightening.

**Universal-core check.** The taxonomy adds no field, no optional object, and
no enum on the wire; the universal core is unchanged in shape. The simplicity
budget is satisfied by addition without mechanism: one keyword, one
validator walk, no per-surface special case. No vertical gains a field it did
not already have, and no v1 receiver can fail on data it never received.

**Security review note.** `docs/PII-CLASSES.md` is a PII-boundary document,
so `governance/LEP.md`'s security/privacy reviewer requirement applies to the
implementation PR that lands annotations and the validator — the maintainer's
D9 signature authorizes the work, not a substitute for that review.

**Threat-model rows affected.** PII in non-PII messages (§4 table), logging
of consumer values (`PRIVACY-DATA-GOVERNANCE.md` "Required operational
controls"), and the extension-smuggling path LEP-0006 Track B opened. The
residual risk this record does not close: a deployment may still write a
`contact` value into its own database column that has no LCP field, and no
annotation can reach it.

## Compatibility, versioning, and deprecation impact

- **JSON Schema validators.** `x-lcp-pii` is an unknown keyword to every
  validator that does not implement it. Draft 2020-12 treats unknown
  keywords as annotations and ignores them; the tree already relies on this
  for `ping_safe` and `$comment` in `verticals/*.json`, which are unknown to
  JSON Schema and read by the platform's own code. Adding one more annotation
  therefore changes **no validation outcome**.
- **No wire change, no version negotiation.** Nothing is transmitted, so a
  v1.0 or v1.1 peer is unaffected, and no capability advertisement is
  required. `lcp.version` does not move for this change; the release that
  contains it is v1.2.0 for other reasons.
- **Schema document versions.** Adding an annotation is a PATCH-level change
  to a schema document in the sense that it is inert; `$id`s do not change,
  and `vertical_schemas.version` does not move for annotation-only edits.
  A class *change* (see below) is the versioned event.
- **Manifest and bundle.** Annotated schemas change bytes, so the schema
  bundle and its SHA-256 manifest must be regenerated in the same PR — a
  repository-mechanical step with an existing check behind it, not a protocol
  change.
- **Deprecation.** None. No field is deprecated; `ping_safe` is retained, not
  replaced. It remains the eligibility flag, and this record gives it a
  ceiling.
- **Class changes are governed by direction.** More protective: MINOR,
  self-contained, can only remove emission eligibility. Less protective:
  requires an LEP with a security/privacy co-sign, because it widens
  disclosure. Adding or removing a class, or reordering them: MAJOR.

## SDK, OpenAPI, MCP, conformance, and operational impact

- **SDKs (10).** Generated models are produced from the schemas; an
  annotation must not become a model field, a default, or a serialized
  property. The per-SDK generation check is the guard: if a generated model
  grows an `x_lcp_pii` member, the annotation leaked into the type layer and
  the PR is wrong. No SDK API change is authorized by this record.
- **OpenAPI.** Where documents are generated from the schemas, the keyword
  MAY be copied through as `x-lcp-pii`; it is inert in OpenAPI too (§4.7.24
  extension keys). It MUST NOT be turned into a `description` rewrite or a
  schema-level `format`.
- **MCP.** No new tool, no tool argument, no response field. A tool that
  exposes a `lead`/`post` payload exposes exactly the fields it did before;
  the reference platform's redaction defaults are unchanged by this record.
- **Conformance.** New vectors, per class: a ping carrying each permitted
  class at least once (accepted) and each refused class once (rejected with
  the named code and field). Reuse of existing vectors is expected where a
  class rule already had a vector (ping PII, `LCP-008`).
- **Coverage check.** A CI tool counts property definitions with and without
  `x-lcp-pii` and fails at any nonzero unannotated count once rollout step 3
  is reached. The count is the acceptance number for this record: 371 core
  entries, 120 vertical properties, and every field of every registered
  extension.
- **Operational.** One new refuse-to-start condition (a downgrade, a
  `prohibited` property, or — at step 3 — an unannotated property). It is
  the operator's own schema set, so the blast radius is a misconfigured
  deploy, and the error names the file and the field. Enforcement ships
  behind `LCP_FEATURE_PII_ENFORCEMENT` default-off in the reference platform,
  per the D1 rule that new gates are exercised by CI and vectors before they
  are on by default.

## Tests

The implementation PR must land these; none of them requires a wire change.

1. **Coverage.** `pii_coverage.json` (generated) reports 0 unannotated
   properties across `schemas/*.json` and `verticals/*.json`; the CI check
   fails on a nonzero count and names every offending path. Negative case:
   add a property with no annotation, assert the check fails naming it.
2. **Downgrade refusal (load).** Load a vertical overlay declaring
   `consumer.phone` as `internal`; assert `LCP-100` with
   `field == "consumer.phone"` and both classes in `details`, and assert the
   process refuses to start. Symmetric case: an upgrade (`internal` →
   `restricted`) loads cleanly.
3. **Container computation.** Assert `lead.consumer` resolves to
   `restricted` (max over `consumer.*`), and that a hypothetical consumer
   with only `contact` children resolves to `contact` — the rule, not a
   hardcoded value.
4. **Ping emission.** `plat: test_ping_rejects_contact_plaintext` (a ping
   with `consumer.full_name` → `LCP-008`), `test_ping_accepts_contact_hash`
   (a ping with `phone_hash` → accepted), and
   `test_ping_rejects_restricted_attribute` (a vertical attribute classed
   `restricted` tagged `ping_safe: true` → rejected at load).
5. **Prohibited values.** `plat: test_prohibited_value_refused_in_extension`
   — an SSN-shaped value under a `raw_form` answer yields `LCP-100` naming
   the path, the message is not persisted, and the value is not scrubbed
   (assert the stored envelope does not exist, not that it is empty).
6. **Hash projections.** The existing `test-vectors/sdk/contact-hash.json`
   rows recompute; the email canonical stays gated (no email-form
   `contacted_hashes` entry ships), and a validator attempt to declare a
   projection for a name or a date of birth is refused.
7. **No-wire-change regression.** A golden corpus of envelopes (one per
   message type) validates identically with and without annotations, byte
   for byte on the wire: annotation must not alter a validation result or a
   serialized payload.

## Alternatives considered

1. **Free-text guidance only — a "PII guidance" section in SPEC and the
   privacy doc, no annotations.** Cheapest, and explicitly the status quo
   that produced this record: the extension policy already states its
   prohibition in prose on one field, and nothing checks it. A reader cannot
   audit 371 properties against prose, and no CI gate can be built on it.
   Rejected: it costs one document and buys nothing that can fail.
2. **JSON Schema `format` instead of an `x-` keyword.** `format` is a
   value-syntax vocabulary ("email", "date-time"); a confidentiality class
   is not syntax, and `format` applies only to strings, so object, array,
   boolean, and integer properties would have no slot. Assertion behaviour of
   `format` also varies by validator configuration, which is the opposite of
   what a load-time invariant needs. Rejected.
3. **Promote `ping_safe` into the taxonomy.** One boolean per field, already
   deployed. Rejected on three counts: it cannot distinguish `restricted`
   from `contact` (both would be `false`), it carries no order so
   "downgrade" is undefined, and it is an eligibility statement rather than a
   description of the value — the two questions diverge the moment a
   `contact` hash is legitimately pingable.
4. **A central catalogue keyed by JSON pointer, outside the schemas.** Keeps
   `schemas/*.json` untouched. Rejected: two artefacts describing the same
   property drift, and the load-time validator would compare a schema against
   a catalogue that a schema edit does not update. The annotation travels
   with the definition that it describes.
5. **Deployment-side DLP only — each operator classifies its own data.**
   Already possible and insufficient: classification that lives only in a
   deployment cannot be compared across adopters, cannot be validated at
   load, and cannot be the thing a vertical author is held to.
6. **Proposed:** five classes annotated in-schema, one normative document,
   one load-time refusal, one CI coverage number. Every existing rule that
   mentions a PII boundary gains a machine-checkable referent.

## Rollout, migration, observability, and rollback

1. **Core annotations.** Annotate `schemas/*.json` (371 entries) per
   `docs/PII-CLASSES.md`. No behaviour change: nothing reads the keyword yet.
   The bundle and manifest regenerate in the same PR.
2. **Verticals and extensions.** Annotate the 120 vertical properties and
   every registered extension field. This is where a real finding can appear:
   an attribute that cannot be `ping_safe: true` under its new class must
   move to `ping_safe: false` (or become a banded derivative) in a vertical
   version bump, and the release note must name it.
3. **Refuse unannotated in CI.** Turn the coverage check blocking at zero.
   From here a new property without a class cannot merge.
4. **Load-time enforcement.** Ship the downgrade/prohibited refusals behind
   `LCP_FEATURE_PII_ENFORCEMENT` (default off), exercised by CI, vectors, and
   the conformance suite, then enable it in the reference deployment after one
   release with no findings. This is the D1 waiver's compensating constraint
   applied literally: the gate exists, runs everywhere it can be checked, and
   is off by default in the platform.
5. **Migration.** Nothing to migrate: annotations are inert and no field
   changes class relative to today's behaviour. The only migration is
   documentary — the extension registry's prose phrases ("classed
   `restricted` under the PII taxonomy") become links to this taxonomy, and
   the registry rows gain the class column.
6. **Observability.** Four numbers, all cheap: unannotated-property count
   (must be 0 after step 3), load-time refusal count by reason, runtime
   `LCP-008` count by field, `LCP-100` prohibited-value count by field. A
   rising prohibited-value count means a sender is systemically
   non-conformant and is the signal this record exists to surface; today it
   is invisible.
7. **Rollback.** Revert the annotation PR: the keyword is inert, so nothing
   changes at runtime. Revert the validator PR: the load-time refusals
   disappear, and runtime behaviour is unchanged because no runtime path
   depends on a class. No data migration and no compatibility window are
   involved — the published behaviour of a conformant peer never changed.
8. **Re-evaluation (D1).** 2027-05-04, as recorded against the waiver. The
   trigger that would have gated this work was not met; the waiver says so
   and this record keeps saying so.

## Deferred questions

- **Retention and erasure per class.** `docs/PRIVACY-DATA-GOVERNANCE.md`
  asks for a retention period per field; the taxonomy does not supply one,
  and mapping class → period would embed legal advice in a protocol
  document. Trigger: the first adopter that must publish a per-class
  retention schedule for an audit, or the first retention decision that
  differs between two classes in the same deployment.
- **Finer subdivisions inside `restricted`.** Health, financial, legal, and
  insurance data may need different consent or routing rules in one
  deployment, and one class cannot express that. Trigger: two independent
  adopter requests, or a vertical whose consent routing needs to branch on
  the distinction.
- **Class-directed encryption or key scoping.** A `restricted` field could
  plausibly demand its own key or envelope. Today the class is advisory to
  the operator's encryption policy; the protocol names no per-class key.
  Trigger: the first deployment that must demonstrate per-class key
  separation to an auditor.
- **Automatic prohibited-value detection.** This record specifies refusal,
  not detection. Whether the reference platform ships a PAN/SSN/biometric
  pattern check, and with what false-positive budget, is unanswered.
  Trigger: the first confirmed prohibited value arriving on production
  traffic, or the first adopter asking for a shared detector.
- **Mapping to legal taxonomies.** GDPR special categories, CCPA sensitive
  personal information, and sector rules (HIPAA, GLBA) each cut across these
  five classes. Mapping them is a legal judgement per jurisdiction.
  Trigger: an adopter whose privacy notice must cite the mapping, or a
  jurisdiction whose regulator asks for one.
- **Class-aware log schemas.** The matrix says logs never carry `contact` or
  `restricted`, and no log schema exists to annotate. Trigger: the first
  structured-log schema added to the reference platform, or the first
  incident review that needs the rule to be checkable rather than stated.
- **`computed` as a first-class resolution step.** Only `envelope.payload`
  and `attributes` need a class computed at runtime from another document.
  If a third site appears, the resolution rule deserves its own normative
  paragraph rather than a per-site convention. Trigger: a third `computed`
  site.
- **Whether `prohibited` ever annotates a property.** At v1.2.0 none does:
  LCP carries no such value, so there is nothing to annotate. The class is
  kept because a refusal must be expressible. Trigger: the first proposal
  for a field whose only legal content would be prohibited — the annotation
  is then the refusal itself.

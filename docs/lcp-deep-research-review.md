# LCP v1.0 — Deep Research Review
> **Research page · Page 6 of 6**
>
> **Status: resolved.** All 5 blockers and 15 should-fixes in this review
> have been addressed in SPEC.md (see §14 review-log appendix). This file
> is kept for provenance — it documents the adversarial review the spec
> passed before v1.0. The findings below describe the spec as it was
> *before* the fixes, not as it is now.

**Reviewer role:** senior protocol architect, adversarial pass
**Reviewed:** SPEC.md (canonical), cross-checked against AGENTS.md rules, README.md, and the deep-research prompt itself
**Date:** 2026-08-15

---

## 1. Historical pre-fix verdict

At the time of this review, the design was a sound foundation but not yet
bulletproof. The ping/post PII split, envelope/payload separation,
country-code-first location model, and "REST core + thin MCP wrapper" agent
binding were defensible choices, while the findings below identified the
remaining pre-v1 blockers. This historical snapshot deliberately preserves the
adversarial starting point; the canonical v1.0 decisions and resolutions are
recorded in SPEC.md §14 and are not restated as current defects here.

---

## 2. Historical blockers (all resolved before v1.0)

**B1 — Ping PII safety is a blocklist, not a structural guarantee.**
§4 `ping` lists 18 forbidden keys. A blocklist can never be complete — it already misses `full_name`, `middle_name`, `session_capture_id`, `jornaya_token`/`trustedform_token`, `call_recording_id`, and any quasi-identifying field a *vertical* schema might introduce inside `attributes` (e.g. a mortgage vertical accidentally including `property_address`). Enumeration-based safety is a matter of when it fails, not if.
*Fix:* Make the `ping` JSON Schema a strict allowlist (`additionalProperties: false`) instead of a blocklist filter. Additionally, require every vertical schema to tag each attribute field `ping_safe: true/false`, and have the L2/L3 conformance runner mechanically reject any `ping` payload containing a non-`ping_safe` field — closing the loophole where new verticals leak PII through `attributes` rather than through the core.

**B2 — Compliance block hardcodes US-specific vendor fields into the universal core.**
§3 `compliance` names `jornaya_token`, `trustedform_token`, and `tcpac_consent` as core fields. These are specific US TCPA-consent-proof vendors and a US-specific law reference — this is exactly the kind of market-specific leakage AGENTS.md rule 1 and SPEC.md §12 are meant to prevent, and it's currently invisible to the universal-audit checklist because the checklist doesn't check the compliance block's *named* sub-fields, only "vertical" and "market" fields in general.
*Fix:* Replace the three named tokens with a generic `consent_evidence: [{ type, provider, token_or_url }]` array. Jornaya/TrustedForm/TCPA become documented *examples* of `type` values in a non-normative appendix, not required core fields. This also naturally accommodates the EU/UK/BR consent-proof vendors that don't exist as named fields today.

**B3 — `phone_hash` provides no real privacy protection.**
§3/§9: `phone_hash = sha256(E.164)`. Global phone number space is small and structured (country code + national number, typically ≤10 digits nationally) — precomputing a rainbow table over all valid E.164 numbers for a given country is a weekend project, not a research problem. Calling this a "hash" implies a privacy property it doesn't have; anyone who receives a `ping` (which is explicitly meant to be safe to share with unvetted buyers) can deanonymize `phone_hash` back to the real number.
*Fix:* Switch to `HMAC-SHA256(shared_secret, e164)`. This preserves dedup capability between parties who share the key while removing the "anyone with a phonebook can reverse this" property. Cheap to adopt now; expensive to migrate later since dedup depends on hash stability. Either way, §9 should explicitly state whether `phone_hash` is a privacy control or merely a dedup convenience — right now it's presented as both and is really only the latter.

**B4 — No published `status` transition graph, despite `LCP-004` depending on one.**
§3 lists 11 status values; §5 defines `LCP-004 INVALID_STATUS_TRANSITION (422)` as an enforceable error. But the valid-transition graph itself isn't in the spec. Two independent, fully-conformant implementers will disagree about whether `DISPUTED → CONVERTED` is legal (real-world disputes do resolve into acceptance) or whether `ARCHIVED` is truly terminal. An error code that references an undefined rule set isn't enforceable — it's decorative.
*Fix:* Publish a full transition table before v1.0: terminal states, non-terminal states, and every legal edge, including the disputed-resolution paths.

**B5 — Enum forward-compatibility is stated for message types and optional fields, but not for `status`, `channel`, or `event`.**
§1 principle 11 ("backward compatibility") and §6 ("unknown optional fields ignored; unknown message types → structured error") cover two of the four places new values will appear over 20 years. They don't say what a v1.0 receiver should do when it sees a `status` or `event` value that didn't exist yet when it was built (e.g. a hypothetical v1.1 `PARTIALLY_REFUNDED`). If the implicit behavior is "reject," every enum addition becomes a de facto MAJOR bump, which contradicts the additive/MINOR framing in §6.
*Fix:* State explicitly that `status`, `channel`, and `event` are **open enumerations** — unrecognized values must be stored/passed through, not treated as validation failures — while `message.type` remains the one **closed** axis that legitimately triggers `LCP-006`.

---

## 3. Historical should-fix findings (resolved or explicitly deferred)

- **Consolidate `call` into `lead` with `channel: call`.** Right now channel is split across two competing taxonomies: `lead.channel` (form|chat|click|api|agent|referral) and a wholly separate `call` message type. As new channels arrive (video, IoT, in-app), each one has to decide which taxonomy it belongs to, and the two will drift. Since `schemas/*.json` haven't been written yet (§13), this is nearly free to fix now and expensive later — recommend doing it before schema authoring starts, not deferring it.
- **`consumer.first_name`/`last_name` assumes a splittable Western name.** Add support for an unstructured `full_name` for the large fraction of the world where given/family-name splitting doesn't cleanly apply.
- **`consumer.gender` in the universal core is questionable.** It reads as inherited from insurance/mortgage underwriting rather than something a gutter lead or a solar lead needs. Either move it to `attributes` (vertical-specific) or, if kept, define its enum/format — currently unspecified.
- **No transport-neutral home for the HMAC signature.** §9 ties `X-LCP-Signature`/`X-LCP-Timestamp` to HTTP headers, but the envelope is explicitly meant to be transport-agnostic (§2's own rationale for keeping `sender_id`/`receiver_id` in-envelope rather than in headers). A queue-based deployment has no headers. Define a canonical `envelope.message.security` block, with the HTTP binding mirroring it into headers for convenience — same pattern as the `test` field below.
- **`test` should be mirrored as an HTTP header, not only an envelope field**, so infrastructure can filter test traffic without parsing JSON bodies. Keep the envelope field canonical (it survives non-HTTP transports); add `X-LCP-Test` as a required mirror for the HTTP binding.
- **Unify the error shape.** §5 shows a singular error body (`{code, message, field, details}`) while `ack.errors[]` (§4) is already an array. A single request/message can fail multiple field validations at once — collapsing that to one error object is lossy. Use `errors[]` everywhere.
- **Missing error codes:** `LCP-011 RATE_LIMITED (429)` — §9 mentions per-sender rate limits but no corresponding code exists. Also split `LCP-002 UNKNOWN_SENDER (401)` into "sender_id not recognized" vs. "sender recognized but signature invalid/timestamp expired" — these are different failure modes (identity vs. replay protection) and currently share a code.
- **No batch submission or push delivery.** `submit_lead`/`query_lead_status` (§10) are single-item and pull-based. An agent submitting or monitoring at volume shouldn't have to poll per-lead. Add `submit_leads_batch` and an optional webhook/event-subscription mechanism (reasonable as an L3 feature), with the MCP binding wrapping it as `subscribe_to_events`.
- **`provenance.agent.attestation` is undefined.** As written it's an unverifiable free-text claim — a real fraud vector once agents can submit leads programmatically at scale, which is exactly the scenario the spec is designing for. Define it as a signed, verifiable token (e.g. a JWT with `agent_id`/`acting_for`/`issuer`/`iat` claims, checkable against a published key).
- **"Agent as consumer" deterministic response contract has no concrete timeout or fallback.** §10 says buyer agents "answer within the post timeout, or treated as timeout" but never states the timeout value or what happens next (auto-`EXPIRED`? re-offer to next buyer?). Needs a number and a defined fallback state.
- **Vertical schemas aren't explicitly barred from reusing core field names.** Once this is an open standard with third-party vertical contributors, nothing stops a vertical schema from shadowing `phone` or `email` inside `attributes`. Add a reserved-namespace rule to the AGENTS.md/README quality gate.
- **No `governance/SECURITY.md`.** `governance/` (per README's layout) has CONTRIBUTING, CLA, and the extension registry, but no responsible-disclosure process — close to mandatory for a protocol whose entire payload is PII, and cheap to add before any public launch.
- **No trademark/usage policy for "LCP compliant" claims.** Conformance is self-declared (§11); nothing governs who can say "LCP-L2 compliant" in marketing. Worth a short policy before wide publication.
- **`GET /v1/lcp/capabilities`'s `markets` field (§8) is unspecified** and risks quietly reintroducing the legacy `au_nz`/`us` grouping the rest of the spec has moved away from. Define it explicitly as an ISO 3166-1 country-code list.
- **Consent captures *that* consent happened, not *what* was consented to.** `consent_timestamp`/`consent_source_url`/`consent_text_version` record occurrence, but not scope (calls? texts? sharing with named partners?). Given this protocol's primary use case is TCPA-exposed verticals, consent *scope* is what actually gets litigated. Add `consent_purposes[]` or equivalent.
- **No lead expiry field**, despite `EXPIRED` existing as a status. Add `expires_at`/`ttl_seconds` to `lead`/`ping`.
- **No exclusivity model.** Shared/non-exclusive lead selling (multiple buyers per lead) is standard industry practice and isn't addressed — `pricing.is_duplicate_resub` is adjacent but answers a different question (accidental resubmission, not intentional multi-sell). Add an explicit `exclusivity` field (`exclusive` | `shared`, optionally `max_buyers`).

---

## 4. Deferred roadmap items (v1.1+, with trigger)

- **Erasure/DSAR message type or event** (right-to-be-forgotten style deletion request tied to `lead_id`/`phone_hash`). *Trigger:* operating in a market with statutory erasure obligations (EU/UK/BR/CA-CCPA), or a counterparty contractually requiring it.
- **Agent-to-agent price negotiation** beyond the current single-shot `floor_price_cents` ping/post auction. *Trigger:* agent-native bidding volume becomes large enough that static floor pricing is materially leaving money on the table versus a negotiation protocol.
- **Formal sensitivity taxonomy** for PII categories beyond the current ad hoc "(sensitive)" tags on `dob`/`gender` — e.g. voiceprints implicit in `call.recording`, behavioral signal in `session_capture_id`. *Trigger:* a market or vertical explicitly regulates one of these categories (e.g. Illinois BIPA-style biometric rules for call recordings).
- **Multi-maintainer governance/steering process** beyond the current single-maintainer model. *Trigger:* external contributors or implementers beyond the originating org adopt LCP.

---

## 5. Historical universal-audit result

Confirmed non-universal fields, all traceable to concrete fixes above:
- `compliance.jornaya_token` / `trustedform_token` / `tcpac_consent` — US-specific vendors and law reference in the *core*. **(B2)**
- `consumer.first_name`/`last_name` — assumes Western-splittable names. **(§3, should-fix)**
- `consumer.gender` — plausibly a vertical-specific (underwriting) field promoted to core without justification against the "simplicity budget" (AGENTS.md rule 2). **(§3, should-fix)**
- `capabilities.markets` (§8) — undefined shape, risk of reintroducing the legacy `au_nz`/`us` grouping the core has otherwise correctly moved away from. **(§3, should-fix)**

Everything else in the canonical core (`location`, `provenance`, `attributes`, `status`) genuinely passes the "gutter lead in Australia / solar lead in the US / call lead in NZ / agent-submitted lead" test as written.

---

## 6. MCP/AI-agent assessment

"REST core + thin MCP wrapper, tools map 1:1 to endpoints" is the right call, and worth stating plainly: it keeps the protocol's future from being bet on MCP's specific longevity, and it means A2A or any future agent protocol maps onto the same abstract binding rules (§10) rather than requiring a redesign. This is a real strength, not just an acceptable choice.

Where the current design underserves agent scale: no batch submission, no push delivery (polling-only `query_lead_status`), and an unverifiable `attestation` claim in exactly the scenario — high-volume automated submission — where fraud resistance matters most. These are should-fix items above, not architectural objections; the wrapper shape itself doesn't need to change to accommodate them.

---

## 7. Naming verdict

"LCP" collides with **Largest Contentful Paint**, one of Google's three Core Web Vitals — an extremely high-search-volume term in web performance and SEO. That's a real discoverability problem, not a paranoid one: a standard whose entire value proposition depends on being easy to find and cite ("the HTTP of lead generation") will spend years fighting an unrelated, much more searched-for acronym for every branded query ("LCP protocol," "LCP spec," "LCP JSON schema"). "LCP" also has pre-existing, if smaller, collisions (Link Control Protocol in PPP networking).

This is cheap to fix now (pre-launch, draft status) and expensive after adoption. Recommend picking either a less-contested acronym (e.g. **LXP** — Lead Exchange Protocol) or a coined, non-acronym name in the style of other successful open protocols/tools (Kafka, gRPC, Terraform) — check trademark and domain availability before committing either way.

---

## 8. Anything we missed entirely

- **Settlement/billing is implicitly out of scope but never says so.** The lifecycle (`accepted`/`rejected`/`disputed`/`refunded`/`converted`) is well modeled, but nothing states whether LCP governs the actual money movement (invoicing, payout) or leaves it to bilateral agreements. One sentence in §1 Overview closes this ambiguity cheaply.
- **No `locale`/language field.** Given genuinely global ambition, free-text fields (consent text, notes) will be non-English; a BCP-47 `locale` tag on `consumer` or `provenance` is a nice-to-have but worth having.
- **Exclusivity/shared-lead selling** — already covered in §3 above, but worth restating here as a business-model gap rather than a field-level nit: this is core to how lead gen actually works commercially (exclusive vs. shared leads), and its absence from the spec is more surprising than most of the smaller field-level gaps.

Everything else requested by the review brief (versioning cadence, conformance tiers, envelope/payload separation, Apache 2.0 + patent clause) held up under adversarial review — I'm not flagging them because they're genuinely sound as written, not because they were skipped.

---

**Previous:** [Deep research prompt](lcp-deep-research-prompt.md) · **Next:** [Publishing gap analysis](publishing-gap-analysis.md)

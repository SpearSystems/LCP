# LCP — Lead Context Protocol Specification

> Part of the normative LCP v1.0 specification: **Conformance**.
> Scope, conformance tiers, governance, universal-core audit, authoring order, and the review log.
> Index: [SPEC.md](../SPEC.md) · File map: [spec/README.md](README.md).

## 1. Overview

LCP is a universal protocol for exchanging consumer lead data between
publishers, platforms, and buyers. It is:

- **Channel-agnostic** — form fills, calls, chats, clicks, API, AI agents.
- **Vertical-agnostic** — the core has zero vertical-specific fields;
  verticals are typed extension blocks.
- **Market-agnostic** — ISO 3166-1 country codes, per-country territory
  and postal validation, ISO 4217 currency.
- **PII-disciplined** — ping messages carry non-PII attributes + hashes
  only; full PII flows only in post messages to the winning buyer.
- **Agent-ready** — agents are first-class senders via a binding layer
  (MCP first), never coupled into the core.

**Scope.** LCP governs lead data exchange — the envelope, payload,
lifecycle, and compliance evidence. It does **not** govern money
movement (settlement, invoicing, payout); financial arrangements are
bilateral between parties. LIP lifecycle statuses (`accepted`,
`rejected`, `disputed`, `refunded`, `converted`) describe the lead's
state, not the payment's state.

### Design principles

1. Universal core, extensible verticals.
2. Channel-agnostic message types.
3. PII discipline (ping/post split).
4. Envelope/payload separation.
5. Semver + per-schema versions + N+2 deprecation.
6. Mandatory idempotency.
7. Structured errors (array-shaped, multi-error).
8. Machine-readable schemas (JSON Schema).
9. Capability discovery.
10. Conformance tiers L1–L3, self-declared.
11. Backward compatibility with open/closed enum policy.

## 7. Conformance tiers

- **L1:** envelope + required fields + idempotency + ack + valid timestamps.
- **L2:** full lifecycle + ping/post split (strict allowlist enforcement)
  + compliance records (consent evidence + purposes).
- **L3:** full + dedup fingerprints + capability discovery + published
  JSON Schemas + agent binding (attestation verification).

## 11. Governance

- Apache 2.0 (patent retaliation clause rationale).
- Anti-capture clause: no mandatory fees, approvals, or proprietary
  dependencies.
- Extension registry: namespace registration, format-only validation.
- No mandatory certification; conformance self-declared.
- CLA for contributors.

See [governance/](../governance/).

## 12. Universal audit

The core must work for a gutter lead in Australia, a solar lead in the
US, a call lead in NZ, and a lead submitted by an AI agent — with zero
core changes. Audit checklist:

- [ ] No vertical-specific fields in core.
- [ ] No market-specific fields in core — **including named sub-fields
  of `compliance`** (no vendor names like `jornaya_token` or
  law references like `tcpac_consent` as core fields; use
  `consent_evidence[]` with generic `type`/`provider`).
- [ ] `consumer` does not assume Western name splitting (`full_name`
  available as alternative to `first_name`/`last_name`).
- [ ] `consumer.gender` is NOT in core (moved to vertical `attributes`).
- [ ] Phone E.164 universal; `phone_hash` is HMAC-SHA256 (per-pair).
- [ ] Call leads first-class (`call` message type).
- [ ] AI-agent submissions: `channel=agent`, `source_type=agent`.
- [ ] Ping is a strict allowlist (`additionalProperties: false`), not a
  blocklist.
- [ ] Vertical schemas tag every field `ping_safe: true/false`.
- [ ] Vertical schemas do not shadow core field names in `attributes`.
- [ ] `capabilities.countries` is ISO 3166-1 alpha-2 codes (no legacy
  `markets` grouping).
- [ ] Status transition table is published and matches §3.
- [ ] Open/closed enum policy is stated (§6).
- [ ] Legacy production wire contract fields map 1:1 into LCP.

## 13. TODO (authoring order)

- [x] Run the deep-research review pass (`docs/lcp-deep-research-prompt.md`).
- [x] Resolve or explicitly defer every finding (see §14).
- [x] Fill envelope + core schemas (`schemas/*.json`).
- [x] Fill message-type schemas (lead, call, ping, post, ack, event).
- [x] Fill vertical schemas — mortgage first (`verticals/mortgage.json`).
- [x] Tag every vertical attribute field `ping_safe: true/false`.
- [x] Fill examples (`examples/*.json`).
- [x] Fill test vectors (`test-vectors/`).
- [x] Write conformance runner (L1/L2/L3) — 30/30 pass, including the post-release sensitive-ping vectors.
- [x] Write reference MCP server (`implementations/mcp-server/`).
- [x] Add `governance/SECURITY.md` (responsible disclosure).
- [x] Add trademark/usage policy for "LCP compliant" claims.
- [x] Fill extension registry format (namespace, registration, payload location).
- [x] Write CLA full text (`governance/CLA.md`).
- [x] Publish decision — repository is public under Apache 2.0 (see [RELEASE.md](../docs/RELEASE.md) for the tagged release record).
- [ ] Spec site (LEX-style) — trigger-gated: publish only once a maintained
  canonical-source workflow exists (see §14); not part of v1.1.

## 14. Review-log appendix

Findings from the deep-research review
(`docs/lcp-deep-research-review.md`) and their resolution:

### Blockers — all resolved

| ID | Finding | Resolution | Section |
|---|---|---|---|
| B1 | Ping PII safety is a blocklist | Replaced with strict allowlist (`additionalProperties: false`) + `ping_safe` tagging | §4 ping, §12 |
| B2 | Compliance block hardcodes US vendors | Replaced with generic `consent_evidence[]` array; vendors are examples | §3 compliance |
| B3 | `phone_hash` unsalted sha256 (trivially reversible) | Switched to per-pair `HMAC-SHA256(shared_secret, e164)` | §9 |
| B4 | No status transition graph | Published full transition table with terminal states + resolution paths | §3 status |
| B5 | Open/closed enum policy unstated | Added open/closed enum table: `message.type` closed, all others open | §6 |

### Should-fix — resolved in v1.0

| Finding | Resolution | Section |
|---|---|---|
| `call` vs `lead` consolidation | Kept separate; design note added for contributors | §4 |
| `full_name` for non-Western names | Added to `consumer` | §3 |
| `consumer.gender` in core | Removed; moved to vertical `attributes` | §3 |
| No transport-neutral security home | Added `payload.message.security` block | §9 |
| `test` not mirrored as header | Added `X-LCP-Test` header requirement | §2 |
| Error shape inconsistency (singular vs. array) | Unified to `errors[]` everywhere | §5 |
| Missing `LCP-011 RATE_LIMITED` | Added (429) | §5 |
| `LCP-002` conflates identity + signature | Split into `LCP-002` (identity) + `LCP-012` (signature) | §5 |
| `attestation` is free text | Defined as signed JWT with verifiable claims | §9 |
| Agent-as-consumer timeout undefined | Defined as 30s default with auto-`EXPIRED` fallback | §9 |
| Vertical schemas can shadow core field names | Reserved-namespace rule added | §3 attributes, §12 |
| `capabilities.markets` undefined | Replaced with `countries` (ISO 3166-1 alpha-2) | §8 |
| Consent captures *that*, not *what* | Added `consent_purposes[]` | §3 compliance |
| No lead expiry field | Added `expires_at` / `ttl_seconds` | §3 |
| No exclusivity model | Added `exclusivity` + `max_buyers` | §3 |
| Settlement/billing scope ambiguous | Added scope statement to §1 | §1 |

### Should-fix — open (trigger-gated, unscheduled)

v1.1.0 delivered the LEP-driven set (canonical timestamps and cross-SDK
interop, shared-lead slots, request history, extensions and
`FINAL_PRICING`, attestation verification). The findings below remain open
opportunities gated on the trigger in each row; none is a commitment to
v1.1 or v1.2.

| Finding | Trigger | Planned resolution |
|---|---|---|
| Batch submission (`submit_leads_batch`) | Agent submission volume grows | Add as MCP tool + REST endpoint |
| Push delivery (webhook/event subscription) | Polling at volume becomes costly | Add `subscribe_to_events` MCP tool + `POST /v1/lcp/events/subscribe` |
| Platform quality score in ping/post | Platforms want to share independent scoring | Extensions namespace `{org}.platform.assessment` |
| Delivery receipt (`PROCESSED` ack status) | Buyer CRM sync failures need detection | Add `PROCESSED` to ack status enum |
| Vertical-specific buyer criteria in offers | Complex matching rules per vertical | Extensions namespace `{org}.buyer.criteria` |
| Raw IP address in post (full PII context) | Fraud detection + geo-validation | Add `ip_address` to provenance (post only, not ping) |
| Raw form submission data | Dispute evidence — what consumer actually entered | Extensions namespace `{org}.intake.raw_form` |
| Multi-currency pricing | Cross-border lead-gen (US buyer purchasing AU leads) | `price_cents_original` + `fx_rate` fields, or bilateral |
| Core schema version in envelope | Schema-level breaking change detection | `core_schema_version` in message block, or document that protocol version = core schema version |
| Address fields in core (`street_address`) | Multiple verticals need consumer residential address | Add to `location` or document as vertical-specific |
| Spec site (LEX-style) | Adoption grows beyond GitHub | Static site render of SPEC.md |

### Deferred to v1.1+ (with trigger)

| Finding | Trigger |
|---|---|
| Agent-to-agent price negotiation | Agent bidding volume large enough that static floor pricing is suboptimal |
| Formal sensitivity taxonomy for PII | A market regulates a category not covered (e.g. biometric) |
| Multi-maintainer governance | External contributors adopt LCP |
| LEP (LCP Enhancement Proposal) process | Community growth requires structured proposal workflow |
| Hosted conformance sandbox | Self-hosted conformance runner is insufficient for certification |
| Split SPEC.md into focused documents | Spec exceeds ~1000 lines or gets second major section |

### Naming

The review flagged that "LCP" collides with Largest Contentful Paint
(Google Core Web Vitals). This is a real discoverability concern. The
decision is to **keep the name "LCP"** — the collision is a marketing/
SEO problem, not a protocol problem. Mitigation: consistent "Lead
Context Protocol" branding, a spec site with topical authority, and
disambiguation in public materials.

---

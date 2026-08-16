# LCP Publishing Gap Analysis
> **Research page · Page 6 of 6**
>
> **Status: resolved.** All high-priority and medium-priority gaps in this
> analysis have been addressed (OpenAPI spec, CODE_OF_CONDUCT, CHANGELOG,
> MAINTAINERS, ADOPTERS, GitHub templates, AI contribution policy, trust
> model, bid message type, vertical schemas, platform integration guide).
> This file is kept for provenance. It is a historical snapshot, not a
> current gap list. Subsequent work added the OpenAPI contract, CHANGELOG,
> governance files, Docker sandbox, Python SDK, and reference platform.
> The hosted conformance service and other lower-priority items remain tracked
> in SPEC.md §14 deferred-item tables.

**Date:** 2026-08-15
**Purpose:** Historical pre-v1 audit of patterns LCP could borrow from LEX and MCP. The repository is now public and v1.0.0 is released; the checklist at the end is the current post-release backlog.

---

## Summary

LCP is structurally solid — spec, schemas, test vectors, conformance runner, MCP server, and governance docs all exist. The gaps below are things that established protocol repos (LEX, MCP) include that would make LCP more polished, discoverable, and contributor-ready at launch.

---

## A. Historical high-priority gaps (closed before v1.0)

### 1. OpenAPI / AsyncAPI spec for the HTTP binding
**Source:** LEX has `api/lex-openapi.yaml` (OpenAPI 3.1) + `api/lex-asyncapi.yaml` (AsyncAPI 3.0)
**Historical LCP status:** SPEC.md §8 defined the binding before the machine-readable contract was added. The repository now ships `api/lcp-openapi.yaml`.
**Recommendation:** Add `api/lcp-openapi.yaml` covering the HTTP transport binding — submit lead, submit call, query status, get capabilities, get schema. This lets adopters generate clients in any language and try the API in Swagger UI. The MCP server's tool definitions can serve as a starting point.
**Priority:** HIGH — adopters expect a swaggerable API.

### 2. Quick-start code samples in multiple languages
**Source:** LEX ships quick-start snippets for Python, JavaScript, Java, and C#, plus working sample apps in `samples/` (python-fastapi, nodejs-express, java-springboot, dotnet-steeltoe).
**Historical LCP status:** The README and integration examples now include conformance, SDK, HTTP, Python, Node.js, cURL, and buyer-webhook starting points.
**Recommendation:** Add at minimum a Python quick-start to the README (create envelope → validate → send → receive ack). A `samples/` directory with one working receiver (even a simple Flask/FastAPI echo validator) would dramatically lower the barrier to first implementation.
**Priority:** HIGH — first-impression developer experience.

### 3. Field dictionary / data dictionary
**Source:** LEX has `specs/LEX_FIELD_DICTIONARY.md` — every field with type, constraints, and examples in one place.
**LCP status:** The canonical SPEC.md and machine-readable schemas remain normative, and `docs/FIELD-DICTIONARY.md` now provides the human-readable v1.0 field reference.
**Recommendation:** Either add a `docs/FIELD_DICTIONARY.md` or append a field reference table to SPEC.md. This is the document a developer implementing a parser will keep open.
**Priority:** HIGH — reduces implementation ambiguity.

### 4. Separate spec documents (split the monolith)
**Source:** LEX splits into 8 focused docs: `LEX_SPECIFICATION.md`, `LEX_FIELD_DICTIONARY.md`, `LEX_MESSAGE_TYPES.md`, `LEX_PRODUCT_MODEL.md`, `LEX_EXTENSION_STANDARD.md`, `LEX_CONFORMANCE.md`, `LEX_CONSENT_MODEL.md`, `LEX_DEDUPLICATION.md`. MCP also separates spec, schema, and docs.
**LCP status:** Everything is in one 677-line `SPEC.md`.
**Recommendation:** Consider splitting into at least: `SPEC.md` (core: envelope, message types, errors, versioning), `CONFORMANCE.md` (tiers + test vector mapping), `SECURITY.md` (auth, PII, phone hash — currently in governance/ but the spec sections §9 are normative). The single-file approach is fine for v1.0 launch, but flag it for v1.1.
**Priority:** MEDIUM — not blocking, but helps as the spec grows.

---

## B. Historical medium-priority gaps (closed or intentionally deferred)

### 5. CHANGELOG.md
**Source:** MCP has a published changelog per spec version (`/specification/2026-07-28/changelog`), tracking all changes since previous revision.
**Historical LCP status:** `CHANGELOG.md` now records the v1.0.0 release and the changes leading to it.
**Recommendation:** Add `CHANGELOG.md` at repo root. First entry: `v1.0.0 — initial publication`. This establishes the habit early.
**Priority:** MEDIUM — expected in any published repo.

### 6. CODE_OF_CONDUCT.md
**Source:** MCP has `CODE_OF_CONDUCT.md` and references it in contributing docs. LEX doesn't appear to have one.
**Historical LCP status:** `governance/CODE_OF_CONDUCT.md` is present and linked from the contribution guidance.
**Recommendation:** Add `governance/CODE_OF_CONDUCT.md` (Contributor Covenant 2.1 is standard, minimal, and well-recognized). Link from CONTRIBUTING.md.
**Priority:** MEDIUM — standard for community projects.

### 7. MAINTAINERS.md
**Source:** MCP has `MAINTAINERS.md` listing core maintainers, referenced from CONTRIBUTING, AGENTS, and governance docs.
**Historical LCP status:** `MAINTAINERS.md` identifies the current single maintainer and records the future multi-maintainer gap.
**Recommendation:** Add `MAINTAINERS.md` at repo root listing the initial maintainer(s). Even a single-maintainer file establishes the governance contact point.
**Priority:** MEDIUM — needed for security disclosures and contributor routing.

### 8. GitHub issue/PR templates
**Source:** MCP has `.github/ISSUE_TEMPLATE/` with forms, `.github/PULL_REQUEST_TEMPLATE.md`, and `config.yml` redirecting certain issue types. Blank issues are disabled.
**Historical LCP status:** GitHub issue/PR templates, security links, release workflows, and the attribution-policy workflow are present under `.github/`.
**Recommendation:** Add at minimum:
- `.github/PULL_REQUEST_TEMPLATE.md` — checklist (CLA signed, examples updated, schemas validate, test vectors pass)
- `.github/ISSUE_TEMPLATE/bug-report.md` and `feature-request.md`
- `.github/ISSUE_TEMPLATE/config.yml` — contact links for security (point to SECURITY.md)
**Priority:** MEDIUM — reduces noise on launch.

### 9. Dual licensing (spec CC-BY-4.0, code Apache-2.0)
**Source:** LEX uses Apache 2.0 for software + CC BY 4.0 for specifications. MCP uses MIT for the repo but Apache 2.0 for contributions.
**LCP status:** Apache 2.0 for everything. The spec text is under the same license as the code.
**Recommendation:** Consider adding `specs/LICENSE` (CC BY 4.0) for the specification documents, keeping Apache 2.0 for code/schemas. This is the pattern used by W3C, IETF, and LEX. It allows free redistribution of the spec text while keeping the patent protections of Apache 2.0 for implementations. Not blocking, but it's the industry norm for standards bodies.
**Priority:** LOW-MEDIUM — nice to have, not urgent for v1.0.

### 10. AI contribution disclosure policy
**Source:** MCP's CONTRIBUTING.md has an explicit "AI Contributions" section requiring disclosure of AI-assisted PRs. MCP's AGENTS.md restricts agent-submitted PRs to trusted maintainers or contributors with 3+ merged PRs.
**Historical LCP status:** `governance/CONTRIBUTING.md` requires AI disclosure in the PR, and the attribution-policy workflow rejects generated commit trailers.
**Recommendation:** Add a short "AI Contributions" section to CONTRIBUTING.md requiring disclosure. LCP is itself AI-authored, so this is both principled and practical.
**Priority:** MEDIUM — increasingly expected.

---

## C. Lower-priority / aspirational gaps

### 11. Spec versioning approach (date-based vs semver)
**Source:** MCP uses date-based versioning (YYYY-MM-DD) for spec versions — `schema/2025-11-25/`, `schema/2026-07-28/`. LEX uses semver (`lexVersion: "1.0.0"`).
**LCP status:** Semver (`version: "1.0.0"` in envelope). This is fine — LCP's semver + N+2 deprecation is well-reasoned. No change needed, but worth noting that MCP's date-based approach is an alternative for when the spec gets multiple revisions per year.
**Priority:** NONE — LCP's choice is deliberate and documented.

### 12. Specification Enhancement Proposal (SEP) process
**Source:** MCP has a formal `seps/` directory with a `TEMPLATE.md` and 30+ proposals. Each protocol change goes through SEP → prototype → review → merge.
**LCP status:** No formal proposal process. Changes are PR-based with governance/CONTRIBUTING.md.
**Recommendation:** Not needed for v1.0. When the community grows, consider an "LCP Enhancement Proposal" (LEP) process. Document the idea in governance/ for now.
**Priority:** LOW — premature for a launching standard.

### 13. Sandbox / conformance testing endpoint
**Source:** LEX defines a sandbox endpoint spec (`sandbox.lexstandard.org`) with deterministic test behaviors and a `/conformance/run` endpoint that issues certification tokens.
**Historical LCP status:** At the time of this analysis, conformance was only a local Python runner (`test-vectors/conformance.py`). The repository now also includes a Docker-based self-hosted sandbox; a hosted conformance service remains a v1.1+ consideration.
**Historical recommendation:** Keep the local runner and self-hosted Docker sandbox as the v1 path; consider hosted certification infrastructure later.
**Priority:** LOW — aspirational.

### 14. Batch endpoint
**Source:** LEX OpenAPI defines `POST /messages/batch` (up to 100 messages per call, non-transactional).
**LCP status:** SPEC.md §10 TODO mentions `submit_leads_batch` as planned for v1.1.
**Recommendation:** No change — already tracked. Note that LEX's non-transactional batch semantics (some succeed, some fail) is the right pattern when LCP adds it.
**Priority:** NONE — already planned.

### 15. Subscription / webhook filtering message type
**Source:** LEX has a `SUBSCRIPTION` message type for registering routing preferences and webhook filters.
**LCP status:** No subscription message type. The `event` message type covers lifecycle notifications but not filter registration.
**Recommendation:** Consider for v1.1 — buyers registering interest filters (verticals, countries, price bands) is a natural LCP extension. Not needed for v1.0 launch.
**Priority:** LOW — future.

### 16. Multi-format support (XML-EDI, X12, EDIFACT)
**Source:** LEX supports 4 wire formats: JSON-EDI, XML-EDI, X12, EDIFACT. Same data model, different serialization.
**LCP status:** JSON only.
**Recommendation:** LCP is a modern protocol — JSON-only is the right call. Enterprise EDI formats are for legacy DMS/ERP integration which is LEX's automotive-retail focus. LCP's audience (lead-gen platforms, publishers, buyers) speaks JSON. No change needed.
**Priority:** NONE — deliberate design choice.

### 17. Deduplication fingerprint spec
**Source:** LEX has a dedicated `LEX_DEDUPLICATION.md` with `customerFingerprint` (SHA-256 hash of normalized email+phone+lastName), `deduplicationWindowHours`, `crossPlatformIds[]`, and a receiver decision flow.
**LCP status:** `phone_hash` (per-pair HMAC-SHA256) is defined in SPEC.md §9. No global fingerprint, no dedup window, no cross-platform ID linkage.
**Recommendation:** LCP's per-pair hash is privacy-stronger (no cross-buyer dedup) but LEX's `deduplicationWindowHours` concept is worth borrowing — it's a sender hint that tells receivers how aggressively to dedup. Consider adding an optional `dedup_window_hours` hint field to the ping message. Also, `crossPlatformIds[]` is a useful pattern for aggregator scenarios.
**Priority:** LOW — consider for v1.1.

### 18. Consent model depth (multi-jurisdiction)
**Source:** LEX has a massive `LEX_CONSENT_MODEL.md` covering TCPA, GDPR, UK GDPR, CCPA, DPDPA (India), PIPL (China), LGPD (Brazil), POPIA (South Africa), PIPEDA (Canada), PDPA (Singapore), APPI (Japan), nFADP (Switzerland), PIPA-K (Korea), PDPL (Saudi), PDPA (Thailand) — each with structured fields.
**LCP status:** `consent_evidence[]` array with generic `{type, provider, token_or_url}` + `consent_purposes[]`. This is deliberately generic (no vendor-specific fields in core), which is the right design — LEX's approach bakes 15 jurisdiction-specific blocks into the spec, which is heavy and will need constant updates.
**Recommendation:** LCP's approach is better for a universal protocol. The `consent_evidence.type` open enum can accommodate `tcpa_consent`, `gdpr_consent`, etc. without core changes. Consider adding a companion doc `docs/CONSENT_GUIDE.md` with recommended `consent_evidence.type` values per jurisdiction — advisory, not normative.
**Priority:** LOW — advisory doc, not core change.

### 19. Trust model / intended behaviors document (completed)
**Source:** MCP's SECURITY.md has a detailed "Intended Behaviors and Trust Model" section — what's NOT a vulnerability, trust assumptions, developer/operator responsibilities.
**LCP status:** `governance/SECURITY.md` now includes an explicit Trust Model section covering ping/post separation, per-pair HMAC, agent attestation, open enumerations, and deployment-level rate limiting.
**Recommendation:** Add a "Trust Model" section to SECURITY.md clarifying: ping/post PII split is a design property, not a vulnerability to "bypass"; HMAC per-pair hashing is intentional; agent attestation is the trust boundary for AI-submitted leads. This triages future security reports.
**Priority:** MEDIUM — saves maintainer time on invalid reports.

### 20. Spec site / documentation website
**Source:** LEX has `lexstandard.org` (Cloudflare Pages). MCP has `modelcontextprotocol.io` (Mintlify).
**LCP status:** SPEC.md §13 intentionally defers the spec site to v1.1; the repository is public and the user has separately deferred the website and announcement.
**Recommendation:** Build a static site when adoption work begins, using SPEC.md as the canonical source rather than maintaining a second normative copy.
**Priority:** MEDIUM — non-blocking, user-deferred.

---

## D. Patterns LCP already has (no action needed)

These are things LCP already does well, confirmed by comparison:

| Feature | LCP | LEX | MCP |
|---------|-----|-----|-----|
| Apache 2.0 license | ✅ | ✅ | ✅ (MIT repo, Apache contributions) |
| Anti-capture clause | ✅ (CONTRIBUTING) | ✅ (explicit NS-RULE-*) | ✅ (governance SEP) |
| CLA | ✅ (full text) | ✅ (referenced) | ✅ (in CONTRIBUTING) |
| Conformance tiers L1-L3 | ✅ | ✅ | N/A (different model) |
| Test vectors | ✅ (27 vectors, runner) | ✅ (20 test cases) | ✅ (schema examples) |
| Extension registry | ✅ | ✅ (more detailed) | ✅ (via `_meta` + SEPs) |
| Security policy | ✅ | ✅ (basic) | ✅ (very detailed) |
| Trademark/usage policy | ✅ | ❌ | ❌ |
| Ping/post PII split | ✅ (strict allowlist) | N/A | N/A |
| Agent binding (MCP) | ✅ (reference impl) | ❌ | N/A (MCP IS the protocol) |
| JSON Schema (Draft 2020-12) | ✅ | ✅ | ✅ (from TypeScript) |
| Idempotency | ✅ (envelope-level) | ✅ (messageId-level) | N/A |
| Open/closed enum policy | ✅ (documented §6) | ✅ (implicit) | ✅ (schema-level) |

---

## E. Current post-v1 action checklist

### Completed before v1.0.0

- [x] OpenAPI 3.1 HTTP binding: `api/lcp-openapi.yaml`.
- [x] Python, Node.js, cURL, buyer-webhook, SDK, and conformance quickstarts.
- [x] Human-readable field dictionary in `docs/FIELD-DICTIONARY.md`.
- [x] `CHANGELOG.md`, `MAINTAINERS.md`, Code of Conduct, issue/PR templates,
      AI contribution disclosure, and the security Trust Model.
- [x] Schemas, examples, conformance vectors, SDKs, reference platform, MCP
      adapter, Docker sandbox, release evidence, and trusted publication gates.

### Remaining, non-blocking improvements

1. [x] Add the v1.0 field dictionary at `docs/FIELD-DICTIONARY.md`.
2. [x] Add the advisory consent guide at `docs/CONSENT-GUIDE.md`.
3. [ ] Add the user-deferred spec website and public announcement.
4. [ ] Consider dual licensing for normative specification text if a standards
       community later needs a separate documentation license.
5. [ ] Add an LEP proposal process, hosted conformance service, or split the
       canonical spec only when community scale justifies the additional surface.
6. [ ] Add batch submission, subscription/filter registration, and dedup-window
       guidance as v1.1+ features or docs; maintain the v1.0 consent guide.
7. [ ] Add a second maintainer/reviewer and strengthen release independence.

---

## F. Key architectural differences to preserve (don't copy these from LEX)

- **LCP's consent model is generic** (`consent_evidence[]` with open `type` enum) vs LEX's 15 jurisdiction-specific blocks. LCP's approach is lighter and more future-proof — don't bloat the core.
- **LCP's phone_hash is per-pair HMAC** vs LEX's global SHA-256 fingerprint. LCP's is privacy-stronger — don't switch to a global hash.
- **LCP is JSON-only** vs LEX's 4 wire formats. LCP's audience doesn't need X12/EDIFACT.
- **LCP uses semver** vs MCP's date-based versioning. LCP's semver + N+2 deprecation is well-reasoned for a lead-exchange protocol.
- **LCP's `consent_evidence[]` replaces vendor-specific core fields** — this is a deliberate design decision (review blocker B5). LEX puts `tcpa`, `gdpr`, `ccpa` etc. as top-level spec fields. LCP's generic approach is better for a universal protocol.

---

## G. Focused v1.1 roadmap

The following work is intentionally post-v1.0. It should be proposed and
versioned as additive changes rather than quietly changing the v1.0 contract.

### Phase 1 — governance and adoption readiness

- Add a second maintainer/reviewer and enable independent release approval.
- Define a lightweight LEP process when external contributors begin proposing
  protocol changes.
- Publish the user-deferred spec website and announcement from the canonical
  `SPEC.md`, without creating a second normative source.
- Maintain the v1.0 field dictionary and extend the advisory consent guide if adopters need additional jurisdiction coverage.

### Phase 2 — higher-volume delivery

- Specify `submit_leads_batch` with bounded, non-transactional semantics.
- Define event subscriptions/webhooks and filtering without changing the core
  ping/post privacy boundary.
- Evaluate an optional deduplication-window hint and document its privacy scope.

### Phase 3 — ecosystem scale

- Offer a hosted conformance service only if self-hosted vectors are not enough
  for adopters or certification partners.
- Split the canonical specification only if its size or contributor workflow
  justifies multiple normative documents.
- Revisit multi-maintainer governance and community decision records as adoption
  expands.

Every v1.1 candidate should first update schemas, examples, conformance vectors,
SDK generated models, and the compatibility matrix, then use a new immutable
minor tag. None of these roadmap items changes the published v1.0.0 artifacts.

---

**Previous:** [Deep research review](lcp-deep-research-review.md) · **Next:** [Documentation home](README.md)

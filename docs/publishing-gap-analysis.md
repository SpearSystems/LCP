# LCP Publishing Gap Analysis

**Date:** 2026-08-15
**Purpose:** Patterns and features LCP should borrow from LEX and MCP spec repos before publishing to GitHub.

---

## Summary

LCP is structurally solid — spec, schemas, test vectors, conformance runner, MCP server, and governance docs all exist. The gaps below are things that established protocol repos (LEX, MCP) include that would make LCP more polished, discoverable, and contributor-ready at launch.

---

## A. High-priority gaps (do before publishing)

### 1. OpenAPI / AsyncAPI spec for the HTTP binding
**Source:** LEX has `api/lex-openapi.yaml` (OpenAPI 3.1) + `api/lex-asyncapi.yaml` (AsyncAPI 3.0)
**LCP status:** SPEC.md §8 defines `GET /v1/lcp/capabilities` and implies POST endpoints, but there is no machine-readable API contract.
**Recommendation:** Add `api/lcp-openapi.yaml` covering the HTTP transport binding — submit lead, submit call, query status, get capabilities, get schema. This lets adopters generate clients in any language and try the API in Swagger UI. The MCP server's tool definitions can serve as a starting point.
**Priority:** HIGH — adopters expect a swaggerable API.

### 2. Quick-start code samples in multiple languages
**Source:** LEX ships quick-start snippets for Python, JavaScript, Java, and C#, plus working sample apps in `samples/` (python-fastapi, nodejs-express, java-springboot, dotnet-steeltoe).
**LCP status:** README has a conformance runner command and MCP server command. No "send your first lead" code.
**Recommendation:** Add at minimum a Python quick-start to the README (create envelope → validate → send → receive ack). A `samples/` directory with one working receiver (even a simple Flask/FastAPI echo validator) would dramatically lower the barrier to first implementation.
**Priority:** HIGH — first-impression developer experience.

### 3. Field dictionary / data dictionary
**Source:** LEX has `specs/LEX_FIELD_DICTIONARY.md` — every field with type, constraints, and examples in one place.
**LCP status:** Fields are defined inline in SPEC.md sections (§2 envelope, §3 core, §4 message types) but there's no single reference table.
**Recommendation:** Either add a `docs/FIELD_DICTIONARY.md` or append a field reference table to SPEC.md. This is the document a developer implementing a parser will keep open.
**Priority:** HIGH — reduces implementation ambiguity.

### 4. Separate spec documents (split the monolith)
**Source:** LEX splits into 8 focused docs: `LEX_SPECIFICATION.md`, `LEX_FIELD_DICTIONARY.md`, `LEX_MESSAGE_TYPES.md`, `LEX_PRODUCT_MODEL.md`, `LEX_EXTENSION_STANDARD.md`, `LEX_CONFORMANCE.md`, `LEX_CONSENT_MODEL.md`, `LEX_DEDUPLICATION.md`. MCP also separates spec, schema, and docs.
**LCP status:** Everything is in one 677-line `SPEC.md`.
**Recommendation:** Consider splitting into at least: `SPEC.md` (core: envelope, message types, errors, versioning), `CONFORMANCE.md` (tiers + test vector mapping), `SECURITY.md` (auth, PII, phone hash — currently in governance/ but the spec sections §9 are normative). The single-file approach is fine for v1.0 launch, but flag it for v1.1.
**Priority:** MEDIUM — not blocking, but helps as the spec grows.

---

## B. Medium-priority gaps (strengthen before/shortly after publishing)

### 5. CHANGELOG.md
**Source:** MCP has a published changelog per spec version (`/specification/2026-07-28/changelog`), tracking all changes since previous revision.
**LCP status:** No CHANGELOG. SPEC.md §14 has a review-log appendix (blockers/should-fixes), but that's review findings, not version-to-version changes.
**Recommendation:** Add `CHANGELOG.md` at repo root. First entry: `v1.0.0 — initial publication`. This establishes the habit early.
**Priority:** MEDIUM — expected in any published repo.

### 6. CODE_OF_CONDUCT.md
**Source:** MCP has `CODE_OF_CONDUCT.md` and references it in contributing docs. LEX doesn't appear to have one.
**LCP status:** No code of conduct.
**Recommendation:** Add `governance/CODE_OF_CONDUCT.md` (Contributor Covenant 2.1 is standard, minimal, and well-recognized). Link from CONTRIBUTING.md.
**Priority:** MEDIUM — standard for community projects.

### 7. MAINTAINERS.md
**Source:** MCP has `MAINTAINERS.md` listing core maintainers, referenced from CONTRIBUTING, AGENTS, and governance docs.
**LCP status:** No maintainers file. "The maintainers" is referenced generically in CLA, SECURITY, CONTRIBUTING.
**Recommendation:** Add `MAINTAINERS.md` at repo root listing the initial maintainer(s). Even a single-maintainer file establishes the governance contact point.
**Priority:** MEDIUM — needed for security disclosures and contributor routing.

### 8. GitHub issue/PR templates
**Source:** MCP has `.github/ISSUE_TEMPLATE/` with forms, `.github/PULL_REQUEST_TEMPLATE.md`, and `config.yml` redirecting certain issue types. Blank issues are disabled.
**LCP status:** No `.github/` directory at all.
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
**LCP status:** No AI contribution policy. AGENTS.md exists but is a repo guide for coding agents, not a contribution policy.
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
**LCP status:** Conformance is a local Python runner (`test-vectors/conformance.py`). No hosted sandbox.
**Recommendation:** A hosted sandbox is a v1.1+ feature. For v1.0, document in the README that the conformance runner is the test path. Consider a Docker-based self-hosted sandbox later.
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

### 19. Trust model / intended behaviors document
**Source:** MCP's SECURITY.md has a detailed "Intended Behaviors and Trust Model" section — what's NOT a vulnerability, trust assumptions, developer/operator responsibilities.
**LCP status:** SECURITY.md has scope, PII-specific guidance, and disclosure timeline — good, but no explicit "this is by design, not a vulnerability" section.
**Recommendation:** Add a "Trust Model" section to SECURITY.md clarifying: ping/post PII split is a design property, not a vulnerability to "bypass"; HMAC per-pair hashing is intentional; agent attestation is the trust boundary for AI-submitted leads. This triages future security reports.
**Priority:** MEDIUM — saves maintainer time on invalid reports.

### 20. Spec site / documentation website
**Source:** LEX has `lexstandard.org` (Cloudflare Pages). MCP has `modelcontextprotocol.io` (Mintlify).
**LCP status:** SPEC.md §13 TODO: "Spec site (LEX-style)" — already tracked.
**Recommendation:** A simple static site (even a single-page render of SPEC.md with a sidebar) significantly improves perceived maturity. Can be a GitHub Pages site from the repo. Not blocking for repo publication but should follow within days.
**Priority:** MEDIUM — already tracked, prioritize after repo is public.

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

## E. Recommended action checklist (sorted by priority)

### Before publishing (blocking):
1. **Add OpenAPI spec** for HTTP binding → `api/lcp-openapi.yaml`
2. **Add Python quick-start** to README + a `samples/` dir with one working validator
3. **Add field dictionary** → `docs/FIELD_DICTIONARY.md` or appendix to SPEC.md

### Before or within 1 week of publishing:
4. **Add CHANGELOG.md** at repo root
5. **Add MAINTAINERS.md** at repo root
6. **Add CODE_OF_CONDUCT.md** (Contributor Covenant 2.1)
7. **Add GitHub PR/issue templates** → `.github/`
8. **Add trust model section** to SECURITY.md
9. **Add AI contribution disclosure** to CONTRIBUTING.md
10. **Spec site** — even a simple GitHub Pages render

### Post-launch (v1.1+):
11. Dual license (CC BY 4.0 for spec text)
12. Dedup window hint field
13. Batch endpoint
14. Subscription/filter message type
15. SEP/LEP proposal process
16. Hosted conformance sandbox
17. Split SPEC.md into focused documents

---

## F. Key architectural differences to preserve (don't copy these from LEX)

- **LCP's consent model is generic** (`consent_evidence[]` with open `type` enum) vs LEX's 15 jurisdiction-specific blocks. LCP's approach is lighter and more future-proof — don't bloat the core.
- **LCP's phone_hash is per-pair HMAC** vs LEX's global SHA-256 fingerprint. LCP's is privacy-stronger — don't switch to a global hash.
- **LCP is JSON-only** vs LEX's 4 wire formats. LCP's audience doesn't need X12/EDIFACT.
- **LCP uses semver** vs MCP's date-based versioning. LCP's semver + N+2 deprecation is well-reasoned for a lead-exchange protocol.
- **LCP's `consent_evidence[]` replaces vendor-specific core fields** — this is a deliberate design decision (review blocker B5). LEX puts `tcpa`, `gdpr`, `ccpa` etc. as top-level spec fields. LCP's generic approach is better for a universal protocol.
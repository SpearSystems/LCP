# Changelog

All notable changes to LCP (Lead Context Protocol) are documented in this
file. The format is based on [Keep a Changelog](https://keepachangelog.com/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- OpenAPI 3.1 specification for the HTTP transport binding (`api/lcp-openapi.yaml`)
- Code of Conduct (Contributor Covenant v2.1)
- Maintainers file
- Adopters file
- GitHub issue and pull request templates
- Trust model section in SECURITY.md
- AI contribution disclosure policy in CONTRIBUTING.md

## [1.0.0-draft] — 2026-08-15

### Added
- LCP specification (SPEC.md) — 14 sections + 2 appendices
  - Envelope/payload separation with transport-agnostic design
  - Canonical core: consumer (full_name, locale, no gender), location,
    compliance (consent_evidence[] array, consent_purposes[]), provenance
    (agent block), attributes, status, channel
  - 6 message types: lead, call, ping, post, ack, event
  - Error taxonomy (14 error codes, errors[] array shape)
  - Open/closed enum policy (message.type closed; status, channel, event open)
  - Status transition graph with terminal states + resolution paths
  - Security: per-pair HMAC-SHA256 phone hash, agent attestation (JWT),
    30s agent-as-consumer timeout, transport-neutral security block
  - Agent binding (MCP first, abstract binding rules for A2A)
  - Conformance tiers L1–L3, self-declared
  - Capability discovery (countries as ISO 3166-1 alpha-2)
  - Governance: Apache 2.0, anti-capture, extension registry, self-declared
    conformance, CLA
  - Exclusivity field (exclusive/shared, max_buyers)
  - Lead expiry (expires_at / ttl_seconds)
  - Settlement scope statement (LCP governs data, not money movement)

- JSON Schemas (Draft 2020-12) — 9 files
  - schemas/envelope.json, core.json, lead.json, call.json, ping.json,
    post.json, ack.json, event.json
  - verticals/mortgage.json (17 fields, all tagged ping_safe: true/false)
  - Ping schema is strict allowlist (additionalProperties: false)
  - Open enums: status, channel, event, call.status (no enum constraint)
  - Closed enum: message.type (triggers LCP-006)

- 6 example payloads (continuous AU mortgage lead story)
  - lead, call, ping, post, ack, event

- 27 test vectors across 3 conformance tiers
  - L1 (8): envelope, required fields, idempotency, UUID, timestamps
  - L2 (12): lifecycle, ping/post PII split, ping_safe enforcement,
    invalid phone, missing consumer, non-Western full_name
  - L3 (7): agent attestation, status transitions (25 edges), US TCPA
    consent, errors[] array, open-enum pass-through, exclusivity, expiry

- Conformance runner (test-vectors/conformance.py)
  - JSON Schema Draft 2020-12 validation via referencing library
  - Ping_safe enforcement (rejects non-safe fields in ping attributes)
  - Status transition graph validation
  - 27/27 tests passing

- Reference MCP server (implementations/mcp-server/)
  - 6 tools: submit_lead, submit_call, query_lead_status, get_schema,
    get_capabilities, list_offers
  - Stdio transport, stateless adapter
  - Local schema fallback (works without a live endpoint)
  - Bearer + HMAC authentication

- Governance documents
  - CLA (full text: copyright + patent grant, corporate contributions)
  - CONTRIBUTING.md (anti-capture clause, core vs. extension guidance)
  - SECURITY.md (responsible disclosure, PII-specific guidance)
  - TRADEMARK.md ("LCP compliant" usage rules)
  - EXTENSION-REGISTRY.md (namespace format, open registration)

- Deep-research review (docs/)
  - Cross-LLM review prompt
  - Sonnet 5 adversarial review — all 5 blockers + 15 should-fixes resolved

### Resolved (from deep-research review)
- B1: Ping PII safety → strict allowlist (additionalProperties: false)
- B2: Compliance block → generic consent_evidence[] array
- B3: Phone hash → per-pair HMAC-SHA256
- B4: Status transition graph published
- B5: Open/closed enum policy stated
- 15 should-fixes resolved (full_name, gender→attributes, errors[],
  consent_purposes[], expires_at, exclusivity, agent attestation as JWT,
  30s timeout, reserved-namespace rule, countries replaces markets, etc.)
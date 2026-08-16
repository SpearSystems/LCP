# Changelog

All notable changes to LCP (Lead Context Protocol) are documented in this
file. The format is based on [Keep a Changelog](https://keepachangelog.com/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Non-blocking commit-attribution reporting, a one-command hook bootstrap,
  and a tracked `commit-msg` hook with configurable generated-identity policy
  that preserves human co-authors.
- Versioned requirement-profile and named service-area extensions in the
  reference platform, with adoption-readiness, LEP, and v1.1 roadmap guidance.
- Human-readable v1.0 field dictionary and advisory consent/evidence guide.

### Changed
- Converted the v1.0 release ticket and publishing-gap analysis into accurate
  post-release records, with the remaining governance and v1.1 work explicitly
  tracked.

## [1.0.0] — 2026-08-16

### Added
- Published the signed v1.0.0 release with verified package, container, SBOM,
  provenance, and release-manifest evidence.
- Complete Previous/Next navigation and `Page N of 6` numbering across all documentation pages, forming one continuous reading path through the docs site
- First adopter in [ADOPTERS.md](ADOPTERS.md): SpearPointX — performance-weighted lead exchange
- Release-ticket approval checklist (`docs/RELEASE-TICKET.md`) that turns dry-run artifact verification into a gate before the real tag
- Inline release-evidence review job that posts digest and conformance summaries on release pull requests
- Scheduled weekly probe (`release-verify.yml`) that verifies the published release evidence against the signed manifest and registry presence
- Dependency update decision record (`docs/DEPENDENCY-DECISIONS.md`) documenting applied and deferred Dependabot updates

### Changed
- Updated the MCP adapter for the MCP 2.x callback-based `Server` API while retaining compatibility with the 1.x decorator API; the SDK compatibility import gate now passes with `mcp 2.0.0`.
- Added the Kotlin Central Portal submission step required after Gradle's OSSRH compatibility upload, so the Kotlin deployment is automatically validated and published instead of remaining outside the Portal.
- Completed Kotlin Maven publication metadata and detached signing (sources, Javadocs, SCM, developers, and PGP signatures), fixed the nested Ruby SDK release working directory, and stopped retrying non-transient Central validation failures.
- Added immutable-version probes for retrying partially completed registry releases without attempting to republish Java, Kotlin, npm, crates.io, or RubyGems artifacts that already exist; crates.io publication now checks the sparse index and fails closed when registry state cannot be determined.
- Added the npm repository metadata required for provenance validation and the Ruby `rake` development dependency required by the trusted release task.
- Added complete Rust crate metadata (homepage, repository, documentation, and README) for a discoverable v1 package.
- Completed registry-side Trusted Publishing setup for crates.io and RubyGems; the crates.io bootstrap token was revoked and the RubyGems `lcp-sdk` publisher was activated by the successful v1.0.0 OIDC release.
- Fixed the npm release job to build the TypeScript SDK before publishing, so the bootstrap package will be replaced by the complete v1.0.0 distribution
- Switched Java and Kotlin Maven coordinates to the DNS-controlled `systems.spear` namespace, verified through `spear.systems`
- Recorded the NuGet `SpearSystems` organization as the `LcpSdk` package and Trusted Publishing owner while retaining `rbeno` as the OIDC token-requesting profile
- Moved the TypeScript SDK to the controlled `@spear-systems/lcp-sdk` npm scope after auditing the pre-existing `@spearsystems` namespace
- Updated the npm release job to Node.js 24 with npm caching disabled, meeting npm Trusted Publishing's current OIDC runtime requirements
- Made the Python release matrix use distinct GitHub environments for each pending PyPI publisher, allowing the three new monorepo projects to use OIDC Trusted Publishing without reusing an ambiguous pending-publisher identity
- Added an SDK packaging gate to the compatibility workflow that builds each SDK's distributable artifact on every push and PR (`python -m build`, `npm pack`, `dotnet pack`, `mvn package`, `cargo package`, `gem build`, `gradle jar`, `composer archive`), catching packaging regressions such as the sdist-to-wheel force-include breakage before release
- Fixed the release-evidence workflow: sign and verify now agree on Sigstore bundle names (`release-notes.md.sigstore.json` / `release-manifest.json.sigstore.json`), and the release manifest records the package identity (e.g. `lcp-sdk-python`) instead of the registry coordinate so the offline verifier accepts all 12 evidence records
- Added the LCP banner to the repository and made it the README masthead (`assets/lcp-banner.png`)
- Coordinated `SDK_VERSION` and all 12 package manifests to 1.0.0 and removed the remaining v0.1/draft status markers (README, SPEC, implementation decisions, changelog) ahead of the v1.0.0 release
- Applied Dependabot updates across GitHub Actions (checkout v7.0.1, setup-node v7.0.0, setup-python v7.0.0, setup-java v5.7.0, cosign-installer v4.1.2, dependency-review v5.0.0, crates-io-auth v1.0.5, ruby/setup-ruby v1.321.0, github-script v7) and SDKs (Rust hmac/getrandom/jsonschema/sha2, Go module bumps, TypeScript devDependencies, Kotlin plugin 2.4.10, JUnit 6.1.3)
- Resolved all known dependency vulnerabilities: pinned Jackson core/databind to 2.18.9 in the Java and Kotlin SDKs (nine CVEs on the transitive 2.18.3 pulled by networknt 1.5.9), bumped ajv to ^8.18.0 in the TypeScript SDK (GHSA-2g4f-4pwh-qvx6), and bumped golang.org/x/text to 0.39.0 in the Go SDK (GO-2026-5970); verified with `mvn test`, the Gradle test suite, `npm test`, `go test`, and osv-scanner reporting zero findings
- Replaced the flaky `search.maven.org` SOLR registry probe with direct `repo1.maven.org` artifact checks per coordinate, so the release dry-run's availability gate fails fast and reliably instead of timing out
- Added a pinned, SHA-verified osv-scanner job to the security workflow that scans every SDK lockfile on push, pull request, and the weekly schedule, failing on high/critical (or unrated) vulnerabilities while uploading the full SARIF to code scanning
- Fixed the Kyverno admission fixture for cosign 3.x: signing-config is enabled by default and rejects `--tlog-upload=false`, so the fixture now passes `--use-signing-config=false`; CI failures now surface the exact failing command in check-run annotations and upload a diagnostics artifact
- Applied Dependabot updates: jackson-databind 3.1.5 → 3.2.1 in the Java and Kotlin SDKs (second patched version for CVE-2026-59889; PRs #33/#32), MCP adapter constraint widened to `>=1.28.1,<2.1.0` (verified against mcp 2.0.0; PR #34), github-script 7.1.0 → 9.0.0 and download-artifact 4.2.1 → 8.0.1 (PRs #35/#36; the only v5 breaking change concerns downloads by artifact `id`, which this repo does not use)
- Completed the networknt json-schema-validator 3.x Jackson 3 rewrite for the Java and Kotlin SDKs (Dependabot PRs #16 and #25): validators migrated from `JsonSchemaFactory`/`SpecVersion` to `SchemaRegistry`/`Schema`/`SpecificationVersion`, and the SDKs' own JSON parsing moved from `com.fasterxml.jackson` to `tools.jackson` 3.1.5 (fixes CVE-2026-54512, CVE-2026-54513, and CVE-2026-59889); the Jackson 2 pins that mitigated the 1.5.9 transitive CVEs were removed. Verified with `mvn test` and the Gradle test suite
- Hardened reference-platform API-key storage: unsalted SHA-256 digests replaced with per-credential salted PBKDF2-HMAC-SHA256 (resolves the CodeQL weak-sensitive-data-hashing alerts) and constant-time verification; previously issued API keys must be re-provisioned via `upsert_credential`
- Fixed a path traversal in the Python SDK ping-safe check: the attacker-controlled vertical name is resolved against the preloaded schema registry instead of being interpolated into a filesystem path (resolves the CodeQL path-expression alerts)
- Visible CI status and a gated tagged-release workflow with a non-publishing dry run, registry collision checks, per-package signed SBOM/provenance evidence, Sigstore-signed notes, release manifest, and source SBOM
- Offline release-evidence verifier (`tools/verify_release_evidence.py`) that validates downloaded manifests, source archives, SBOMs, provenance statements, digests, and optional Cosign bundles before approval
- Hermetic Kyverno admission assurance using a pinned local OCI registry, generated fixture images, ephemeral signing keys, and local provenance/SBOM attestation rejection cases
- Protected release-record publication and maintainer documentation for GitHub branch rules, OIDC trusted publishers, release environments, and regulated-production approvals
- Declarative publisher mapping registry with versioned brand/form normalization, allowlisted transforms, OTP-aware quality signals, and PII-free source digests
- Distinct Kotlin Maven publication coordinate (`systems.spear:lcp-sdk-kotlin`) so Java and Kotlin SDKs can be published from the same release without artifact collision
- Controlled home-services service/subcategory taxonomy and a dedicated motor-vehicle-accident (`mva`) vertical
- Authenticated attachment upload/download with local AES-GCM storage and a production S3-compatible SSE-KMS adapter, fail-closed ClamAV scanning, immutable residency policy, and opaque storage references
- Call post delivery with call blocks, signed call outcome events, structured duration/disposition payable rules, and per-offer monthly quota/pacing reports
- Full Draft 2020-12 runtime validation APIs and generated typed models across all ten SDKs, with deterministic schema bundles and SHA-256 synchronization manifests
- Coordinated SDK version gate and protected trusted-registry publication workflow for PyPI, npm, NuGet, Maven Central, crates.io, RubyGems, Packagist, Go modules, and Swift Package Manager
- Repository-native paginated documentation navigation for publishers, buyers, developers, platform operators, security reviewers, and contributors
- Official multi-language SDK program with shared HMAC vectors, compatibility policy, Tier 1 Python/TypeScript/Go/.NET SDKs, and Tier 2/3 Java/PHP/Rust/Ruby/Kotlin/Swift reference SDKs
- Python raw-body webhook verification and MCP adapter reuse of the shared Python SDK transport/signing implementation
- SDK compatibility workflow covering every language package and the shared signing fixture
- Security/deployment hardening profile with Postgres support, fail-closed production configuration, AES-GCM envelope encryption, request limits, strict test-traffic separation, health endpoints, audit records, durable routing leases, SSRF/egress controls, and controlled lead erasure
- Cloud-neutral Kubernetes examples with separate API/worker deployments, network policies, disruption budgets, autoscaling guidance, and mounted secret/encryption-key configuration
- Production-oriented reference platform/router with SQLite/Postgres persistence, offer matching, ping/bid/post routing, signed webhook retries, admin CLI, and Docker sandbox
- Standalone Python SDK for envelope construction, JSON Schema validation, canonical HMAC signing, idempotency, and HTTP operations
- Buyer offer schema (`schemas/offer.json`) and implementation decision profile (`docs/IMPLEMENTATION-DECISIONS.md`)
- End-to-end synthetic publisher and buyer sandbox fixtures
- OpenAPI and MCP documentation for the canonical HMAC signing profile
- OpenAPI 3.1 specification for the HTTP transport binding (`api/lcp-openapi.yaml`)
- Code of Conduct (Contributor Covenant v2.1)
- Maintainers file
- Adopters file
- GitHub issue and pull request templates
- Trust model section in SECURITY.md
- AI contribution disclosure policy in CONTRIBUTING.md
- Bid message type (`schemas/bid.json`) — completes the ping/bid/post auction flow
- 4 new vertical schemas: insurance, solar, legal, home_services
- Platform integration guide (`docs/PLATFORM-INTEGRATION.md`)
- Call tracking fields: `forwarded_to`, `tracking_number` on call block
- IVR detail: `language`, `abandoned`, `menu_options_selected`
- OTP detail block: `channel`, `verified_at`, `verified_value_hash`, `attempts`
- Contact window: consumer availability hint (timezone, hours, days)
- Platform source: ad platform attribution in provenance
- Lead quality + verification: `verified_phone/email`, `verification{method, by, at}`
- Compliance scrubs: DNC, litigator, blacklist (`scrubs[]` array)
- Lead source/quality: `source_type` (open), `acquisition_method`, `is_incentivized`
- Ping quality flags: verified, incentivized, duplicate_risk, spam_score, scrub statuses
- Offer restrictions: excluded sources, reject incentivized, claim language, scrub rejections
- Delivery windows + capacity in capabilities
- Payable definition + payable status in post pricing
- Dispute window hours + expiry in post pricing
- `publisher_id` + `offer_id` + `lead_age_minutes` in ping
- `offer_id` in post
- `rejection_reason` (structured) in ack
- `consent_expires_at` in compliance
- `secondary_phone` + `preferred_contact_method` in consumer
- `email_hash` for email-based dedup
- `dedup_window_hours` hint in ping
- `exclusivity` hint in ping
- `submitted_at` on lead, call, and post
- `CONSENT_WITHDRAWN` + `ERASURE_REQUEST` event patterns
- Webhook signing documentation (same HMAC scheme)
- `transferred_from` + `queue` blocks on call
- 11 new mortgage fields (rate term, cash-out, bankruptcy, foreclosure, etc.)
- `mortgage_product` object in mortgage vertical — country-scoped product enums (US: conventional, FHA, VA, HELOC, jumbo, USDA; CA: insured, insurable, uninsured; GB: standard_variable, discount, tracker, offset; AU: principal_and_interest, interest_only, offset)
- Country-scoping rule for vertical schemas (AGENTS.md rule 7, SPEC.md §3)
- Spear Systems watermark added to all schemas, SPEC header, README, and MCP server

### Specification and initial build-out (2026-08-15)

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
# LCP — Design Notes & Research

## Current documentation

- [PUBLISHER-ONBOARDING.md](PUBLISHER-ONBOARDING.md) — publisher setup,
  source mapping, authentication, retries, sandbox testing, and go-live checks.
- [BUYER-ONBOARDING.md](BUYER-ONBOARDING.md) — buyer offers, auction bids,
  webhook verification, idempotency, and production controls.
- [PLATFORM-INTEGRATION.md](PLATFORM-INTEGRATION.md) — how common platforms
  (Facebook Lead Ads, Google Lead Forms, Twilio, HubSpot, Salesforce, TikTok)
  map to/from LCP fields.
- [IMPLEMENTATION-DECISIONS.md](IMPLEMENTATION-DECISIONS.md) — approved
  production reference profile: offer matching, auction selection, HMAC,
  webhook retries, SDK boundaries, and sandbox parity.
- [Reference platform](../implementations/reference-platform/README.md) —
  install, configure, operate, and deploy the persistent LCP router.
- [Sandbox](../examples/sandbox/README.md) — end-to-end synthetic
  publisher/buyer test using the same platform code path.
- [Security architecture](SECURITY-ARCHITECTURE.md) — trust boundaries,
  security profiles, and defense-in-depth controls.
- [Threat model](THREAT-MODEL.md) — assets, actors, abuse cases, and
  residual risks.
- [Privacy and data governance](PRIVACY-DATA-GOVERNANCE.md) — residency,
  retention, erasure, and PII operations.
- [Production deployment](DEPLOYMENT.md) — Postgres, Kubernetes, scaling,
  and recovery targets.
- [Security profiles](SECURITY-PROFILES.md) — Baseline, Enterprise, and
  Regulated deployment expectations.
- [Operations runbook](OPERATIONS.md) — health, metrics, deployment, privacy
  operations, and incident practices.
- [Supply-chain security](SUPPLY-CHAIN-SECURITY.md) — dependency audits, SBOMs,
  image scanning, and release controls.
- [Container signing and provenance](CONTAINER-SUPPLY-CHAIN.md) — Cosign,
  GitHub attestations, digest deployment, and Kyverno admission enforcement.
- [Vulnerability exception register](VULNERABILITY-EXCEPTIONS.md) — current
  full-image scan review, owners, dispositions, and expiry-based follow-up.
  The CI machine-readable register is [`vulnerability-exceptions.json`](vulnerability-exceptions.json).
- [Integration examples](../examples/integrations/) — Python, Node.js, cURL,
  and buyer webhook templates.

## Historical artifacts (kept for provenance)

These documents were part of the spec development process. Their
findings have been resolved — they do not reflect the current state of
the spec.

- [lcp-deep-research-prompt.md](lcp-deep-research-prompt.md) — the
  cross-LLM review prompt used to stress-test the v1.0 design.
- [lcp-deep-research-review.md](lcp-deep-research-review.md) — Sonnet 5
  adversarial review. All 5 blockers and 15 should-fixes resolved in
  SPEC.md (see §14 review-log appendix).
- [publishing-gap-analysis.md](publishing-gap-analysis.md) — pre-publish
  comparison against LEX and MCP repos. All high/medium gaps addressed.
  Lower-priority items tracked in SPEC.md §14 deferred tables.
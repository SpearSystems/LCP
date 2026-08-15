# LCP documentation

> **Documentation home · Page 1 of 6**
>
> This repository is designed to be browsed like a small documentation site.
> Use the role cards below, then follow the **previous / next** links at the
> bottom of each page. Every page is plain Markdown and works offline in a
> checkout, GitHub, and common IDEs.

<details open>
<summary><strong>Choose your path</strong></summary>

| I want to… | Start here | Then read |
|---|---|---|
| Send leads as a publisher | [Publisher onboarding](PUBLISHER-ONBOARDING.md) | [Integration examples](../examples/integrations/) → [SDK index](../implementations/sdk/) |
| Buy or receive leads | [Buyer onboarding](BUYER-ONBOARDING.md) | [Offer criteria](BUYER-ONBOARDING.md#offer-criteria) → [Platform integration](PLATFORM-INTEGRATION.md) |
| Run an LCP platform | [Platform integration](PLATFORM-INTEGRATION.md) | [Reference platform](../implementations/reference-platform/README.md) → [Deployment](DEPLOYMENT.md) |
| Build an application | [SDK index](../implementations/sdk/) | [SDK support policy](SDK-ROADMAP.md) → your language README |
| Publish or verify a release | [Release guide](RELEASE.md) | [Supply-chain security](SUPPLY-CHAIN-SECURITY.md) → [Container verification](CONTAINER-SUPPLY-CHAIN.md) |
| Configure protected releases | [Maintainer release setup](MAINTAINER-RELEASE-SETUP.md) | [Release guide](RELEASE.md) → [Supply-chain security](SUPPLY-CHAIN-SECURITY.md) |
| Deploy securely | [Deployment](DEPLOYMENT.md) | [Security profiles](SECURITY-PROFILES.md) → [Operations](OPERATIONS.md) |
| Understand the protocol | [Canonical specification](../SPEC.md) | [Schemas](../schemas/) → [Conformance vectors](../test-vectors/) |
| Connect an AI agent | [MCP adapter](../implementations/mcp-server/README.md) | [SDK contract](SDK-ROADMAP.md#relationship-to-mcp) |

</details>

## Page map

| Page | Audience | Contents |
|---:|---|---|
| 1 | Everyone | This index and the role-based paths |
| 2 | Publishers and buyers | Onboarding, examples, and offer criteria |
| 3 | Developers | SDKs, validation, generated models, and publishing |
| 4 | Platform operators | Reference platform, deployment, and Kubernetes |
| 5 | Security and operations | Threat model, privacy, supply chain, and runbooks |
| 6 | Contributors | Governance, extensions, and historical research |

<details>
<summary><strong>Current documentation</strong></summary>

### Page 2 · Integrate

- [Publisher onboarding](PUBLISHER-ONBOARDING.md) — publisher setup,
  source mapping, authentication, retries, sandbox testing, and go-live checks.
- [Publisher mapping](PUBLISHER-MAPPING.md) — versioned multi-brand form
  normalization, OTP variation, taxonomy mapping, and audit records.
- [Calls and telephony](CALLS-AND-TELEPHONY.md) — post-call and real-time
  transfer boundaries, call posts, outcome events, and payable duration rules.
- [MVA and attachments](MVA-ATTACHMENTS.md) — MVA schema, encrypted file
  uploads, authenticated downloads, and retention/erasure controls.
- [Monthly quotas](MONTHLY-QUOTAS.md) — payable pacing and per-offer target
  reporting for buyer minimums.
- [Buyer onboarding](BUYER-ONBOARDING.md) — buyer offers, auction bids,
  webhook verification, idempotency, and production controls.
- [Platform integration](PLATFORM-INTEGRATION.md) — mappings for Facebook Lead
  Ads, Google Lead Forms, Twilio, HubSpot, Salesforce, and TikTok.
- [Integration examples](../examples/integrations/) — Python, Node.js, cURL,
  and buyer webhook templates.
- [Sandbox](../examples/sandbox/README.md) — synthetic end-to-end testing on
  the same platform code path as production.

### Page 3 · Build

- [SDK index](../implementations/sdk/) — language matrix and quickstarts.
- [SDK program](SDK-ROADMAP.md) — tiers, compatibility contract, validation,
  generated models, package release gates, and registry publishing.
- [Tagged releases and artifact verification](RELEASE.md) — coordinated tag
  gates, published SDK coordinates, signed release records, SBOMs, and adopter
  verification commands.
- [Implementation decisions](IMPLEMENTATION-DECISIONS.md) — approved
  production reference profile and rationale.
- [Canonical specification](../SPEC.md) — wire contract and governance.
- [JSON Schemas](../schemas/) and [vertical schemas](../verticals/) — the
  machine-readable contract.
- [Conformance vectors](../test-vectors/) — executable protocol examples.

### Page 4 · Operate

- [Reference platform](../implementations/reference-platform/README.md) —
  persistent HTTP/router implementation, mappings, attachments, call outcomes,
  and quota reporting.
- [Production deployment](DEPLOYMENT.md) — Postgres, Kubernetes, scaling,
  residency, and recovery targets.
- [Kubernetes example](../implementations/reference-platform/kubernetes/README.md)
  — verified-image admission deployment.
- [Operations runbook](OPERATIONS.md) — health, metrics, deployment, privacy
  operations, and incident practices.

### Page 5 · Assure

- [Security architecture](SECURITY-ARCHITECTURE.md) — trust boundaries and
  defense-in-depth controls.
- [Threat model](THREAT-MODEL.md) — assets, actors, abuse cases, and residual
  risks.
- [Privacy and data governance](PRIVACY-DATA-GOVERNANCE.md) — residency,
  retention, erasure, and PII operations.
- [Security profiles](SECURITY-PROFILES.md) — Baseline, Enterprise, and
  Regulated deployment expectations.
- [Supply-chain security](SUPPLY-CHAIN-SECURITY.md) — dependency audits,
  SBOMs, image scanning, and release controls.
- [Tagged releases and artifact verification](RELEASE.md) — signed release
  notes, package publication, release manifests, and verification.
- [Container signing and provenance](CONTAINER-SUPPLY-CHAIN.md) — Cosign,
  attestations, digest deployment, and Kyverno admission enforcement.
- [Vulnerability exception register](VULNERABILITY-EXCEPTIONS.md) — current
  full-image scan review and expiry-based follow-up.
- [Maintainer release setup](MAINTAINER-RELEASE-SETUP.md) — branch protection,
  protected release environment, OIDC trusted publishers, and break-glass
  controls.
- [Dependency update decisions](DEPENDENCY-DECISIONS.md) — record of applied
  and deferred Dependabot updates, including the completed networknt Jackson 3
  migration and the SDK-wide osv-scanner security gate.

### Page 6 · Contribute

- [Contributing](../governance/CONTRIBUTING.md) — contribution process and
  core-versus-extension boundaries.
- [Security policy](../governance/SECURITY.md) — responsible disclosure.
- [Extension registry](../governance/EXTENSION-REGISTRY.md) — namespaced
  extension registration.
- [Trademark and conformance claims](../governance/TRADEMARK.md) — how to
  describe implementations accurately.

</details>

<details>
<summary><strong>Reference and research material</strong></summary>

These documents were part of the spec development process. Their findings are
preserved for provenance and do not override the current specification.

- [lcp-deep-research-prompt.md](lcp-deep-research-prompt.md) — cross-LLM review prompt.
- [lcp-deep-research-review.md](lcp-deep-research-review.md) — adversarial review;
  resolved findings are recorded in SPEC.md §14.
- [publishing-gap-analysis.md](publishing-gap-analysis.md) — pre-publish
  comparison against LEX and MCP repositories.

</details>

---

**Previous:** [Repository home](../README.md) · **Next:** [Publisher onboarding](PUBLISHER-ONBOARDING.md)

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
| Buy or receive leads | [Buyer onboarding](BUYER-ONBOARDING.md) | [Offer criteria](BUYER-ONBOARDING.md#3-publish-an-offer) → [Platform integration](PLATFORM-INTEGRATION.md) |
| Run an LCP platform | [Platform integration](PLATFORM-INTEGRATION.md) | [Reference platform](../implementations/reference-platform/README.md) → [Deployment](DEPLOYMENT.md) |
| Build an application | [SDK index](../implementations/sdk/) | [SDK support policy](SDK-ROADMAP.md) → your language README |
| Publish or verify a release | [Release guide](RELEASE.md) | [Supply-chain security](SUPPLY-CHAIN-SECURITY.md) → [Container verification](CONTAINER-SUPPLY-CHAIN.md) |
| Configure protected releases | [Maintainer release setup](MAINTAINER-RELEASE-SETUP.md) | [Release guide](RELEASE.md) → [Supply-chain security](SUPPLY-CHAIN-SECURITY.md) |
| Deploy securely | [Deployment](DEPLOYMENT.md) | [Security profiles](SECURITY-PROFILES.md) → [Operations](OPERATIONS.md) |
| Understand the protocol | [Canonical specification](../SPEC.md) | [Schemas](../schemas/) → [Conformance vectors](../test-vectors/) |
| Connect an AI agent | [MCP adapter](../implementations/mcp-server/README.md) | [SDK contract](SDK-ROADMAP.md#mcp-relationship) |

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

- [Capability map](CAPABILITIES.md) — six diagrams for intake, auction and
  shared slots, negotiation, event subscriptions, matching/extension
  admission, and the data lifecycle; the system map is the README hero.
- [Error guide](ERROR-GUIDE.md) — every `LCP-0xx` code in plain English, with
  common causes and the exact field-level fix; `lcp-doctor --explain CODE`.
- [No-code recipes](RECIPES.md) — Lovable/Supabase, GoHighLevel, Make, Zapier,
  and Google Sheets, from field map to first successful test lead.
- [Buyer webhook kit](../examples/integrations/buyer-webhook/) — runnable
  receivers that verify both signed delivery surfaces and return the ack.
- [No-code templates](../examples/integrations/no-code/) — Deno edge function,
  Make module configuration, and Apps Script poster.
- [Publisher onboarding](PUBLISHER-ONBOARDING.md) — publisher setup,
  source mapping, authentication, retries, sandbox testing, and go-live checks.
- [Publisher mapping](PUBLISHER-MAPPING.md) — versioned multi-brand form
  normalization, OTP variation, taxonomy mapping, and audit records.
- [Requirement profiles](REQUIREMENT-PROFILES.md) — versioned buyer criteria,
  named service areas, safe operators, and dynamic deployment guidance.
- [Calls and telephony](CALLS-AND-TELEPHONY.md) — post-call and real-time
  transfer boundaries, call posts, outcome events, and payable duration rules.
- [Secure attachments](ATTACHMENTS.md) — encrypted file uploads,
  authenticated downloads, and retention/erasure controls (vertical-neutral).
- [Vertical schemas](VERTICALS.md) — the typed extension block, the six
  shipped verticals, and what a vertical may not contain.
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
- [SDK validator capabilities](SDK-VALIDATION-CAPABILITIES.md) — the shared
  language-neutral validation contract and its corpus.
- [v1.2.0 release record](RELEASE-TICKET-1.2.0.md) — prepared 2026-11-04;
  the tag is cut after merge approval.
- [Field dictionary](FIELD-DICTIONARY.md) — human-readable paths, types,
  requiredness, privacy boundaries, and schema references.
- [Tagged releases and artifact verification](RELEASE.md) — coordinated tag
  gates, published SDK coordinates, signed release records, SBOMs, and adopter
  verification commands.
- [v1.1.1 release record](RELEASE-TICKET-1.1.1.md) — published patch (§9 post response window) and registry coordinates
- [v1.1.0 release record](RELEASE-TICKET-1.1.0.md) — published minor, registry coordinates, container digest, and carried follow-ups
- [v1.0.2 release record](RELEASE-TICKET-1.0.2.md) — published patch and evidence
- [v1.0.1 release record](RELEASE-TICKET-1.0.1.md) — published patch and evidence
- [v1.0.0 release record](RELEASE-TICKET.md) — initial published release
- [Implementation decisions](IMPLEMENTATION-DECISIONS.md) — approved
  production reference profile and rationale.
- [Canonical specification](../SPEC.md) — wire contract and governance.
- [JSON Schemas](../schemas/) and [vertical schemas](../verticals/) — the
  machine-readable contract.
- [Conformance vectors](../test-vectors/) — executable protocol examples.

### Page 4 · Operate

- [Reference platform](../implementations/reference-platform/README.md) —
  persistent HTTP/router implementation, mappings, indexed offer candidates,
  attachments, call outcomes, and quota reporting.
- [Performance workflow](../.github/workflows/performance.yml) — non-publishing
  PostgreSQL 18.4 and candidate-reduction regression checks.
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
- [Consent and evidence guide](CONSENT-GUIDE.md) — jurisdiction-neutral
  evidence mapping, purpose scope, ping/post handling, and withdrawal.
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
- [ARB retro-audit ledger 2026-11](AUDIT-2026-11.md) — claim-by-claim
  re-verification of the Nov security/governance merges with commands and
  residual tracked items.
- [Maintainer release setup](MAINTAINER-RELEASE-SETUP.md) — branch protection,
  protected release environment, OIDC trusted publishers, and break-glass
  controls.
- [Dependency update decisions](DEPENDENCY-DECISIONS.md) — record of applied
  and deferred Dependabot updates, including the completed networknt Jackson 3
  migration and the SDK-wide osv-scanner security gate.
- [PII classes](PII-CLASSES.md) — the five-class taxonomy, per-field mapping,
  emission matrix, hashing rules, and the no-downgrade rule.
- [SPEC-to-test coverage matrix](SPEC-COVERAGE.md) — every normative clause
  mapped to the tests that prove it, with dated exemptions.
- [ERR-002/ERR-012 triage](err-auth-triage.md) — ARB wave 1.5 error-code
  resolution behind the normative §5 auth family.

### Page 6 · Contribute

- [Contributing](../governance/CONTRIBUTING.md) — contribution process and
  core-versus-extension boundaries.
- [Security policy](../governance/SECURITY.md) — responsible disclosure.
- [Extension registry](../governance/EXTENSION-REGISTRY.md) — namespaced
  extension registration.
- [LEP process](../governance/LEP.md) — proposal, review, compatibility, and
  deprecation workflow.
- [LEP process adoption plan](../governance/LEP-PROCESS-PLAN.md) — draft storage,
  review, registry, and pilot workflow.
- [LEP-0001 batch and event subscriptions](../governance/LEPs/LEP-0001-batch-submission-and-event-subscriptions.md)
  — accepted 2026-11-04 under the D1 trigger waiver; implementation shipped in
  the v1.2.0 release branch.
- [v1.2 decision record](DECISIONS-v1.2.md) — D1–D12 maintainer decisions that
  authorize the release, including the trigger waiver and its re-evaluation date.
- [Adoption readiness](ADOPTION-READINESS.md) — post-release signals,
  feedback funnel, and scale triggers.
- [v1.1 roadmap](V1.1-ROADMAP.md) — implemented foundations and deferred work.
- [v1.2 execution plan](V1.2-EXECUTION-PLAN.md) — the backlog-clearing plan:
  waves, decisions, gates, and tracking for every remaining item.
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
- [publishing-gap-analysis.md](publishing-gap-analysis.md) — historical pre-publish
  comparison against LEX and MCP repositories; current planning lives in the
  [v1.1 roadmap](V1.1-ROADMAP.md).

</details>

---

**Previous:** [Repository home](../README.md) · **Next:** [Publisher onboarding](PUBLISHER-ONBOARDING.md)

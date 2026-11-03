# LCP Enhancement Proposal registry

This is the canonical index of numbered LCP Enhancement Proposals. The process
is defined in [`LEP.md`](LEP.md); the adoption decisions and pilot sequence are
recorded in [`LEP-PROCESS-PLAN.md`](LEP-PROCESS-PLAN.md).

## Registry rules

- Numbers are sequential, four digits, and never reused.
- A rejected or deferred proposal remains in the registry with its decision.
- The registry status must match the proposal document's declared status.
- A material implementation may begin only after the proposal is `Accepted`.
- `Implemented` means the accepted proposal's required schemas, examples,
  vectors, SDK/API artifacts, documentation, and verification gates are linked
  from the record or its implementation notes.
- Batch and subscription tracks in LEP-0001 are independently decidable; one
  track may be accepted while the other is deferred.

| LEP | Title | Status | Author(s) | Opened | Review deadline | Decision/date | Target version | Proposal | Implementation / traceability |
|---|---|---|---|---|---|---|---|---|---|
| LEP-0001 | Bounded batch submission and event subscriptions | Deferred | LCP maintainers | 2026-08-18 | — | Deferred 2026-11-03 (maintainer; trigger-unmet) | v1.1.0 (revisit) | [proposal](LEPs/LEP-0001-batch-submission-and-event-subscriptions.md) | Not accepted; not implemented. Deferred per independent ARB dossier ([#164](https://github.com/SpearSystems/LCP/issues/164#issuecomment-6592471520)): adopter/trigger evidence unmet (F1) and post-draft collisions (F2-F5); revival requires a revised record per the proposal's Decision section. |
| LEP-0002 | Canonical timestamp generation and cross-SDK signature interop | Implemented | LCP maintainers | 2026-11-02 | — | Accepted 2026-11-03 (maintainer; window waived) | v1.1.0 | [proposal](LEPs/LEP-0002-canonical-timestamp-and-signing-interop.md) | Decision + dossier: [proposal §Decision](LEPs/LEP-0002-canonical-timestamp-and-signing-interop.md), [#121 dossier](https://github.com/SpearSystems/LCP/issues/121#issuecomment-6578306330). Implementation authorized: three-SDK whole-second emission, SPEC §9 text, interop blocking (corrections #149 + baseline-sync #152 landed). Implementation: PR [#158](https://github.com/SpearSystems/LCP/pull/158) (Java/Kotlin/Rust canonical emission + frozen-clock tests, SPEC §9 canonical-generation paragraph, interop blocking flip). |
| LEP-0003 | Shared-lead slot allocation and contacted-party dedup exchange | Implemented | LCP maintainers | 2026-11-02 | — | Accepted 2026-11-03 (maintainer; re-review + RF dispositions complete) | v1.1.0 | [proposal](LEPs/LEP-0003-shared-lead-slot-protocol.md) | Decision + re-review: [proposal §Decision](LEPs/LEP-0003-shared-lead-slot-protocol.md), [re-dossier](https://github.com/SpearSystems/LCP/issues/124#issuecomment-6591860350), RF fixes #161. Implemented 2026-11-03: wire [#166](https://github.com/SpearSystems/LCP/pull/166), platform enforcement [#168](https://github.com/SpearSystems/LCP/pull/168), SDK helpers [#171](https://github.com/SpearSystems/LCP/pull/171); T1–T10 executed and tracked at [#160](https://github.com/SpearSystems/LCP/issues/160). |
| LEP-0004 | Autonomous Review Board for independent review | Implemented | LCP maintainers | 2026-11-02 | — | Accepted 2026-11-03 (conditional — #151 tightenings in implementation PR) | none (governance process) | [proposal](LEPs/LEP-0004-autonomous-review-board.md) | Decision: [proposal §Decision](LEPs/LEP-0004-autonomous-review-board.md); dossier [#128](https://github.com/SpearSystems/LCP/issues/128#issuecomment-6578281988). `LEP.md` amendment + charter tightenings land in implementation PR [#156](https://github.com/SpearSystems/LCP/pull/156) (conditions F1/F2/F3/F5/F7 with F4 reconciliation; per-lane charter prompts published in `ARB-PROMPTS.md`); process not operative until that PR merges. |
| LEP-0005 | Request-history log lookup (paginated audit read surface) | Draft | LCP maintainers | 2026-11-03 | — | — | v1.1.0 | [proposal](LEPs/LEP-0005-request-history-log-lookup.md) | Draft record + discussion [#187](https://github.com/SpearSystems/LCP/issues/187); no implementation authorized. Adopter evidence solicited on #187; independent ARB dossier required before any decision; PII-boundary/auth/retention touch per `LEP.md` names a human security/privacy reviewer at Review. |
| LEP-0006 | Marketplace migration extensions: final-pricing feedback and `{org}.form.questions` | Draft | LCP maintainers | 2026-11-03 | — | — | v1.1.0 | [proposal](LEPs/LEP-0006-marketplace-migration-extensions.md) | Draft only; tracks A (`FINAL_PRICING` event) and B (lead/post `extensions` + registered `{org}.form.questions`) independently decidable per LEP.md; no implementation authorized; registry rows proposed-on-acceptance in `governance/EXTENSION-REGISTRY.md`. Discussion: [#185](https://github.com/SpearSystems/LCP/issues/185). |
| LEP-0007 | Outbound call-center outreach: registered `{org}.outbound.call_request` extension | Draft | LCP maintainers | 2026-11-03 | — | — | v1.1.0 | [proposal](LEPs/LEP-0007-outbound-call-center-outreach.md) | Draft only; zero-schema-change route (namespaced open-enum event value + `rejection_reason: ignored` known value); message-type alternative deferred with triggers per LEP.md two-adopters rule; no implementation authorized; registry row proposed-on-acceptance. Discussion: [#186](https://github.com/SpearSystems/LCP/issues/186). |
| LEP-0008 | Agent attestation verification — issuer-anchored JWS verification at intake, §5 mapping, staged L3 advertisement honesty | Draft | LCP maintainers | 2026-11-03 | — | — | v1.1.0 | [proposal](LEPs/LEP-0008-agent-attestation-verification.md) | Draft only; closes the #192 L3-honesty gap: issuer-published-JWKS (pinned-key fallback) verification of `provenance.agent.attestation` at intake before PII persistence, closed EdDSA/ES256 allowlist, failures mapped onto existing §5 codes (LCP-100 shape, LCP-002 identity incl. `details.reason`, LCP-500 key-unavailable) with no new code; `### Valid example` envelopes and the full failure battery executed against the platform `SchemaValidator` + a reference verifier at `974b9ca`; no implementation authorized — named human security/privacy reviewer or ARB-Security co-sign required at Decision (touches an authentication rule, LEP.md). Discussion: [#205](https://github.com/SpearSystems/LCP/issues/205). |

## Status vocabulary

`Discuss` → `Draft` → `Review` → `Accepted` / `Rejected` / `Deferred` →
`Implemented` → `Deprecated` / `Superseded`.

The registry is intentionally a decision index, not a substitute for the
full proposal, review discussion, or decision rationale.

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
| LEP-0003 | Shared-lead slot allocation and contacted-party dedup exchange | Accepted | LCP maintainers | 2026-11-02 | — | Accepted 2026-11-03 (maintainer; re-review + RF dispositions complete) | v1.1.0 | [proposal](LEPs/LEP-0003-shared-lead-slot-protocol.md) | Decision + re-review: [proposal §Decision](LEPs/LEP-0003-shared-lead-slot-protocol.md), [re-dossier](https://github.com/SpearSystems/LCP/issues/124#issuecomment-6591860350), RF fixes #161. Implementation gated on T1–T10 ([#160](https://github.com/SpearSystems/LCP/issues/160)) and the roadmap change-gates; not yet implemented. |
| LEP-0004 | Autonomous Review Board for independent review | Implemented | LCP maintainers | 2026-11-02 | — | Accepted 2026-11-03 (conditional — #151 tightenings in implementation PR) | none (governance process) | [proposal](LEPs/LEP-0004-autonomous-review-board.md) | Decision: [proposal §Decision](LEPs/LEP-0004-autonomous-review-board.md); dossier [#128](https://github.com/SpearSystems/LCP/issues/128#issuecomment-6578281988). `LEP.md` amendment + charter tightenings land in implementation PR [#156](https://github.com/SpearSystems/LCP/pull/156) (conditions F1/F2/F3/F5/F7 with F4 reconciliation; per-lane charter prompts published in `ARB-PROMPTS.md`); process not operative until that PR merges. |

## Status vocabulary

`Discuss` → `Draft` → `Review` → `Accepted` / `Rejected` / `Deferred` →
`Implemented` → `Deprecated` / `Superseded`.

The registry is intentionally a decision index, not a substitute for the
full proposal, review discussion, or decision rationale.

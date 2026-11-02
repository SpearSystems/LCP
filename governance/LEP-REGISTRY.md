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
| LEP-0001 | Bounded batch submission and event subscriptions | Draft | LCP maintainers | 2026-08-18 | — | — | v1.1.0 | [proposal](LEPs/LEP-0001-batch-submission-and-event-subscriptions.md) | Not accepted; not implemented. Public discussion and independent security/privacy review are pending. |
| LEP-0002 | Canonical timestamp generation and cross-SDK signature interop | Accepted | LCP maintainers | 2026-11-02 | — | Accepted 2026-11-03 (maintainer; window waived) | v1.1.0 | [proposal](LEPs/LEP-0002-canonical-timestamp-and-signing-interop.md) | Decision + dossier: [proposal §Decision](LEPs/LEP-0002-canonical-timestamp-and-signing-interop.md), [#121 dossier](https://github.com/SpearSystems/LCP/issues/121#issuecomment-6578306330). Implementation authorized: three-SDK whole-second emission, SPEC §9 text, interop blocking (corrections #149 + baseline-sync #152 landed). |
| LEP-0003 | Shared-lead slot allocation and contacted-party dedup exchange | Draft | LCP maintainers | 2026-11-02 | — | — | v1.1.0 | [proposal](LEPs/LEP-0003-shared-lead-slot-protocol.md) | Not accepted; not implemented. Independent ARB dossier found 10 findings incl. oversell enforcement gap ([#124 dossier](https://github.com/SpearSystems/LCP/issues/124#issuecomment-6579188344), tracked [#153](https://github.com/SpearSystems/LCP/issues/153)); maintainer directive 2026-11-03: revise the record to resolve F1–F10, then re-dossier. |
| LEP-0004 | Autonomous Review Board for independent review | Accepted | LCP maintainers | 2026-11-02 | — | Accepted 2026-11-03 (conditional — #151 tightenings in implementation PR) | none (governance process) | [proposal](LEPs/LEP-0004-autonomous-review-board.md) | Decision: [proposal §Decision](LEPs/LEP-0004-autonomous-review-board.md); dossier [#128](https://github.com/SpearSystems/LCP/issues/128#issuecomment-6578281988). `LEP.md` amendment + charter tightenings land in implementation PR [#156](https://github.com/SpearSystems/LCP/pull/156) (conditions F1/F2/F3/F5/F7 with F4 reconciliation; per-lane charter prompts published in `ARB-PROMPTS.md`); process not operative until that PR merges. |

## Status vocabulary

`Discuss` → `Draft` → `Review` → `Accepted` / `Rejected` / `Deferred` →
`Implemented` → `Deprecated` / `Superseded`.

The registry is intentionally a decision index, not a substitute for the
full proposal, review discussion, or decision rationale.

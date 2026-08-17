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

## Status vocabulary

`Discuss` → `Draft` → `Review` → `Accepted` / `Rejected` / `Deferred` →
`Implemented` → `Deprecated` / `Superseded`.

The registry is intentionally a decision index, not a substitute for the
full proposal, review discussion, or decision rationale.

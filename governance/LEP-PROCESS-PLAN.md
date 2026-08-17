# LEP process adoption plan

- **Date:** 2026-08-18
- **Status:** **Draft for review** — no process changes are implemented yet
- **Related:** `governance/LEP.md` (process definition, shipped at baseline
  `c9d8d1a`), `docs/V1.1-ROADMAP.md` Phase 1 (use the LEP process for external
  proposals), plan item 15 of the audit remediation plan

## Why this exists

`governance/LEP.md` already defines *what* an LEP is and the six stages
(discuss → draft → review → decision → implement → deprecate/supersede), but
the process has not yet been exercised through a completed
Discuss → Draft → Review → Decision cycle. LEP-0001 is now the first numbered
draft and the natural pilot for this plan. This document operationalizes the
review path; it is not a change to the published protocol.

## Goal and acceptance

**Goal:** the first external or material change goes through the published LEP
process, and the roadmap tracks LEP status.

**Acceptance (future):**

- LEP-0001 exists as a numbered draft under `governance/LEPs/` and has moved
  through at least Discuss → Draft → Review.
- `governance/LEP-REGISTRY.md` lists every LEP with number, title, status, and
  decision date.
- The roadmap carries an LEP status column/table.
- An independent reviewer (second maintainer) has signed off a decision, per
  the roadmap's Phase 1 high-priority item.

## Decisions to make during the review pass

1. **Record storage.** Draft default: numbered markdown documents under
   `governance/LEPs/LEP-NNNN-<slug>.md`, plus a registry table in
   `governance/LEP-REGISTRY.md`. Alternative: issue-tracked proposals with the
   document linked. Decide once, document in `governance/LEP.md`.
2. **Numbering.** `LEP-0001` onward, sequential, never reused; a rejected or
   deferred LEP keeps its number and its decision record.
3. **Review roles.** Author, invited reviewers (affected implementers,
   publishers, buyers, security/privacy), decision by maintainers. The
   roadmap's second-maintainer/independent-release-approval work is a
   prerequisite for independent LEP decisions.
4. **Review window.** Draft default: 14-day comment window for material
   proposals; shorter for urgent security decisions (single maintainer, with
   the reason recorded and a second review afterward — already in LEP.md).
5. **Status lifecycle.** Map the registry statuses to LEP.md's stages
   (Discuss → Draft → Review → Accepted/Rejected/Deferred → Implemented →
   Deprecated/Superseded). One status per LEP, tracked in the registry.
6. **Traceability to code.** Accepted LEPs are referenced from the schemas /
   vectors / SDK changes they produce (changelog entry + decision record), so
   a conformance vector or SDK model can be traced back to its proposal.
7. **Roadmap coupling.** Add the LEP status table to `docs/V1.1-ROADMAP.md`
   once the first LEP is opened, per the roadmap's "no roadmap item silently
   changes the published contract" rule.

## Review checklist for `governance/LEP.md` itself

- Template/required-content checklist covers: problem + evidence, scope,
  wire shape with valid/invalid examples, privacy/security/universal-core
  audit, compatibility/versioning, SDK/OpenAPI/MCP/conformance/ops impact,
  alternatives, rollout/migration/observability/rollback, deferred questions
  with revisit triggers. (LEP-0001 was drafted against this list — it fits.)
- Cross-checked against the universal-core rules in `AGENTS.md` (simplicity
  budget, core-vs-extension, N+2 deprecation, PII discipline) and
  `SPEC.md` §12.
- Confirm the decision rules stay anti-capture: no ownership, fees, approval
  rights, or veto over the open extension registry.

## Pilot: LEP-0001

The first live use of the process is LEP-0001 (batch submission + event
subscriptions, drafted 2026-08-18). The pilot should:

1. Move LEP-0001 from Draft to Review with the affected implementers
   (reference platform, all SDKs, MCP) and a security/privacy reviewer.
2. Surface any friction in the process itself (missing template fields,
   unclear review window) and fold fixes back into `governance/LEP.md`.
3. Produce the decision record (accept/reject/defer with rationale) and the
   first registry row.

## Sequencing

1. Post-v1.0.2: review this plan + `governance/LEP.md` with the second
   maintainer; confirm storage, numbering, and review-window decisions.
2. Create `governance/LEP-REGISTRY.md` and the roadmap LEP status table.
3. Open LEP-0001 for review (already drafted) and run it through the stages.
4. Thereafter: external proposals enter via the `[LEP]` issue template, with
   the registry updated at each status change.

## Deferred questions

- Whether hosted conformance/certification (Phase 1 medium priority) needs its
  own LEP-adjacent process — revisit when the hosted service is scoped.
- Whether a standards-community SIG or external-maintainer role is needed if
  adoption grows — revisit on adopter signals, per the roadmap's monthly
  review cadence.

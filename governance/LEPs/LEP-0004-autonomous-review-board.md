# LEP-0004 — Autonomous Review Board for independent review

- **Title:** Autonomous Review Board for independent review
- **Author(s):** LCP maintainers (draft for adopter review)
- **Date:** 2026-11-02
- **Status:** **Accepted** — accepted 2026-11-03 (conditional; implementation gated on the #151 charter tightenings), not yet implemented
- **Target version:** none — governance process change; no protocol version, no wire impact

## Decision (maintainer, 2026-11-03)

**Accepted, conditional.** Recorded by the maintainer (randybeno) following the
ARB sign-off model. Decision basis: the L-DocsDrift independent-review dossier
(<https://github.com/SpearSystems/LCP/issues/128#issuecomment-6578281988>) and
its tracked findings F1–F7.

Conditions (all must land in the implementation PR before this process is
operative; until then `ARB.md` and `LEP.md` remain un-amended and the ARB has
no force):

- **F1 (capture):** attribute authorship to the *controller*, not the lane;
  a fresh-context agent launched by a proposal's author to audit its own
  proposal is labeled `SELF-AUDIT` and cannot satisfy independent review.
  Add a mandatory launch-controller field to the G2 attestation.
- **F5 (self-contradiction):** the Decision-rules bullet must read
  "satisfied by" (not "only by") an ARB dossier, preserving a future human
  second maintainer's ability to satisfy the requirement directly.
- **F4 (stale text):** reconcile the surviving stage-4 "independent
  second-maintainer review … named domain review" sentence (LEP.md:60-61)
  that the current diff leaves outside its amended sections.
- **F2 (deterministic PII gate):** the charter names no lane that produces
  "the ARB-Security dossier"; add the lane row or rename the gate to a
  lane that exists.
- **F3/F7 (dissent re-runnability):** the charter's claim that "charter
  prompts live in this repository" is unbacked at this SHA; publish the
  prompts and require dossiers to record plant bytes so an adopter can
  reproduce a finding without transcript access.
- **Implementation:** PR [#156](https://github.com/SpearSystems/LCP/pull/156).
  Conditions → hunks: F1 → the charter's independence rule 2 and gate G2 in
  `governance/ARB.md` (authorship attaches to the run's controller;
  SELF-AUDIT labeling; launch-control attestation) plus the Launch control
  attestation line in `governance/ARB-DOSSIER-TEMPLATE.md`; F2 → the
  `L-Security` row in the lanes table of `governance/ARB.md` (produces the
  ARB-Security dossier named by the PII/auth/retention gate and by the
  amended Roles bullet) and the template's lane enumeration; F3 → the
  charter's reproducibility rule 3 and the chicken-and-egg item in
  `governance/ARB.md`, with the per-lane charter prompts published in the
  new `governance/ARB-PROMPTS.md`; F5 → the amended Decision-rules bullet in
  `governance/LEP.md`, which reads "satisfied either by a review from a
  maintainer who did not author the proposal or by an ARB Review Dossier
  that passes charter gates G1–G6" (the "only by" wording rejected per the
  Alternatives section); F7 → the charter's reproducibility rule 1 and gate
  G1 in `governance/ARB.md` plus the Plant bytes appendix in
  `governance/ARB-DOSSIER-TEMPLATE.md`. F4 → the stage-4 Decision sentence
  in `governance/LEP.md` reconciled in the same diff. Verification:
  charter/template grep sweeps, `tools/check_lep_registry.py`, and
  `tools/check_doc_citations.py` green at the implementation SHA (outputs in
  the PR body); findings tracked at
  [#151](https://github.com/SpearSystems/LCP/issues/151); dossier
  [#128](https://github.com/SpearSystems/LCP/issues/128#issuecomment-6578281988).

Independent-review note: this is the single-use chicken-and-egg exception
declared in this proposal (§ below). The independent control is external
re-runnability of the published charter, template, and evidence commands at
the recorded commit, not an ARB review of the ARB.

Public discussion: <https://github.com/SpearSystems/LCP/issues/128>.
Charter record: [`../ARB.md`](../ARB.md); dossier template:
[`../ARB-DOSSIER-TEMPLATE.md`](../ARB-DOSSIER-TEMPLATE.md). Neither has any
effect until this proposal is Accepted; this record changes nothing in
`LEP.md` itself (the amendment lands post-acceptance, see Proposed LEP.md
amendment below).

## Problem statement and evidence

`governance/LEP.md` (Decision stage) requires that "a material proposal
requires an independent second-maintainer review; security or privacy
changes also require a named domain review," and `AGENTS.md` forbids anyone
to "bypass independent review on behalf of maintainers." LCP has exactly one
active maintainer. Every merge on `origin/main` to date — all 68 of them —
was performed by that single person (verified at draft time with
`git log --merges --format='%an' origin/main | sort | uniq -c`: two commit
identities, one human, one machine). The independent-review clause is
therefore already, in practice, a second hat on the same head, and its cost
lands entirely on the owner's time:

1. `governance/LEP-REGISTRY.md` shows all three numbered proposals
   (LEP-0001, LEP-0002, LEP-0003) blocked on "independent security/privacy
   review are pending" — the words appear on all three registry rows.
2. The v1.1 program (docs/V1.1-ROADMAP.md) has produced a large volume of
   security- and privacy-relevant changes (signing canonicalization,
   slot-allocation PII flows, logging-hygiene gates, supply-chain pinning)
   whose review demand now exceeds the one human's throughput. The review
   labor, not the review authority, is the bottleneck.
3. Owner-performed "independent" review of owner-authored changes is not
   independence by any reading of the clause; future external audits of the
   project (including the adversarial audits this proposal institutionalizes)
   will find the process itself non-compliant.

The fix is to **redefine how the clause is satisfied, not to bypass it**: an
Autonomous Review Board (ARB) — a codified, adversarial, publicly
re-runnable agent audit whose Review Dossier, when it meets the charter's
quality gates, satisfies the independent-reviewer requirement, while the
maintainer keeps the decision and the accountability. What is removed is the
manual reading labor; what is added is machine-produced falsifiable
evidence. The full normative mechanics (lanes, defect budget, gates,
dissent, role separation) are the charter; this record proposes the
governance change only.

## Scope

- Governance process only: `governance/LEP.md` role/decision clauses, the
  new charter `governance/ARB.md`, and `governance/ARB-DOSSIER-TEMPLATE.md`.
- Not core protocol, not vertical schemas, not registered extensions, not
  wire behavior, not SDK/OpenAPI/MCP artifacts, not conformance vectors.

## Proposed wire shape

None. This proposal adds no message types, fields, errors, or version
behavior; valid/invalid wire examples are not applicable. Zero impact on v1
senders or receivers. The only artifacts are process documents; the "output"
of the process is a Review Dossier comment on a public issue/PR, whose
format is proposed (not wire-) normative in
[`../ARB-DOSSIER-TEMPLATE.md`](../ARB-DOSSIER-TEMPLATE.md).

## Proposed LEP.md amendment

Text to be applied to `governance/LEP.md` **only on acceptance of this
proposal** — this record is the amendment, merged into LEP.md in a separate
implementation PR exactly as the LEP process demands (Draft status
authorizes nothing). Diff against `governance/LEP.md` at the SHA this
proposal's dossier cites:

```diff
 ## Roles and independence
 
 Each material LEP identifies:
 
 - **Author:** owns the proposal and responses to review comments.
 - **Shepherd:** maintains the issue, registry status, deadlines, and links.
 - **Domain reviewers:** represent affected publishers, buyers, implementers,
   and SDK maintainers.
-- **Security/privacy reviewer:** reviews PII, authorization, delivery,
-  retention, replay, and abuse implications.
-- **Independent maintainer:** reviews the decision separately from the author
-  where possible and is required for material acceptance/rejection/deferral.
+- **Security/privacy reviewer:** reviews PII, authorization, delivery,
+  retention, replay, and abuse implications. For a proposal touching a PII
+  boundary, an authentication rule, or a retention rule, this role MUST be
+  filled by a named human domain reviewer, or by the decision maintainer's
+  explicit co-sign of the ARB-Security review dossier. Agent reviews
+  certify consistency with the specification, not legality or privacy
+  fitness.
+- **Independent maintainer:** reviews the decision separately from the author
+  and is required for material acceptance/rejection/deferral. An ARB Review
+  Dossier meeting every quality gate of [`ARB.md`](ARB.md) satisfies this
+  requirement; the dossier MUST come from a lane that did not author the
+  change under review, MUST enumerate its refutation attempts at a full
+  commit SHA, and MUST be posted publicly on the proposal or pull request.
 - **Decision maintainers:** record the final decision and rationale publicly.
+
+ARB lanes, judges, and automation never record decision status. Only a
+maintainer writes `Accepted`, `Rejected`, or `Deferred`, and the decision
+rationale cites the dossier and the public discussion it was published on.
 
 ## Decision rules
 
 - Prefer a namespaced extension or reference-platform behavior over a new core
   field when the requirement is buyer-, publisher-, market-, or
   deployment-specific.
 ...
 - Separate capabilities with materially different semantics may share an
   umbrella discussion, but each must have independently reviewable and
   independently decidable acceptance criteria.
+- The independent-review requirement for a material decision is satisfied
+  only by an ARB Review Dossier that passes charter gates G1–G6. A valid
+  dissenting dossier re-run at the same commit blocks recording of the
+  decision until the charter's dissent protocol has adjudicated it.
+- Decisions are adopted per decision: the maintainer records
+  `Accepted`/`Rejected`/`Deferred` citing the dossier link. Silence is never
+  consent — no dossier expiry, review-window lapse, or automation may
+  produce a decision status.
```

## Privacy, security, and universal-core audit

- No PII boundary, authentication rule, retention rule, or lifecycle state
  changes. This proposal cannot leak data because it moves no data: dossiers
  cite commands, outputs, and file:lines of the public repository.
- Universal-core audit (SPEC.md §12): no fields or message types are added;
  the audit is vacuous by scope.
- The security surface of this proposal is the process itself, and the
  charter is written against the two ways a review process dies:
  rubber-stamping (the adversarial defect budget and judge gate — a PASS
  without ≥5 enumerated refutation attempts is void) and capture
  (everything — charter, prompts, template, dossiers — is public, and any
  adopter may re-run the harness at the same commit; a valid dissent blocks
  the decision until adjudicated).
- Deliberate human anchor: proposals touching PII/auth/retention keep a
  named human reviewer or maintainer co-sign requirement (amendment above).
  For this proposal's own acceptance, the chicken-and-egg clause in
  `ARB.md` applies and is disclosed plainly: LEP-0004 cannot be reviewed by
  the machinery it creates; the sole maintainer accepts it, if at all, as a
  recorded process exception whose independent control is external
  re-runnability plus the public 14-day window. No future proposal may
  invoke this clause.

## Compatibility, versioning, and deprecation impact

- No protocol version bump: nothing in `SPEC.md`, schemas, vectors, or SDKs
  changes (governance-process change; AGENTS.md rule 2 untouched).
- `LEP.md` stage vocabulary, the registry checker, and the 14-day public
  review window are unchanged; the amendment redefines who/what may perform
  the independent review, not when decisions may be made.
- Fully backward compatible for adopters: a decision made with a conforming
  ARB dossier is recorded identically to one made with a human reviewer.
- No deprecations. Rollback (below) restores the prior text without orphans.

## SDK, OpenAPI, MCP, conformance, and operational impact

- None to SDK, OpenAPI, MCP artifacts or generated models: the LEP registry
  checker (`tools/check_lep_registry.py`) validates this proposal unchanged.
- Conformance runner and test vectors: untouched by acceptance itself. The
  charter's reproducibility gate (durable findings become CI tests before
  lane close) means ARB *reviews* will add tests over time — additive,
  per-decision, each landing through the normal PR pipeline.
- Operational impact: each material LEP decision requires one lane fan-out
  + judge run (cost guard: max two runs per lane per cycle) instead of
  maintainer reading time; dossiers are posted as issue comments (no new
  infrastructure). Review of LEP-0002 and LEP-0003 becomes the first real
  ARB execution.

## Alternatives considered

1. **Recruit a second human maintainer.** Cleanest legitimacy. No candidate
   exists today, and nothing prevents the same requirement being met this
   way later — the amendment is "satisfied by", not "only by". Rejected for
   now for lack of candidates; remains always available.
2. **Keep owner-only review.** The status quo; does not satisfy the clause's
   intent (independence), does not satisfy it at scale, and blocks three
   proposals indefinitely.
3. **Silence-is-consent.** ARB-PASS + 14-day window expiry auto-Accepts
   unless the owner objects. Fastest, but it moves a PII protocol's legal
   and privacy decisions into automation-by-default and lets unreviewed
   decisions accrue on maintainer inattention. Explicitly rejected as the
   adoption model (kept as a deferred question with a trigger below).
4. **Human-ARB hybrid (ARB pre-reads, human reviews everything).** Removes
   none of the bottleneck; ARB evidence then exists only as an advisory
   digest. Rejected as paying the cost without the accountability win.
5. **Proposed:** ARB dossier satisfies the independent-review requirement
   under charter gates, with per-decision maintainer sign-off and the
   PII/auth/retention human anchor.

## Rollout, migration, observability, and rollback

1. **Acceptance** → apply the amendment above to `governance/LEP.md` in a
   dedicated implementation PR linking this record and the registry row;
   registry checker must pass; this LEP's status transition is recorded per
   the chicken-and-egg clause.
2. **First runs** → LEP-0002 and LEP-0003 receive full lane fan-outs and
   judge-ratified dossiers posted to issues #121/#124 (their 14-day public
   windows run in parallel as before); owner signs each decision from the
   dossier with one reply.
3. **Steady state** → every material LEP decision requires a dossier at the
   decision SHA; dissent re-runs are open to any adopter continuously.
4. **Observability** → metrics recorded on the discussion issues: dossier
   turnaround (target <48h from request), dissent count, findings converted
   to CI tests per lane, and maintainer overrides of ARB verdicts (a
   maintainer MAY override — the decision was always theirs — and the
   override must be stated in the decision rationale).
5. **Rollback** → revert the LEP.md amendment PR. Recorded decisions survive
   untouched (they were always maintainer decisions citing evidence), and
   the charter/dossier history remains as public audit trail. No migration
   or compatibility window is needed because nothing external consumes this
   process.

## Deferred questions

- **Silence-is-consent automation** — rejected by this proposal's adoption
  model, kept as a deferred question. Trigger to revisit: after ≥5 complete
  ARB decision cycles with zero maintainer overrides of judge-ratified
  dossiers AND at least two independent external adopters having re-run the
  harness at published SHAs, the owner may reconsider auto-accept for
  non-PII/non-auth/non-retention proposals only.
- **Model monoculture** — one model family auditing code generated by the
  same family shares blind spots; charter makes cross-model judge SHOULD,
  not MUST. Trigger to revisit: first dissent or finding class that a
  second model family demonstrably catches and the first missed.
- **ARB review of ARB changes** — edits to the charter itself are material
  governance changes; who audits the auditors' manual is unresolved beyond
  dissent + public window. Trigger to revisit: first proposed charter
  amendment.
- **Third-party hosted re-runs** — adopters without local harness capacity
  rely on maintainers publishing invocation instructions. Trigger to
  revisit: ≥2 adopters requesting a hosted re-run facility.

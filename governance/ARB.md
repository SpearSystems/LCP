# LCP Autonomous Review Board (ARB) charter

Status of this document: **proposed process, inert until LEP-0004 is
Accepted**. Merging this file records the charter in draft; it changes no
existing requirement. On acceptance of
[LEP-0004](LEPs/LEP-0004-autonomous-review-board.md) this charter becomes the
normative definition of independent review for material LEP decisions, and the
amendments quoted in LEP-0004 land in [`LEP.md`](LEP.md).

Key words MUST, MUST NOT, SHOULD, and MAY are used as in
[SPEC.md](../SPEC.md) (RFC 2119).

## Purpose

The ARB replaces the human "independent second-maintainer review" *labor* —
not the accountability — with a codified adversarial audit performed by
agent reviewers whose output is public, reproducible, and challengeable. An
ARB **Review Dossier** that satisfies the quality gates below satisfies the
independent-maintainer requirement of `LEP.md` for the decision it covers.

The failure mode this charter exists to prevent is **self-attestation**: an
auditor that agrees with the author because it shares the author's context,
or a decision-maker who accepts a dossier that contains no attempt to be
wrong. Every rule below traces to that failure mode.

## Lanes

Each review is executed by one lane. Lanes are single-surface by design;
breadth per lane breeds shallow coverage.

| Lane | Surface | Primary questions |
|---|---|---|
| L-Spec | `SPEC.md` vs schemas vs conformance | every normative MUST/SHOULD implemented and tested; universal-core audit (SPEC.md §12) automated where possible |
| L-SDK (×10, one per SDK) | each SDK | the SDK security invariants of `docs/SDK-ROADMAP.md`; raw-body verification; constant-time comparison; TLS defaults; log cleanliness |
| L-Platform | reference platform + router semantics | auction integrity (no oversell, no replay, tenant scoping), dead-letter and retention behavior, consent evidence |
| L-Security | PII boundaries, auth rules, retention rules: SPEC vs code vs docs | consent evidence, auth-code taxonomy, retention/erasure behavior, logging hygiene; produces the **ARB-Security dossier** |
| L-MCP | MCP adapter | tool contract vs Python SDK reuse, event-loop blocking claims, auth |
| L-SupplyChain | CI workflows, action pins, SBOM, release paths | every pinned `uses:` SHA matches its claimed tag; trusted-publishing claims true as written; exception registries honest and unexpired |
| L-DocsDrift | governance, LEPs, docs | every `path:line` / `path:symbol` citation resolves at the reviewed commit; documented claims match code; changelog accuracy |
| L-Judge | the dossiers | rejects dossiers that are non-adversarial, non-reproducible, or vacuous |

A lane that cannot exercise its surface (missing toolchain, environment
limitation) MUST declare the limitation in the dossier and mark the verdict
`INCONCLUSIVE`; it MUST NOT paper over the gap with reasoning.

## Independence rules

1. **Fresh context.** Every auditor runs with no conversation history shared
   with the author of the artifact under review. Its inputs are this charter
   prompt, the dossier template, and the artifact itself.
2. **Author-never-audits.** No lane reviews an artifact it wrote. Authorship
   attaches to the **controller** of the lane run — the party that launches
   it, supplies its prompt, or curates its inputs — not to the lane's label:
   an author's own agent running a differently-named lane against the
   author's own artifact is a self-audit and does not satisfy G2 standing
   alone. A self-audit dossier is still public evidence; it MUST be labeled
   **SELF-AUDIT** in the independence attestation, and a decision that relies
   on one MUST state in its rationale that no non-author-controlled run covers
   the relied-on surfaces. A change authored in one lane MUST be audited by a
   lane that did not produce it; lanes are assigned to surfaces disjoint from
   their own authorship.
3. **One lane, one surface.** A reviewer MUST NOT audit a neighboring lane's
   surface to "help"; cross-surface observations are filed as findings
   against the owning lane, not as verdicts.
4. **Model diversity (SHOULD).** Where more than one model configuration is
   available, L-Judge and at least one other lane SHOULD use a model family
   different from the one that authored the change, to decorrelate blind
   spots. The absence of diversity MUST be stated in the dossier.

## Adversarial defect budget

- Every dossier MUST enumerate **at least five concrete refutation attempts**
  against the subject at a full commit SHA: crafted inputs, event orderings,
  edge cases, citation checks, boundary conditions — each with the command
  that executed it and the observed output.
- A `PASS` verdict WITHOUT the full enumerated refutation log is **void**:
  it is not a review, and L-Judge MUST reject it.
- Findings are reported with severity, file:line at the reviewed SHA, and a
  minimal reproduction. "Looks good" is not a possible output of a valid
  dossier.
- The budget counts attempts at refutation, not test executions; a lane that
  ran 50 tests but attempted no creative break has not met it. Attempts that
  failed to break the subject are legitimate dossier content and MUST be
  reported — they are the evidence the PASS is worth something.

## Reproducibility rules

1. **Every claim is command + observed output** at a full (not abbreviated)
   commit SHA, from a clean checkout. Claims without a reproduction command
   are opinion and MUST be labeled as such by the judge. A claim that depends
   on a constructed input (a plant, a crafted payload) MUST record that input
   byte-exactly — inline literal or committed fixture path at the head SHA —
   so a dissenter re-runs the same bytes, not a paraphrase.
2. **Durable findings become CI tests before the lane closes.** Any bug
   class, untested invariant, or falsified claim found in a dossier MUST be
   converted into a named, deterministic, CI-enforced test (or an explicit
   tracked waiver with reason) before the lane that found it is closed.
   Reports rot; tests do not.
3. **The harness is published.** The charter, the dossier template, and one
   canonical charter prompt per lane live in this repository
   (`governance/ARB.md`, `governance/ARB-DOSSIER-TEMPLATE.md`, and the lane
   prompts in `governance/ARB-PROMPTS.md`). A lane without a published prompt
   cannot produce a decision-bearing dossier. A dossier is re-runnable by a
   third party at the same commit, and MUST include the exact invocation that
   produced it.

## Role separation

- **Auditors** (all lanes except L-Judge) are read-only: they MAY open
  issues and issue comments; they MUST NOT push, open implementation PRs, or
  mutate any reviewed artifact.
- **Writers** are a separate role: test-author and fix agents that convert
  findings into code. A writer never audits its own output (independence
  rule 2 applies).
- **L-Judge** audits the auditors: it rejects dossiers with vacuous evidence,
  missing refutation budgets, broken reproductions, or independence
  violations, and it adjudicates dissents (below).
- **Only maintainer-side tooling merges.** ARB lanes and the judge do not
  merge anything. Decision statuses in `LEP-REGISTRY.md` are written only by
  maintainers. Agents NEVER mark a proposal Accepted on behalf of
  maintainers (AGENTS.md rule, unchanged and reaffirmed here).

## Public evidence

Dossiers land as comments on the public proposal or pull request they review,
not in private logs. Adopters read the evidence and the refutation budget
directly. Findings that open defects become issues. The repository's
anti-capture posture (see `governance/CONTRIBUTING.md`) requires this: a
review process the community cannot read is a review process the community
cannot trust.

## Dossier quality gates

A Review Dossier satisfies the independent-maintainer requirement only if
ALL gates hold (L-Judge enforces each):

- **G1** Subject pinned to a full commit SHA; claims reproduce from a clean
  checkout at that SHA, with constructed inputs recorded byte-exactly (the
  template's Plant bytes appendix).
- **G2** Independence attestation: fresh context, author-never-audits, lane
  surface matches the artifact, model-diversity statement present,
  **launch-control attestation present** (rule 2: authorship is the run's
  controller's, and a same-party run is labeled **SELF-AUDIT**).
- **G3** Refutation budget met: ≥5 enumerated adversarial attempts with
  commands and outputs.
- **G4** Every substantive claim carries command + observed output;
  non-reproducible statements are marked as inference.
- **G5** Durable findings converted to CI tests or tracked waivers before
  lane close.
- **G6** Verdict is one of `PASS`, `FINDINGS`, `BLOCKED`, `INCONCLUSIVE` and
  is consistent with the evidence; L-Judge has accepted the dossier.

The template is [`ARB-DOSSIER-TEMPLATE.md`](ARB-DOSSIER-TEMPLATE.md).

Dossiers age: a dossier certifies the subject **at its SHA**. Any material
change to the subject after that commit voids the verdict for the changed
surface and requires a fresh lane run.

## Dissent protocol (anti-capture)

1. Any adopter MAY re-run the published harness at the same commit as a
   decision's dossier and publish the resulting dossier as a comment on the
   same issue/PR.
2. A dissenting dossier that satisfies the quality gates (G1–G6, judged by
   the same charter) **BLOCKS the decision** — its acceptance, rejection, or
   deferral — until the conflict is adjudicated.
3. Adjudication is performed by re-running the disputed lanes and/or L-Judge
   on both dossiers; the outcome is recorded publicly on the same thread.
4. A non-conforming dissent (vacuous, non-reproducible, or outside the
   subject's surface) does not block; it is answered with the specific gate
   it failed.
5. Dissent rights are permanent and free: no membership, dues, private
   infrastructure, or repo access required beyond reading the public repo.

This clause exists so the process cannot degrade into vendor-controlled
self-attestation. The check on the robots is other people's robots.

## The chicken-and-egg clause

LEP-0004 cannot be reviewed by the machinery it creates. For this single
proposal: the ARB does not pre-approve itself. LEP-0004 is accepted, if at
all, by the sole maintainer as an explicitly recorded **process exception**,
justified by two facts stated plainly:

1. the change has zero protocol or wire impact — it reassigns *who does the
   reading*, never *what the protocol does*; and
2. the independent control for this exception is not the maintainer's word
   but **external re-runnability**: the charter (`ARB.md`), the per-lane
   charter prompts (`ARB-PROMPTS.md`), and the dossier template
   (`ARB-DOSSIER-TEMPLATE.md`) are public in-repo from the day they merge, so
   any adopter may audit the auditor by re-running it, and the 14-day public
   window plus the dissent protocol apply to LEP-0004 like any other
   proposal.

No future governance proposal may invoke this clause; once the ARB exists,
process changes are ARB subject matter like everything else.

## Standing decisions

- **Adoption model:** each material LEP decision is made by maintainer
  sign-off citing a dossier that passed the gates ("accept LEP-000N per ARB
  dossier <link>"), recorded in `LEP-REGISTRY.md` and the proposal.
- **Silence-is-consent is rejected** as a standing model for a protocol that
  carries personal data: no automated process may remove the named human
  from the legal/privacy decision point. It survives only as a deferred
  question with a trigger (LEP-0004).
- **PII/auth/retention gate:** proposals touching PII boundaries,
  authentication rules, or retention rules require, in addition to any ARB
  dossier, a **named human domain reviewer OR the maintainer's co-sign of
  the ARB-Security dossier**. Agents certify consistency with the
  specification; they do not and cannot certify legality or privacy fitness.

## Cost guards

- Per-lane retry cap: two full lane runs; the second run's dossier (whatever
  it concludes) is final for that cycle.
- No toolchain rabbit-holes: prove the claim with the locally available
  toolchain or declare the limitation in the dossier verdict as
  `INCONCLUSIVE`.
- Worktree and CI hygiene follow the existing repository pipeline; ARB lanes
  do not get privileges the pipeline denies ordinary PRs.

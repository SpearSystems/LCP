# ARB Review Dossier — <LEP-NNNN / PR-NNN / artifact id>: <one-line subject>

> Copy this template verbatim into the comment. Every `<placeholder>` MUST be
> filled; the judge rejects dossiers with empty or vacuous fields. See the
> quality gates G1–G6 in [`ARB.md`](ARB.md).

- **Subject:** <artifact under review, e.g. "LEP-0002 proposal + evidence tables">
- **Head commit (full SHA):** <40-char SHA — all citations and commands were executed at this commit, clean checkout>
- **Lane:** <L-Spec | L-SDK/<lang> | L-Platform | L-MCP | L-SupplyChain | L-DocsDrift | L-Judge>
- **Reviewer:** <harness name + model id + version/params, enough for a third party to re-run it>
- **Date:** <YYYY-MM-DD>

## Independence attestation

- Fresh context (no shared conversation with the artifact's author): yes/no — <how assured>
- Author-never-audits: <lane authorship vs artifact authorship statement>
- Surface disjointness: <this lane did not write any part of the subject>
- Model diversity: <same/different family than authoring lane; state the absence>
- Declared limitations: <environment gaps; anything not exercised → verdict cap `INCONCLUSIVE`>

## Verdict

**<PASS | FINDINGS | BLOCKED | INCONCLUSIVE>** — one line of rationale tied to
the evidence below. Consistency of verdict with evidence is a judge gate (G6).

## Claims and evidence

Every substantive claim = command + observed output at the head SHA (G1, G4).
One row per claim; paste outputs, do not paraphrase them.

| # | Claim | Command (clean checkout at SHA) | Observed output | Reproduced? |
|---|---|---|---|---|
| 1 | <claim> | `<exact command>` | `<exact output>` | yes |

Mark anything not backed by a command row as `[INFERENCE]`.

## Refutation attempts (defect budget: ≥5, G3)

Concrete adversarial attempts to break the subject — crafted inputs, event
orderings, edge cases, citation checks, boundary conditions. Attempts that
succeeded become findings below; attempts that failed to break it are still
required evidence that the PASS is worth something.

| # | Attack attempted | Command / construction | Result (broke / did not break, with observed behavior) |
|---|---|---|---|
| 1 | <attempt> | `<how>` | <outcome> |

## Findings

| # | Severity (blocker/major/minor) | File:line at SHA | Description | Minimal reproduction | Disposition |
|---|---|---|---|---|---|
| 1 | <sev> | `<path>:<line>` | <what is wrong> | `<repro>` | <finding / CI-test conversion / tracked waiver / declined+reason> |

Durable findings MUST be converted into a named CI test or an explicit
tracked waiver **before this lane closes** (G5). List the conversion PR /
issue numbers in Disposition; a finding left as prose is a gate failure.

## Dissent record

- Dissenting dossiers against this SHA/subject: <none | links>
- Blocks on open dissent: <none | what is blocked and pending what>

## Harness re-run instructions

Exact invocation so any adopter can reproduce this dossier at the head SHA:

```text
<commands / agent invocation, one per lane run>
```

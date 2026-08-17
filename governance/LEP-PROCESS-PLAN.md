# LEP process adoption plan

- **Date:** 2026-08-18
- **Status:** **Adopted** — process controls recorded in `governance/LEP.md`
  and validated by `tools/check_lep_registry.py`
- **Related:** [`governance/LEP.md`](LEP.md),
  [`governance/LEP-REGISTRY.md`](LEP-REGISTRY.md),
  [`docs/V1.1-ROADMAP.md`](../docs/V1.1-ROADMAP.md), and the LEP section of
  `AGENTS.md`

## Purpose

This plan operationalizes the LEP process for the first material changes after
v1.0. The process is now adopted for material protocol, schema, extension,
security, privacy, lifecycle, and cross-language changes. It does **not**
accept LEP-0001 or authorize batch/subscription implementation.

## Adopted decisions

1. **Record storage:** numbered Markdown records live under
   `governance/LEPs/`; [`LEP-REGISTRY.md`](LEP-REGISTRY.md) is the canonical
   numbered index. Public issue discussions are linked but do not replace the
   durable record.
2. **Numbering:** `LEP-0001` onward, sequential and never reused. Rejected,
   deferred, deprecated, and superseded proposals retain their numbers.
3. **Review roles:** the author and shepherd maintain the proposal; affected
   implementers, publishers, buyers, and security/privacy reviewers are
   invited; a second maintainer independently reviews material decisions.
4. **Review window:** material proposals receive a 14-day public review by
   default. A material change to scope, wire shape, privacy, or compatibility
   restarts the window.
5. **Status lifecycle:** `Discuss → Draft → Review → Accepted/Rejected/Deferred
   → Implemented → Deprecated/Superseded`. The registry and proposal status
   must agree; terminal decisions require a rationale and date.
6. **Traceability:** accepted LEPs are linked from implementation PRs/commits,
   affected schemas, vectors, SDK artifacts, OpenAPI/MCP changes, changelog,
   and release evidence as applicable.
7. **Core-change gate:** new universal message types require independent
   adopter evidence or documented interoperability necessity, an extension
   feasibility analysis, the universal-core audit, and the required SemVer
   bump.
8. **Independent decisions:** an umbrella discussion may cover related work,
   but capabilities with different semantics must have independent acceptance
   criteria and may be accepted or deferred separately.

## Implemented controls

- `governance/LEP.md` is the normative process document.
- `governance/LEP-REGISTRY.md` records LEP-0001 and defines registry rules.
- `tools/check_lep_registry.py` validates numbering, proposal links,
  metadata, required sections, and registry/document status agreement.
- `tools/tests/test_lep_registry.py` covers the current registry and malformed
  fixture cases.
- The tooling CI job runs the registry validator and its unit tests.
- `AGENTS.md` routes future agents to the LEP process before material edits and
  requires an accepted LEP before implementation.
- `.github/ISSUE_TEMPLATE/lep-proposal.md` and
  `.github/PULL_REQUEST_TEMPLATE.md` identify the required LEP workflow.
- `governance/CONTRIBUTING.md` routes human contributors through the same
  material-change gate and records exemptions in the pull request.
- `docs/V1.1-ROADMAP.md` contains the current LEP status table.

## Acceptance state

The process adoption acceptance conditions are complete:

- [x] Numbered Markdown storage and a canonical registry are established.
- [x] Sequential, never-reused numbering is documented and checked.
- [x] Roles, independent review, and the 14-day window are documented.
- [x] Status lifecycle and decision-record requirements are documented.
- [x] Traceability and implementation gates are documented.
- [x] The roadmap carries LEP status.
- [x] LEP-0001 is registered as a draft and remains unaccepted/unimplemented.

Adoption of the process is separate from acceptance of any protocol proposal.

## Pilot: LEP-0001

LEP-0001 is an umbrella draft for bounded batch submission and event
subscriptions. Its two tracks are independently decidable: batch may be
accepted while subscriptions are deferred, or vice versa. Before formal
Review:

1. open the public `[LEP]` discussion and collect adopter evidence;
2. identify the affected reference-platform, SDK, MCP, operations,
   security/privacy, publisher, and buyer reviewers;
3. decide whether the two tracks should remain one numbered record with
   separate decision sections or become linked subrecords without reusing a
   number;
4. document the extension feasibility analysis before approving new universal
   message types.

During Review, the batch track must resolve item limits, per-item idempotency,
partial failure, ordering, backpressure, and benchmark evidence. The
subscription track must resolve event allowlisting, tenant authorization,
privacy-safe filters, signing, at-least-once delivery, cursor replay, ordering,
retention, retry, and dead-letter behavior.

No runtime, schema, SDK, OpenAPI, MCP, or conformance implementation for either
track may merge while the registry status is `Draft` or `Review`.

## Ongoing governance

- Maintainers update the registry at every status transition.
- Material review changes restart the review deadline.
- The pilot is followed by a short process retrospective; any process change
  updates `LEP.md`, this plan, the registry rules, and the checker together.
- A second-maintainer decision record is required before the first accepted
  core or cross-language change.

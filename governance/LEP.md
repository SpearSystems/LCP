# LCP Enhancement Proposal process

An LCP Enhancement Proposal (LEP) is the public decision record for a
substantial protocol, schema, extension, or cross-language implementation
change. This process is **adopted for material changes**. It prevents
high-impact changes from arriving as undocumented fields or vendor-specific
defaults while keeping ordinary fixes lightweight.

The canonical numbered index is [`LEP-REGISTRY.md`](LEP-REGISTRY.md). The
adoption decisions and pilot sequence are in
[`LEP-PROCESS-PLAN.md`](LEP-PROCESS-PLAN.md).

## When an LEP is required

Open or update an LEP for:

- envelope, message type, error taxonomy, versioning, or security changes;
- a new interoperable vertical or a material vertical-schema revision;
- a new registered extension intended for multiple organizations;
- batch, subscription, multi-currency, or other cross-language behavior;
- a change that affects conformance vectors or all SDKs;
- a deprecation, compatibility, or governance change;
- a change to a PII boundary, authentication rule, retention rule, or lifecycle
  state.

A normal issue or pull request is sufficient for a typo, a documentation-only
clarification, a private deployment extension, or a bug that restores the
published behavior. When the boundary is uncertain, treat the change as LEP
material until maintainers record an exemption.

## Records and numbering

- Number proposals sequentially as `LEP-0001`, `LEP-0002`, and so on.
- Numbers are never reused, including after rejection, deferral, or
  supersession.
- Store the full proposal at
  `governance/LEPs/LEP-NNNN-<slug>.md`.
- Add or update the corresponding row in
  [`LEP-REGISTRY.md`](LEP-REGISTRY.md) at every status transition.
- A proposal may link to its public discussion issue, but the Markdown record
  remains the durable decision record.
- The registry checker (`python tools/check_lep_registry.py`) validates
  numbering, links, status agreement, metadata, and required sections in CI.

## Proposal stages and exit criteria

1. **Discuss** — open a `[LEP]` issue using the template. State the problem,
   affected users, alternatives, compatibility boundary, and evidence. No
   material implementation begins at this stage.
2. **Draft** — assign the next unused number, add the complete proposal
   record, include valid and invalid wire examples, and mark unresolved
   questions. The registry status is `Draft`.
3. **Review** — invite affected implementers, publishers, buyers, and
   security/privacy reviewers. The default public review window is 14 days.
   The registry status is `Review`; the review deadline is recorded. A
   material change to scope, wire shape, privacy, or compatibility restarts
   the review window.
4. **Decision** — maintainers record exactly one of `Accepted`, `Rejected`, or
   `Deferred`, with rationale, date, reviewers, and any conditions. A material
   proposal requires an independent second-maintainer review; security or
   privacy changes also require a named domain review. A single maintainer may
   make an urgent security decision only when the reason and follow-up review
   are recorded.
5. **Implement** — only an `Accepted` proposal may authorize implementation.
   Link the LEP from the implementation PR/commit and update applicable
   schemas, examples, vectors, OpenAPI/MCP contracts, generated SDK models,
   manifests, changelog, and release evidence.
6. **Verify** — run the complete relevant conformance, SDK/schema-sync,
   security/privacy, operational, and rollback checks. Add the verification
   evidence and implementation links to the record, then mark the registry
   `Implemented`.
7. **Deprecate or supersede** — retain the complete record and document the
   N+2 compatibility window, two-year grace period, migration path, and link
   to the superseding LEP when applicable.

A proposal can be `Deferred` without being lost. Its number and rationale
remain permanent, and a later proposal must link back to it rather than
silently reopening the same decision.

## Roles and independence

Each material LEP identifies:

- **Author:** owns the proposal and responses to review comments.
- **Shepherd:** maintains the issue, registry status, deadlines, and links.
- **Domain reviewers:** represent affected publishers, buyers, implementers,
  and SDK maintainers.
- **Security/privacy reviewer:** reviews PII, authorization, delivery,
  retention, replay, and abuse implications.
- **Independent maintainer:** reviews the decision separately from the author
  where possible and is required for material acceptance/rejection/deferral.
- **Decision maintainers:** record the final decision and rationale publicly.

No LEP grants ownership, fees, approval rights, or a veto over the open
extension registry. Review must remain open to affected adopters and must not
require membership, dues, or access to private infrastructure.

## Required proposal content

Every numbered proposal must include:

- title, author(s), date, status, and target version;
- problem statement and evidence from adopters or interoperability testing;
- scope: core, vertical, extension, reference implementation, or
  documentation;
- proposed wire shape with valid and invalid examples;
- privacy, security, and universal-core audit;
- compatibility, versioning, and deprecation impact;
- SDK, OpenAPI, MCP, conformance, and operational impact;
- alternatives, including keeping the behavior deployment-specific;
- rollout, migration, observability, and rollback plan;
- explicit deferred questions and a trigger for revisiting them.

## Decision rules

- Prefer a namespaced extension or reference-platform behavior over a new core
  field when the requirement is buyer-, publisher-, market-, or
  deployment-specific.
- Before adding a new universal message type, require evidence from at least
  two independent adopters or a documented interoperability necessity, plus an
  extension feasibility analysis.
- Prefer one stable vertical with a controlled category taxonomy over many
  near-duplicate vertical files.
- Reject arbitrary executable matching rules, hidden PII flows, and changes
  that make a v1 receiver fail on unknown optional data.
- A core change must satisfy the simplicity budget, pass the universal audit,
  and receive the protocol version bump required by `AGENTS.md` and `SPEC.md`.
- Separate capabilities with materially different semantics may share an
  umbrella discussion, but each must have independently reviewable and
  independently decidable acceptance criteria.

## Traceability and implementation gate

An accepted LEP is not a release by itself. Before implementation merges:

1. the PR links the accepted LEP and the registry row;
2. schema, example, vector, SDK, OpenAPI/MCP, and documentation impacts are
   explicitly checked;
3. generated bundles and SHA-256 manifests are synchronized;
4. security/privacy and operational reviewers sign off where applicable; and
5. the implementation and verification commits are added to the registry and
   proposal record.

The roadmap's LEP table and the registry are navigation aids; the proposal,
public discussion, decision rationale, and verification evidence remain the
source of truth.

## Current records

- [LEP registry](LEP-REGISTRY.md)
- [LEP process adoption plan](LEP-PROCESS-PLAN.md)
- [LEP-0001 batch/subscription draft](LEPs/LEP-0001-batch-submission-and-event-subscriptions.md)

LEP-0001 remains `Draft`: it is not accepted, and no batch or subscription
runtime implementation is authorized.

The current roadmap and deferred triggers are in
[`docs/V1.1-ROADMAP.md`](../docs/V1.1-ROADMAP.md).

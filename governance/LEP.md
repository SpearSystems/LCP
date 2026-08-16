# LCP Enhancement Proposal process

An LCP Enhancement Proposal (LEP) is the public decision record for a
substantial protocol, schema, extension, or cross-language implementation
change. The process is intentionally lightweight while the project is small;
it prevents high-impact changes from arriving as undocumented fields or
vendor-specific defaults.

## When an LEP is appropriate

Open an LEP for:

- envelope, message type, error taxonomy, versioning, or security changes;
- a new interoperable vertical or a material vertical-schema revision;
- a new registered extension intended for multiple organizations;
- batch, subscription, multi-currency, or other cross-language behavior;
- a change that affects conformance vectors or all SDKs;
- a deprecation, compatibility, or governance change.

A normal issue or pull request is sufficient for a typo, a documentation-only
clarification, a private deployment extension, or a bug that restores the
published behavior.

## Proposal stages

1. **Discuss** — open a `[LEP]` issue using the template and identify the
   problem, users, alternatives, compatibility boundary, and evidence.
2. **Draft** — add a numbered proposal document or link the complete proposal
   in the issue. Mark unresolved design questions explicitly.
3. **Review** — invite affected implementers, publishers, buyers, and security/
   privacy reviewers. Prefer concrete examples and negative cases over abstract
   consensus.
4. **Decision** — the maintainers record `accepted`, `rejected`, or `deferred`
   with rationale. A single maintainer may make an urgent security decision,
   but must record the reason and seek a second review afterward.
5. **Implement** — update schemas, examples, vectors, OpenAPI/MCP contracts,
   generated SDK artifacts, changelog, and release notes as applicable.
6. **Deprecate or supersede** — retain the decision record and document the
   N+2 compatibility window or migration path.

## Required proposal content

- Title, author(s), date, and status.
- Problem statement and evidence from adopters or interoperability testing.
- Scope: core, vertical, extension, reference implementation, or documentation.
- Proposed wire shape with valid and invalid examples.
- Privacy, security, and universal-core audit.
- Compatibility, versioning, and deprecation impact.
- SDK, OpenAPI, MCP, conformance, and operational impact.
- Alternatives considered, including keeping the behavior deployment-specific.
- Rollout, migration, observability, and rollback plan.
- Explicit deferred questions and a trigger for revisiting them.

## Decision rules

- Prefer an extension or reference-platform behavior over a new core field when
  the requirement is buyer-, publisher-, market-, or deployment-specific.
- Prefer one stable vertical with a controlled category taxonomy over many
  near-duplicate vertical files.
- Reject arbitrary executable matching rules, hidden PII flows, and changes
  that make a v1 receiver fail on unknown optional data.
- A core change must satisfy the simplicity budget, pass the universal audit,
  and receive the protocol version bump required by `AGENTS.md` and `SPEC.md`.
- No LEP grants ownership, fees, approval rights, or a veto over the open
  extension registry.

The current roadmap and deferred triggers are in
[`docs/V1.1-ROADMAP.md`](../docs/V1.1-ROADMAP.md).

# LCP — Lead Context Protocol Specification

> **Created and maintained by Spear Systems** (a Spear company).
> Open standard — Apache 2.0, free to implement.

**Status:** v1.0 — published specification.
**License:** Apache 2.0 (see [LICENSE](LICENSE)).

> This is the canonical specification. The design is grounded in the
> production ping/post wire contract (HMAC signing, idempotency,
> nonce replay protection, PII-stripped pings) and the industry-standard
> ping/post model. Findings from the deep-research review pass
> (`docs/lcp-deep-research-review.md`) have been resolved — see the
> review-log appendix (§14).

**This file is the normative index.** The specification is published as the
six documents under [spec/](spec/README.md); those files carry the normative
text, and this index carries the table of contents. Section numbers are
stable — every original `SPEC.md#N-…` anchor still resolves to the heading
below, and each heading links to the document that now owns the section.

| Document | Scope |
|---|---|
| [spec/00-conformance.md](spec/00-conformance.md) | Scope, conformance, governance, audit, review log |
| [spec/01-transport.md](spec/01-transport.md) | Transport and security |
| [spec/02-envelope.md](spec/02-envelope.md) | Envelope and message types |
| [spec/03-payloads.md](spec/03-payloads.md) | Payloads and lifecycle |
| [spec/04-errors.md](spec/04-errors.md) | Errors and status codes |
| [spec/05-capabilities.md](spec/05-capabilities.md) | Capabilities and markets |

## 1. Overview

Purpose, scope (lead data exchange, not money movement), market/channel/
vertical agnosticism, and the eleven design principles — [read in spec/00-conformance.md](spec/00-conformance.md#1-overview).

## 2. Envelope

The transport-agnostic envelope: message identity, sender/receiver, timestamps,
idempotency key, the canonical `test` flag, and the envelope schema — [read in spec/02-envelope.md](spec/02-envelope.md#2-envelope).

## 3. Canonical core

The vertical-free payload blocks (`consumer`, `location`, `compliance`,
`provenance`, `attributes`, `attachments`), the lead status machine, lead
expiry, and exclusivity slot semantics — [read in spec/03-payloads.md](spec/03-payloads.md#3-canonical-core).

## 4. Message types

The seven message types — `lead`, `call`, `ping`, `bid`, `post`, `ack`,
`event` — including the ping allowlist, the ping/bid/post auction flow, and
the event class rules — [read in spec/02-envelope.md](spec/02-envelope.md#4-message-types).

## 5. Error taxonomy

The structured error codes, their HTTP status mapping, the single shared error
shape, and the identity/replay rejection split — [read in spec/04-errors.md](spec/04-errors.md#5-error-taxonomy).

## 6. Versioning & extensibility

Protocol and schema semver, N+2 deprecation, the namespaced extension
mechanism, and the open-vs-closed enumeration policy — [read in spec/05-capabilities.md](spec/05-capabilities.md#6-versioning--extensibility).

## 7. Conformance tiers

The self-declared L1/L2/L3 tiers and what each one adds — [read in spec/00-conformance.md](spec/00-conformance.md#7-conformance-tiers).

## 8. Capability discovery

The capabilities document, version-string membership rules, capacity,
offer restrictions, delivery windows, and country discovery — [read in spec/05-capabilities.md](spec/05-capabilities.md#8-capability-discovery).

## 9. Security

TLS, API-key and HMAC authentication (canonical signing input and timestamp
form), per-pair hash schemes, agent attestation verification, rate limits, PII
handling, webhook signing, and the post timeout — [read in spec/01-transport.md](spec/01-transport.md#9-security).

## 10. Agent binding

Agent roles, agent identity, the abstract binding rules, and the MCP binding — [read in spec/01-transport.md](spec/01-transport.md#10-agent-binding).

## 11. Governance

Licensing, the anti-capture clause, the extension registry, self-declared
conformance, and the CLA — [read in spec/00-conformance.md](spec/00-conformance.md#11-governance).

## 12. Universal audit

The checklist that keeps the core vertical- and market-neutral — [read in spec/00-conformance.md](spec/00-conformance.md#12-universal-audit).

## 13. TODO (authoring order)

Authoring status: completed deliverables and the remaining trigger-gated
items — [read in spec/00-conformance.md](spec/00-conformance.md#13-todo-authoring-order).

## 14. Review-log appendix

The deep-research review findings, their resolutions, and the deferred
items with triggers — [read in spec/00-conformance.md](spec/00-conformance.md#14-review-log-appendix).

## Appendix A: Consent evidence provider examples

Non-normative examples of `consent_evidence` provider/type values — [read in spec/03-payloads.md](spec/03-payloads.md#appendix-a-consent-evidence-provider-examples).

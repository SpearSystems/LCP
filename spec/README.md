# LCP specification — file map

[SPEC.md](../SPEC.md) is the normative index: it carries the document title,
the status statement, and every top-level section heading as an anchor stub, so
existing `SPEC.md#N-…` links keep resolving.

The normative text lives in the six files below. Read them in numeric order;
section numbers are unchanged from the single-file specification and are not
renumbered by the split.

| File | Section numbers | Scope |
|---|---|---|
| [00-conformance.md](00-conformance.md) | §1, §7, §11, §12, §13, §14 | Conformance and scope: positioning and design principles, conformance tiers L1–L3, governance, the universal-core audit, authoring order, and the review-log appendix. |
| [01-transport.md](01-transport.md) | §9, §10 | Transport and security: TLS, API-key and HMAC authentication, canonical timestamps, per-pair hash schemes, agent attestation verification, rate limits, PII handling, webhook signing, the post timeout, and the agent binding layer. |
| [02-envelope.md](02-envelope.md) | §2, §4 | Envelope and message types: the transport-agnostic envelope and the seven message types (`lead`, `call`, `ping`, `bid`, `post`, `ack`, `event`) with their ping/bid/post flow. |
| [03-payloads.md](03-payloads.md) | §3, Appendix A | Payloads and lifecycle: the canonical core blocks, the status transition machine, lead expiry, exclusivity slot semantics, and the non-normative consent-evidence provider examples. |
| [04-errors.md](04-errors.md) | §5 | Errors and status codes: the error taxonomy, HTTP status mapping, the shared error shape, and the identity-versus-replay rejection split. |
| [05-capabilities.md](05-capabilities.md) | §6, §8 | Capabilities and markets: versioning and extensibility policy, the capabilities document, capacity, offer restrictions, delivery windows, and ISO 3166-1 country discovery. |

Cross-references of the form `§N` remain valid across files — the table above
maps each section number to its owning file. Links inside these files are
relative to `spec/` (for example `../schemas/offer.json`).

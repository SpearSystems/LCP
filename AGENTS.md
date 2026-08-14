# LCP — Lead Context Protocol

LCP is a universal, Apache-2.0 protocol for exchanging consumer lead
data (PII) between publishers, platforms, and buyers. This repository
holds the specification, JSON Schemas, test vectors, and reference
implementations.

## Scope

Work only in this repo. LCP is a standalone product — do not modify
other repositories from here, and do not reference internal platform
details in anything that could be published.

## Repository layout

```
LICENSE            Apache 2.0
SPEC.md            The canonical specification
schemas/           JSON Schema for the envelope, core, and message types
verticals/         Per-vertical attribute schemas
examples/          Sample payloads (lead, call, ping, post, ack, event)
test-vectors/      Conformance fixtures (L1/L2/L3)
governance/        CONTRIBUTING, CLA, extension registry
implementations/   Reference implementations (incl. MCP server)
docs/              Design notes and research
```

## Spec authoring rules

1. **Universal core.** The core (envelope, consumer, location,
   compliance, provenance) contains ZERO vertical-specific or
   market-specific fields. Everything vertical-specific lives in
   `attributes`, typed by JSON Schema per vertical. Run the universal
   audit checklist (SPEC.md §12) before any core change.
2. **Simplicity budget.** A competent developer must understand the
   core in two documents. New core fields must be justified against
   this budget; optional blocks are additive.
3. **Core vs. extension.** Core changes (envelope, message types, error
   taxonomy, versioning) require a MINOR/MAJOR version bump. New
   verticals are additive files in `verticals/`. Extensions are
   namespaced `{org}.{division}.{purpose}` and registered in
   `governance/EXTENSION-REGISTRY.md`.
4. **Versioning.** Semver on the protocol; per-schema versions;
   N+2 deprecation with 2-year grace. Unknown optional fields are
   ignored; unknown message types get a structured error.
5. **PII discipline.** Ping messages carry non-PII attributes + hashes
   only (forbidden-keys list in SPEC.md §4). Full PII flows only in
   post messages to the winning buyer.
6. **Machine-readable schemas.** Every message type and vertical ships
   a JSON Schema. A schema change without its schema file is not a
   change.

## Quality gate

- Every schema in `schemas/` and `verticals/` must validate its
  examples in `examples/`.
- Test vectors in `test-vectors/` must pass the conformance runner
  (L1/L2/L3) once one exists.
- No secrets, credentials, or internal platform details in this repo —
  it is publishable at any moment.

## Contribution

See `governance/CONTRIBUTING.md` (Apache 2.0, anti-capture, CLA).

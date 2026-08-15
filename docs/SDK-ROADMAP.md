# LCP SDK Program

This document defines the official SDK contract and support policy for LCP
implementations. It is separate from the wire protocol: an SDK is a developer
convenience layer and must never change the interoperable JSON contract.

## Scope

Every supported SDK should provide the same conceptual surface, using the
language's normal naming and async/concurrency conventions:

- Envelope and message builders.
- Schema validation for envelopes, messages, offers, and verticals, either
  bundled in the SDK or through an explicit validator hook appropriate to the
  language runtime.
- Canonical HMAC-SHA256 signing and verification.
- Timestamp freshness and replay-window checks.
- Idempotency-key helpers.
- HTTP client operations for leads, calls, bids, lifecycle events, status,
  schemas, capabilities, offers, attachment uploads/downloads, and quota
  reports.
- Webhook/request verification against the raw request body.
- Typed acknowledgement, error, and transport responses.
- Safe retry behavior for retryable responses; mutating retries require a
  stable idempotency key.
- Explicit test-mode support (`test: true` and `X-LCP-Test: true`).
- Typed call, attachment, payable-outcome, and monthly-quota models where the
  language SDK supports them.

An SDK does **not** implement offer matching, auction selection, persistence,
webhook queues, CRM integration, or a complete LCP exchange. Those belong to a
platform or application. The optional MCP adapter is also not part of the
core SDK contract.

## Security invariants

All SDKs must preserve these invariants:

1. HMAC covers the exact bytes sent on the wire:
   `<timestamp>\\n<idempotency-key>\\n<raw-request-body>`.
2. Verification accepts a raw byte body and never verifies a re-serialized
   JSON object.
3. Signature comparison is constant-time.
4. TLS certificate verification is enabled by default.
5. Replay-window validation happens before application processing.
6. Retried mutations reuse the same idempotency key and body.
7. Secrets and PII are not written to logs by default.
8. Test traffic cannot silently become production traffic.

The canonical cross-language HMAC fixture is
[`test-vectors/sdk/hmac.json`](../test-vectors/sdk/hmac.json). Every SDK CI job
must run it or an equivalent test derived from it.

## Support tiers

A tier is a maintenance commitment, not a claim that the languages are more
or less capable.

### Tier 1 — maintained production SDKs

- Python
- TypeScript/JavaScript
- Go
- C#/.NET

Tier 1 SDKs receive release testing against the reference platform, shared
signing-vector tests, security review for authentication code, API
compatibility documentation, and release notes.

### Tier 2 — maintained core SDKs

- Java
- PHP
- Rust
- Ruby
- Kotlin

Tier 2 SDKs provide the common builders, signing/verification, validation
hooks, and an idiomatic HTTP integration. They follow the same vectors and
compatibility policy. Their release cadence may be slower than Tier 1.

### Tier 3 — community-assisted SDKs

- Swift

Tier 3 SDKs are published reference implementations with shared vectors and
reviewed security primitives. They become Tier 2 after a maintainer, adopter,
and sustained CI ownership are available.

PHP is intentionally Tier 2 rather than Tier 3 because publisher and CMS
adoption makes it strategically important for LCP even though it is not a
priority language in the MCP SDK program.

## Compatibility policy

- SDK major versions track breaking public SDK API changes.
- SDK minor versions add backwards-compatible helpers.
- SDK patch versions contain compatible fixes.
- The SDK's supported LCP protocol versions are explicit in its README and
  capability helpers.
- A schema or wire-format change must update schemas, OpenAPI, examples,
  test vectors, every affected SDK, and the changelog in one change.
- A deprecated helper gets at least the protocol's N+2 deprecation window
  unless a security issue requires earlier removal.
- SDKs must reject unsupported closed message types and preserve unknown open
  enum values as required by SPEC.md §6.

The repository currently publishes reference packages from one repository.
Each package is independently versioned and should only be advertised as
stable after its package registry and Trusted Publishing configuration have
been enabled.

## Implementation status

| SDK | Tier | Status | Package path |
|---|---:|---|---|
| Python | 1 | Maintained reference SDK | `implementations/sdk/python` |
| TypeScript/JavaScript | 1 | Maintained reference SDK | `implementations/sdk/typescript` |
| Go | 1 | Maintained reference SDK | `implementations/sdk/go` |
| C#/.NET | 1 | Maintained reference SDK | `implementations/sdk/csharp` |
| Java | 2 | Reference SDK | `implementations/sdk/java` |
| PHP | 2 | Reference SDK | `implementations/sdk/php` |
| Rust | 2 | Reference SDK | `implementations/sdk/rust` |
| Ruby | 2 | Reference SDK | `implementations/sdk/ruby` |
| Kotlin | 2 | Reference SDK | `implementations/sdk/kotlin` |
| Swift | 3 | Reference SDK | `implementations/sdk/swift` |

The phrase “official SDK” means that the implementation follows this
contract and is tested in this repository. It does not imply that LCP operates
or certifies a hosted service.

## Release gates

Before promoting an SDK from reference to maintained status:

1. Shared HMAC and envelope vectors pass.
2. The SDK's own unit tests pass on its supported runtime versions.
3. It provides bundled schema validation or a documented validator hook and
   can communicate with the reference platform sandbox.
4. Raw-body webhook verification is covered by a negative test.
5. Package metadata, license, security reporting, and README are complete.
6. No dependency has an unresolved blocking vulnerability.
7. The release workflow produces an immutable package artifact and SBOM.

## Schema validation and code generation

The canonical schema set is the source of truth. It contains the ten core
schemas (`envelope`, `core`, seven message schemas, and `offer`) plus the five
vertical schemas. `tools/generate_sdk_models.py --write` produces, for every
SDK, a complete offline `schema-bundle.json` and a manifest containing the
SHA-256 digest of every source schema. `tools/check_sdk_schema_sync.py --check`
rejects stale manifests, missing generated model families, and incomplete
bundles.

Generated declarations cover the envelope/message metadata, all seven payload
shapes, and buyer offers. They are convenience types for editors and
serializers; runtime acceptance always uses a standards-based Draft 2020-12
validator. Validators must load the complete bundle before validating so that
`$ref` resolution never downloads schemas from the network. Validation order is:

1. Validate the envelope.
2. Read the closed `lcp.message.type` value.
3. Validate the payload with its message schema.
4. For pings, enforce the vertical `ping_safe` policy in addition to JSON Schema.
5. Apply authentication, authorization, replay, idempotency, and PII policy.

No SDK should log the schema bundle, payload, or validation details if they
contain PII. Schema changes require regenerated declarations and manifests in
the same pull request.

## Package publication

`SDK_VERSION` is the coordinated SDK release version. A release tag must match
it and all package metadata that carries an explicit version. The release
workflows build from clean checkouts, regenerate the schema
bundle, run the compatibility matrix, create package/source SBOMs, and publish
only from the protected `release` environment. A version tag must also pass the
[Test](https://github.com/SpearSystems/LCP/actions/workflows/test.yml),
[security](https://github.com/SpearSystems/LCP/actions/workflows/security.yml),
[SDK compatibility](https://github.com/SpearSystems/LCP/actions/workflows/sdk.yml),
Python, SDK, and container workflows before the
[signed tagged release workflow](RELEASE.md) creates a GitHub release record.
The release record contains a Sigstore-signed manifest and release notes so
adopters can verify the commit, schema bundle, package coordinates, and source
SBOM independently of the GitHub UI.

| Registry | SDKs | Authentication |
|---|---|---|
| PyPI | Python, MCP adapter, reference platform | PyPI Trusted Publishing (GitHub OIDC) |
| npm | TypeScript | npm trusted publisher plus provenance |
| NuGet | C#/.NET | NuGet Trusted Publishing (`NuGet/login`) |
| Maven Central | Java (`com.spearsystems:lcp-sdk`), Kotlin (`com.spearsystems:lcp-sdk-kotlin`) | Central Portal user-token credentials in the protected environment |
| Packagist | PHP | Git tag mirroring; Packagist webhook/token is not stored in CI |
| crates.io | Rust | crates.io Trusted Publishing (GitHub OIDC) |
| RubyGems | Ruby | RubyGems Trusted Publishing (GitHub OIDC) |
| Go module proxy | Go | Signed Git tags; the proxy indexes the module automatically |
| Swift Package Manager | Swift | Signed Git tags; SwiftPM consumes the repository URL |

Registry setup is intentionally a separate operator action. Until the
publisher identity is registered, the workflow can build and attest packages
without publishing. Never replace OIDC with a long-lived token merely to make a
release green. Package publication and the final GitHub release-record job are
protected by the `release` environment; the non-publishing release dry run does
not use that final write-capable job. Configure the environment and branch/tag
rules using [`MAINTAINER-RELEASE-SETUP.md`](MAINTAINER-RELEASE-SETUP.md). The
complete tag and verification procedure is in [`docs/RELEASE.md`](RELEASE.md).

## MCP relationship

MCP is an agent transport/binding. LCP SDKs are for publishers, buyers,
platforms, webhooks, and services in any language. The LCP MCP adapter should
consume the Python SDK's signing and HTTP primitives rather than maintain a
second implementation of them. Other language SDKs do not need to implement
MCP unless an adopter has a concrete agent integration requirement.

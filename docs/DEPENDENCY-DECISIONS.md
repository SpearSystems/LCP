# Dependency update decisions

> **Maintainer page · Dependency review log**

This page records the decisions taken when working through the Dependabot
update pull requests. It is a decision record, not a policy: every update is
still reviewed against the SDK compatibility workflow, the conformance
vectors, and the security gate before it is merged.

## Applied updates

| Ecosystem | Change | Notes |
|---|---|---|
| GitHub Actions | `actions/checkout` 5.1.0 → 7.0.1 | Node 24 runtime; no input changes. |
| GitHub Actions | `actions/setup-node` 4.4.0 → 7.0.0 | Node 24 runtime; `node-version` input unchanged. |
| GitHub Actions | `actions/setup-python` 6.3.0 → 7.0.0 | No input changes. |
| GitHub Actions | `actions/setup-java` 4.9.1 → 5.7.0 | No input changes. |
| GitHub Actions | `actions/setup-dotnet` v4 → v6.0.0 | Node 24 runtime; clears the Node.js 20 deprecation warning. |
| GitHub Actions | `sigstore/cosign-installer` 3.9.0 → 4.1.2 | Installs the cosign 3.x CLI. Cosign v3 enables signing-config by default, so any invocation that uses `--tlog-upload=false` must also pass `--use-signing-config=false` (the Kyverno admission fixture was updated accordingly; the keyless release signing path is unaffected). |
| GitHub Actions | `actions/dependency-review-action` 4.9.0 → 5.0.0 | No input changes. |
| GitHub Actions | `rust-lang/crates-io-auth-action` 1.0.3 → 1.0.5 | No input changes. |
| GitHub Actions | `ruby/setup-ruby` 1.0.0 → 1.321.0 | Aligned the release workflow with the compatibility workflow. |
| Rust | `hmac` 0.12 → 0.13 | Requires importing `hmac::KeyInit`; verified with cargo test. |
| Rust | `sha2` 0.10 → 0.11 | Verified with cargo test. |
| Rust | `getrandom` 0.3 → 0.4 | Verified with cargo test. |
| Rust | `jsonschema` 0.37 → 0.49 | Verified with cargo test. |
| Go | `github.com/santhosh-tekuri/jsonschema/v6` 6.0.1 → 6.0.3 | Verified with `go test`. |
| TypeScript | `typescript` 5.x → 7.0.2 | TypeScript 7 no longer auto-includes `@types/node`; the SDK now declares `"types": ["node"]` in `tsconfig.json`. |
| TypeScript | `@types/node` 22.x → 26.2.0 | Verified with the TypeScript build and HMAC test vectors. |
| TypeScript | `ajv` 8.17.1 → ^8.18.0 | Fixes GHSA-2g4f-4pwh-qvx6 (ReDoS via `$data`); verified with `npm test`. |
| Java | `junit-jupiter` 5.14.4 → 6.1.3 | JUnit 6 major; JDK 17 toolchain and Surefire 3.5.6 already support it; verified with `mvn test`. |
| Java | Jackson `jackson-core`/`jackson-databind` pinned 2.18.3 → 2.18.9 | networknt 1.5.9 pulls 2.18.3, which is affected by nine CVEs (GHSA-r7wm-3cxj-wff9, GHSA-j3rv-43j4-c7qm, GHSA-rmj7-2vxq-3g9f and others). Explicit nearest-wins dependencies pin the patched 2.18.9 line on the same 2.x API; verified with `mvn test` and the dependency tree. |
| Kotlin | Jackson `jackson-core`/`jackson-databind` pinned 2.18.3 → 2.18.9 | Same CVEs and rationale as Java; explicit higher versions win Gradle conflict resolution; verified with the Gradle test suite. |
| Java | `maven-source-plugin` 3.3.1 → 3.4.0 | Build plugin; verified with `mvn test`. |
| Java | `maven-javadoc-plugin` 3.11.2 → 3.12.0 | Build plugin; verified with `mvn test`. |
| Java | `maven-gpg-plugin` 3.2.7 → 3.2.8 | Build plugin; verified with `mvn test`. |
| Java | `maven-surefire-plugin` 3.5.2 → 3.5.6 | Build plugin; verified with `mvn test`. |
| Java | `central-publishing-maven-plugin` 0.9.0 → 0.11.0 | Build plugin; verified with `mvn test`. |
| Kotlin | Kotlin JVM Gradle plugin 2.0.21 → 2.4.10 | Verified with the Gradle test suite on JDK 17. |

## Deferred updates

| Dependency | Proposed | Why deferred |
|---|---|---|
| Java `com.networknt:json-schema-validator` | 1.5.9 → 3.0.6 | The 3.x line is a full API rewrite on Jackson 3 (`tools.jackson`): `JsonSchemaFactory`, `JsonSchema`, `ValidationMessage`, and `SpecVersion.VersionFlag` no longer exist, and the validator must be re-architected around the new `Schema` model. The 2.x line has the same class removal. The SDK keeps the proven 1.5.9 API, and the Jackson CVEs on the transitive 2.18.3 are mitigated by pinning 2.18.9. Tracked as follow-up work before any Java/Kotlin SDK breaking release. |
| Kotlin `com.networknt:json-schema-validator` | 1.5.9 → 3.0.6 | Same reason as Java; the Kotlin SDK shares the same validation API surface and Jackson pin. |

Deferring an update means the Dependabot PR is closed with this record as the
reason. The deferred items are candidates for a dedicated
`implementations/sdk` migration milestone with its own compatibility gate.

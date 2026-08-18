# Release record — v1.0.2

> **Status: published on 2026-08-18.**
>
> The signed `v1.0.2` tag resolves to commit
> `7ea57f1aabd0ac583e40541360119468779940c7`. The GitHub release and signed
> evidence assets are published at
> <https://github.com/SpearSystems/LCP/releases/tag/v1.0.2>. Do not move or
> reuse this immutable version.

## 1. Release identity

- [x] Coordinated `SDK_VERSION` and package metadata: `1.0.2`.
- [x] Reference platform package: `lcp-reference-platform==1.0.2`.
- [x] Release changes are additive implementation/tooling changes; no universal
      schema, message type, or wire-contract change was made.
- [x] Published commit SHA: `7ea57f1aabd0ac583e40541360119468779940c7`.
- [x] Signed annotated tag: `v1.0.2`.
- [x] GitHub release record and signed evidence assets are published.

## 2. Included changes

- [x] Repository audit remediation (items 1–13): attachment authorization
      boundary, WSGI binary attachment parity, PostgreSQL offer discovery,
      runtime lifecycle transition enforcement, equivalent ten-SDK validators
      with a shared validation corpus, sensitive vertical ping policy,
      expiry/consent/withdrawal/erasure lifecycle, MCP adapter hardening,
      role-specific lead-status projections, offer delivery windows and
      conditional bid fields, release metadata/evidence, governance and
      operational hardening, and the documentation consistency pass.
- [x] Kotlin SDK Jackson 3 migration (compile fix).
- [x] `python-multipart` 0.0.32 pin (16 Dependabot alerts resolved).
- [x] CI tooling dependency fix (`httpx` for the validation-corpus test).
- [x] Load-benchmark expiry fix (deliver mode now routes and delivers).
- [x] LEP process adoption plan and draft LEP-0001 filed (deferred, no code).

## 3. Release evidence and verification

- [x] `python tools/check_sdk_versions.py --check` passed: 1.0.2.
- [x] `python tools/check_sdk_schema_sync.py --check` passed: 10 SDKs / 16 schemas.
- [x] `python test-vectors/conformance.py` passed: 30/30.
- [x] Reference-platform suite passed: 57 tests (incl. real Postgres 16).
- [x] Tooling suite passed: 44 tests (incl. link checker and stale-state check).
- [x] All seven CI workflows green on the release commit
      (Test, SDK compatibility, Security and supply chain, Performance,
      Publish/sign/attest, Attribution policy, NuGet submission).
- [x] Tag-triggered release workflows passed (Test, Security, SDK compatibility,
      Publish SDKs, Publish Python, container, signed release).
- [ ] Download the release assets and run
      `tools/verify_release_evidence.py`; record the reviewer and date below.

Evidence source: <https://github.com/SpearSystems/LCP/releases/tag/v1.0.2>.
The release manifest is authoritative for the published container digest; do
not infer or deploy the image from its mutable tag.

## 4. Approval and publication record

- [x] Single-maintainer approval (user decision 2026-08-18; no second
      reviewer required for this patch).
- [x] Immutable annotated tag `v1.0.2` created and published.
- [x] Tagged Test, Security and supply-chain, SDK compatibility, package,
      container, and signed-release workflows completed.
- [x] All 12 packages published to their registries (PyPI, npm, NuGet, Maven
      Central ×2, crates.io, RubyGems, Packagist/Go/Swift via tag).
- [ ] Record the offline evidence reviewer, package checksums, and deployed
      container digest in the release-operations log. The signed manifest
      remains the source of truth for the digest.

## 5. Rollback and compatibility

This patch release does not alter the v1.0 protocol contract. Operators may
upgrade the reference platform and SDK artifacts independently of producers and
buyers that remain on v1.0 schemas. If a package or container issue is found,
quarantine the affected artifact and issue a new immutable patch; never replace
`v1.0.2` after publication.

---

**Release guide:** [RELEASE.md](RELEASE.md) · **Previous release record:** [v1.0.1](RELEASE-TICKET-1.0.1.md)

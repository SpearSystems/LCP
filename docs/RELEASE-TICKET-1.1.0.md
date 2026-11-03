# Release record — v1.1.0

> **Status: published on 2026-11-03.**
>
> The `v1.1.0` tag resolves to commit
> `68a22b5f7f36ea6321a21428267fbc5ab8bdf92c`. The GitHub release and its signed
> evidence assets are published at
> <https://github.com/SpearSystems/LCP/releases/tag/v1.1.0>. Do not move or
> reuse this immutable version — the tag is protected against update, deletion,
> and force-push, and every registry version below is immutable at the registry.

## 1. Release identity

- [x] Coordinated `SDK_VERSION` and package metadata: `1.1.0`
      (`tools/check_sdk_versions.py --check` passes on the tagged commit).
- [x] Published commit SHA: `68a22b5f7f36ea6321a21428267fbc5ab8bdf92c`.
- [x] Annotated tag `v1.1.0` pushed after the versioned Test, Security, SDK
      compatibility, and conformance workflows passed for that commit.
- [x] GitHub release, `release-manifest.json`, release notes, source and
      package SBOMs, provenance statements, and their Sigstore bundles published.
- [x] Additive MINOR: no universal-schema, message-type, or wire-contract
      break. v1.0 receivers keep receiving bit-for-bit v1.0 bytes.

The Git tag is annotated but not GPG-signed: the maintainer's local signing key
is not configured, which `docs/RELEASE.md` allows ("signed Git tag is
recommended but the release workflow also signs the release record with a
GitHub OIDC-backed Sigstore identity"). Every release asset carries that
Sigstore signature.

## 2. Included changes

- **LEP-0002** — canonical whole-second timestamp generation (Java, Kotlin,
  Rust) with a blocking ten-SDK signature-interop CI gate.
- **LEP-0003** — shared-lead slot allocation: capability-gated `slots_available`
  / `slots_claimed`, per-offer budget enforcement, and per-pair keyed
  `contacted_hashes` exchange.
- **LEP-0005** — paginated request-history read surface
  (`GET /v1/lcp/history/requests`, MCP `get_request_history`) with the tenant
  decision point and erasure integration.
- **LEP-0006** — marketplace migration: publisher `FINAL_PRICING` feedback with
  bounds enforcement, and lead/post `extensions` + `{org}.form.questions` behind
  the generalized PII validator (reject at ingest, redact at emission).
- **LEP-0008** — verified agent attestation: the 13-step §5 verifier with
  issuer-anchored JWKS, P1363-only ES256, kid-norm A, observe/enforce rollout,
  and `conformance_level` L3 only in `enforce` mode.
- **LEP-0004** — the autonomous review board process and this release's
  evidence-driven review trail.
- **Conformance restorations** — §5 intake codings (`LCP-009`, `LCP-010`,
  `LCP-007`), `pricing.is_duplicate_resub`, and the normative assignment of the
  whole §5 auth family (identity/authorization vs. replay protection).
- **Verification infrastructure** — the SPEC→test coverage matrix is now a
  blocking gate (156 clauses: 145 green, 0 gap, 11 exempt), and `main` plus `v*`
  tags are protected by rulesets.

## 3. Verified publication

Registry versions read back from each registry API on 2026-11-03:

| Registry | Coordinate | Version |
|---|---|---|
| PyPI | `lcp-sdk` | 1.1.0 |
| PyPI | `lcp-mcp-server` | 1.1.0 |
| PyPI | `lcp-reference-platform` | 1.1.0 |
| npm | `@spear-systems/lcp-sdk` | 1.1.0 |
| NuGet | `LcpSdk` | 1.1.0 |
| crates.io | `lcp-sdk` | 1.1.0 |
| RubyGems | `lcp-sdk` | 1.1.0 |
| Maven Central | `systems.spear:lcp-sdk` | 1.1.0 |
| Maven Central | `systems.spear:lcp-sdk-kotlin` | 1.1.0 |
| Go module proxy | `github.com/SpearSystems/LCP/implementations/sdk/go` | v1.1.0 indexed |
| Swift Package Manager | repository tag | v1.1.0 |
| Packagist | `spearsystems/lcp-sdk` | **not registered** — see §5 |

## 4. Reference container

```text
ghcr.io/spearsystems/lcp-reference-platform@sha256:07cb01350446c841651b450f480b38e1d44ca9142a4dd3eae8d7c2855f4a619e
```

Immutable digest for `v1.1.0`; recorded from `lcp-container-release.json`
(`workflow_run_id` 41637333052, commit `68a22b5`). Deploy the digest, never the
movable tag.

**Access verified public:** the package grants an anonymous pull token and
serves both the `v1.1.0` tag and the digest above (`ghcr.io/v2/…/manifests/…`
→ `200`), so `docker pull` needs no credential.

A newly published container package starts **private** even when its linked
repository is public — GitHub inherits access permissions from the repository
but not visibility — and the organization policy must permit public packages
before the package setting can be changed. Both were checked during this
release; `docs/RELEASE.md` and `docs/MAINTAINER-RELEASE-SETUP.md` now carry the
pre-flight step so the next release cannot ship a private image by accident.

## 5. Known follow-ups carried out of this release

- **#193** — a buyer that never acknowledges a delivered post is still not
  auto-expired or re-offered; delivery failures land in the dead-letter queue
  and reconciliation is operator-driven (`docs/OPERATIONS.md`). Lifecycle
  change ⇒ v1.2 design item.
- **Packagist** — `spearsystems/lcp-sdk` is not registered on packagist.org, so
  the documented "signed tag mirroring" does not currently happen. PHP installs
  from the signed tag as a Composer VCS repository in the meantime; registering
  the package (submit the repository URL on packagist.org) is an operator action
  with no CI credential involved.
- **LEP-0005** R1 OpenAPI pattern-equivalence vector; **LEP-0006** SDK
  typed-model declarations for extension blocks; **LEP-0003** email-form entry
  emission remains §9-gated.
- **LEP-0001 / LEP-0007** remain Deferred with recorded revival triggers;
  batch submission and event subscriptions are explicitly not in v1.1.
- **Governance** — single maintainer: zero required approvals on `main` and
  permitted release-environment self-review, plus unsigned contributor commits,
  are documented deviations in `docs/MAINTAINER-RELEASE-SETUP.md`.

## 6. Verification

```bash
git fetch --tags && git tag -v v1.1.0            # annotated tag; assets are Sigstore-signed
gh release download v1.1.0                        # manifest + notes + SBOMs + bundles
cosign verify-blob --bundle release-notes.md.sigstore.json \
  --certificate-identity-regexp 'https://github.com/SpearSystems/LCP/' release-notes.md
python3 tools/check_sdk_versions.py --check
python3 test-vectors/conformance.py               # 63/63
python3 tools/check_coverage_matrix.py --fail-on-gap
```

Registry and container coordinates are pinned in
`release-manifest.json` inside the signed release assets.

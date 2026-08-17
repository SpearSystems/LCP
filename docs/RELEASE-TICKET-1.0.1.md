# Release record — v1.0.1

> **Status: published on 2026-08-17.**
>
> The signed `v1.0.1` tag resolves to commit
> `61886511d2c9424ffb197d0788b049216a4645a2`. The GitHub release and signed
> evidence assets are published at
> <https://github.com/SpearSystems/LCP/releases/tag/v1.0.1>. Do not move or
> reuse this immutable version.

## 1. Release identity

- [x] Coordinated `SDK_VERSION` and package metadata: `1.0.1`.
- [x] Reference platform package: `lcp-reference-platform==1.0.1`.
- [x] Release changes are additive implementation/tooling changes; no universal
      schema, message type, or wire-contract change was made.
- [x] Published commit SHA: `61886511d2c9424ffb197d0788b049216a4645a2`.
- [x] Signed annotated tag: `v1.0.1`.
- [x] GitHub release record and signed evidence assets are published.

## 2. Included changes

- [x] Conservative indexed offer candidate selection for vertical, country,
      category, region, postal, requirement-profile, and service-area
      necessary conditions.
- [x] Fallback candidates for uncertain definitions; the existing full matcher
      remains authoritative and fail-closed.
- [x] Candidate-selection regression benchmark with a minimum 90% reduction
      threshold on the 96-offer synthetic profile.
- [x] Non-publishing PostgreSQL 18.4 performance workflow with intake and
      delivery thresholds plus retained JSON/log artifacts.
- [x] Coordinated package metadata and changelog entry.

## 3. Release evidence and verification

- [x] `python tools/check_sdk_versions.py --check` passed before publication.
- [x] `python tools/check_sdk_schema_sync.py --check` passed before publication.
- [x] `python test-vectors/conformance.py` passed 27/27 vectors before publication. The current post-release tree passes 30/30; the three sensitive-ping vectors were added after this tag.
- [x] Reference-platform and SDK compatibility gates passed in the tagged
      release workflows.
- [x] Candidate-index and PostgreSQL performance evidence was retained by CI.
- [x] `release-manifest.json`, SBOMs, provenance, and Sigstore bundles are
      published as GitHub release assets.
- [ ] Download the release assets and run
      `tools/verify_release_evidence.py`; record the reviewer and date below.

Evidence source: <https://github.com/SpearSystems/LCP/releases/tag/v1.0.1>.
The release manifest is authoritative for the published container digest; do
not infer or deploy the image from its mutable tag.

## 4. Approval and publication record

- [ ] Independent maintainer/reviewer verification is recorded for this
      historical release; the v1.0.0 single-maintainer exception must not
      silently become the normal process.
- [x] Immutable annotated tag `v1.0.1` was created and published.
- [x] Tagged Test, Security and supply-chain, SDK compatibility, package,
      container, and signed-release workflows completed.
- [ ] Record the offline evidence reviewer, package checksums, and deployed
      container digest in the release-operations log. The signed manifest
      remains the source of truth for the digest.

## 5. Rollback and compatibility

This patch release does not alter the v1.0 protocol contract. Operators may
upgrade the reference platform and SDK artifacts independently of producers and
buyers that remain on v1.0 schemas. If a package or container issue is found,
quarantine the affected artifact and issue a new immutable patch; never replace
`v1.0.1` after publication.

---

**Release guide:** [RELEASE.md](RELEASE.md) · **Previous release record:** [v1.0.0](RELEASE-TICKET.md)

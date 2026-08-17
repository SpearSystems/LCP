# Release candidate record — v1.0.1

> **Status: candidate prepared, not tagged or published.**
>
> This record tracks the coordinated patch release for the reference-platform
> extensions. It must be completed only after the non-publishing workflow and
> offline evidence review pass; do not move or reuse `v1.0.0`.

## 1. Candidate identity

- [x] Coordinated `SDK_VERSION` and package metadata: `1.0.1`.
- [x] Reference platform package: `lcp-reference-platform==1.0.1`.
- [x] Candidate changes are additive implementation/tooling changes; no
      universal schema, message type, or wire-contract change was made.
- [ ] Candidate commit SHA: record the pushed commit SHA here before tagging.
- [ ] Signed annotated tag: `v1.0.1` (not created by this preparation change).
- [ ] GitHub release record: create only after all tagged workflows pass.

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

## 3. Required dry-run gates

- [ ] `python tools/check_sdk_versions.py --check`.
- [ ] `python tools/check_sdk_schema_sync.py --check`.
- [ ] `python test-vectors/conformance.py`.
- [ ] Reference-platform and tooling test suites pass.
- [ ] Candidate-index benchmark passes its threshold.
- [ ] PostgreSQL 18.4 performance workflow passes its thresholds.
- [ ] Run the manual `release.yml` workflow with `tag=v1.0.1` and the candidate
      SHA. Confirm it is a non-publishing dispatch and retain its dry-run
      evidence artifact.
- [ ] Run `tools/verify_release_evidence.py` against the downloaded dry-run
      artifact; review signatures, manifests, SBOMs, provenance, and digests.
- [ ] Confirm all registry coordinates for `1.0.1` are absent before a real
      tag using `tools/check_release_registries.py --expect-absent`.

## 4. Approval and publication

- [ ] Independent maintainer/reviewer approval is recorded. The v1.0.0
      single-maintainer exception must not silently become the normal process.
- [ ] Create and push the immutable annotated tag only after the dry-run gates
      pass:

  ```bash
  git tag -a v1.0.1 -m "LCP v1.0.1"
  git push origin v1.0.1
  ```

- [ ] Verify tagged Test, Security and supply chain, SDK compatibility, SDK
      publication, Python publication, container, and signed-release workflows.
- [ ] Record package checksums, the signed release manifest, and the deployed
      container digest after publication.

## 5. Rollback and compatibility

This patch release does not alter the v1.0 protocol contract. Operators may
upgrade the reference platform and SDK artifacts independently of producers and
buyers that remain on v1.0 schemas. If a package or container issue is found,
quarantine the affected artifact and issue a new immutable patch; never replace
`v1.0.1` after publication.

---

**Release guide:** [RELEASE.md](RELEASE.md) · **Previous release record:** [v1.0.0](RELEASE-TICKET.md)

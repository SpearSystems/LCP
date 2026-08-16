# Release record — v1.0.0

> **Maintainer page · Release record · Page 3 of 6**
>
> **Status: complete.** This record documents the signed v1.0.0 publication
> and the evidence reviewed after the tag was released. For a future release,
> copy this file, change the version, and reset only the version-specific gates.

## 1. Candidate and release identity

- [x] Candidate commit: `020a6f3110db9cf5389454d62d8367d7e80e70d4`.
- [x] Coordinated `SDK_VERSION` and package metadata: `1.0.0`.
- [x] Signed annotated tag: `v1.0.0`, resolving to the candidate commit.
- [x] GitHub release record: [SpearSystems/LCP v1.0.0](https://github.com/SpearSystems/LCP/releases/tag/v1.0.0).
- [x] Final signed-release workflow: [run 31968977535](https://github.com/SpearSystems/LCP/actions/runs/31968977535).

## 2. Release gates

- [x] Test, conformance, SDK compatibility, platform, and Postgres checks passed.
- [x] Security, dependency, CodeQL, SBOM, container-scan, and admission checks passed.
- [x] Non-Python SDK publication workflow passed after immutable-version retry
      handling was added for partially completed publication attempts.
- [x] Python publication workflow passed for `lcp-sdk`, `lcp-mcp-server`, and
      `lcp-reference-platform`.
- [x] Container publication, signature, provenance, and SBOM attestations passed.
- [x] Protected final release-record publication completed.

The tag-triggered SDK run is retained at
[Publish SDKs #9](https://github.com/SpearSystems/LCP/actions/runs/31968977477),
and the Python and container runs are retained with the release workflow's
upstream checks.

## 3. Evidence and registry results

- [x] The release manifest contains the coordinated package evidence records.
- [x] Source archives, SBOMs, provenance statements, and Sigstore bundles were
      generated for the exact tagged commit.
- [x] npm package: `@spear-systems/lcp-sdk@1.0.0`.
- [x] PyPI packages: `lcp-sdk`, `lcp-mcp-server`, and
      `lcp-reference-platform`, all at `1.0.0`.
- [x] NuGet package: `LcpSdk` owned by `SpearSystems`, at `1.0.0`.
- [x] Maven Central coordinates: `systems.spear:lcp-sdk` and
      `systems.spear:lcp-sdk-kotlin`, at `1.0.0`.
- [x] crates.io package: `lcp-sdk@1.0.0`; the earlier bootstrap version is
      not the v1 release.
- [x] RubyGems package: `lcp-sdk@1.0.0`; its OIDC publisher was activated by
      the successful v1 release.
- [x] Reference image: `ghcr.io/spearsystems/lcp-reference-platform:v1.0.0`,
      consumed by digest after verification.

See [RELEASE.md](RELEASE.md) for the verification commands and authoritative
registry coordinate table.

## 4. Approval and governance notes

- [x] The protected release environments were reviewed and approved.
- [x] A single-maintainer approval exception was used for v1.0.0 because a
      second independent reviewer was not yet available.
- [ ] Add a second maintainer/reviewer and enable **Prevent self-review** before
      the next production release.
- [x] No release secret, credential, or real consumer PII was added to the
      repository or release evidence.

The single-maintainer exception does not change the artifact or tag history;
it is recorded here so the v1 release is auditable rather than implying that a
two-person approval occurred.

## 5. Post-release operations

- [x] Weekly post-release verification is configured through
      [`release-verify.yml`](../.github/workflows/release-verify.yml).
- [x] Future package versions must use new immutable SemVer tags; `v1.0.0` is
      never moved or reused.
- [x] Registry trusted publishing is the normal path for future releases;
      bootstrap credentials were revoked where applicable.
- [ ] Record future release-probe results and any adoption feedback in the
      next version's release record.

## 6. Using this record for a future release

For `v1.0.1`, `v1.1.0`, or another future version:

1. Copy this file and replace every version-specific coordinate and link.
2. Run the non-publishing release rehearsal and verify its evidence offline.
3. Confirm the candidate SHA, package availability, security findings, and
   approvals before creating the new signed tag.
4. Do not republish an existing package version or move an existing tag.
5. Record the final workflow, registry, image digest, and reviewer evidence in
   the copied record.

---

**Previous:** [Release guide](RELEASE.md) · **Next:** [Implementation decisions](IMPLEMENTATION-DECISIONS.md)

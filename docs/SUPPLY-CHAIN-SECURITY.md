# LCP Supply-Chain Security
> **Supply-chain page · Page 5 of 6**

This repository exchanges consumer PII, so dependency and build integrity are
part of the security boundary. The repository's automated checks are defense
in depth; they are not a substitute for an operator's vulnerability-management
program or an independent assessment.

## Automated checks

`.github/workflows/security.yml` runs on pushes to `main`, pull requests, and
weekly on a schedule. `.github/workflows/test.yml` runs the normal test suite
against a fresh Postgres service, and the `v*` release workflow repeats the
Postgres test plus a release security gate before package publishing. The
security workflow provides:

- **Dependency review** for pull-request dependency changes.
- **pip-audit** against the SDK, MCP adapter, and production reference-platform
  dependency environments. The local implementation distributions themselves
  are intentionally skipped because they are not PyPI-published artifacts;
  their installed and transitive dependencies are still audited.
- **CycloneDX SBOMs** for the repository source tree and reference container.
- **Pinned minimal runtime** built from the official Python Alpine manifest
  digest, with a non-root application user and Kubernetes hardening defaults.
- **Trivy** scanning of the built reference image's OS packages for HIGH and
  CRITICAL issues. Fixed findings fail the CI gate; upstream findings without a
  fix do not block a build that cannot remediate them and must be reviewed in a
  full operator scan without the CI filter. Python libraries are covered
  separately by pip-audit, avoiding false positives from inherited base-image
  SBOM metadata for removed
  build-time packages. Each image job also uploads a 90-day, non-gating full
  Trivy SARIF report covering OS and library findings, including unfixed issues,
  for exception-register review. The Trivy action is pinned to the signed
  v0.36.0 commit rather than a mutable version tag. The current reviewed
  findings, dispositions, owners, and expiry dates are recorded in the
  [vulnerability exception register](VULNERABILITY-EXCEPTIONS.md).
- **CodeQL** static analysis for Python.
- **Hermetic live admission assurance** in the Test workflow. The Kyverno
  admission job uses a pinned local OCI registry and generated scratch images,
  signing keys, and attestation predicates. It does not use public Kyverno
  test-image artifacts for the live webhook check, and it fails closed on
  registry, cluster, or webhook errors.

`.github/dependabot.yml` opens weekly update pull requests for Python and
GitHub Actions dependencies. Update pull requests must pass the normal tests,
security checks, and package builds before merging.

`.github/workflows/container-release.yml` publishes the reference image for
version tags, emits BuildKit and GitHub SLSA provenance plus a CycloneDX SBOM
attestation, signs the immutable image digest with Sigstore keyless signing,
and verifies the signature, provenance, and SBOM attestations before the job
succeeds. See the [container verification
guide](CONTAINER-SUPPLY-CHAIN.md) and the Kyverno example for deployment-side
enforcement.

## Vulnerability report review

The full Trivy report is deliberately non-gating so that unfixed upstream
findings remain visible rather than being hidden by the release gate. Review the
SARIF artifact for every scheduled and release run, compare it with the
[vulnerability exception register](VULNERABILITY-EXCEPTIONS.md), and retain the
image digest, scanner version, vulnerability database timestamp, SBOMs, and
review decision with the release record. A finding may only be accepted with a
named owner, a written rationale, and an expiry-based next review; a local
exception never automatically applies to a downstream deployment.

## SBOM use

SBOMs are generated as CI artifacts rather than committed to the repository.
For a release, retain the source and container SBOM artifacts with the release
record and compare them against the previous release. Operators should also
create an SBOM for any rebuilt image, because the final image can differ from
the repository reference image through base-image and build-environment
changes.

## Release requirements

Before publishing a package or container:

1. Run the conformance vectors and SDK/reference-platform tests.
2. Run the real Postgres integration test against an isolated disposable
   database.
3. Build all Python wheels and inspect their metadata.
4. Generate and retain the source and container SBOMs.
5. Resolve or formally accept every HIGH/CRITICAL dependency and image
   finding. CI blocks actionable fixed findings; operators must track upstream
   findings without a fix in the vulnerability exception register.
6. Pin the deployment image by digest and verify its provenance/signature
   before admission. For Kubernetes, enforce the approved signer and SLSA
   predicate with the Kyverno example.
7. Confirm that no secrets, real PII, credentials, or private configuration are
   present in the source, SBOM, image, or release artifacts.

The tag-triggered package and container workflows enforce the dependency
audit, create release SBOM artifacts, scan the reference image, upload a full
non-gating Trivy report for release review, and make package publication wait
for the release security gate. The separate signed tagged release workflow
waits for all required tag workflows, then creates a GitHub release containing
Sigstore-signed release notes, a package/container manifest, the source SBOM,
and per-package tagged source archives, CycloneDX SBOMs, and SLSA provenance
statements with signatures. Keep the source, container, full Trivy, and signed
release reports with the release record even though CI stores them as artifacts
or release assets rather than commits. The workflow also supports a
non-publishing dry run which checks registry version availability and verifies
all release signatures before a real tag.

The release workflow additionally runs the offline evidence verifier
(`tools/verify_release_evidence.py`) on its own generated bundle before
uploading, so manifest/file/digest inconsistencies fail the job even when every
signature is valid. Maintainers and adopters can run the same tool against a
downloaded dry-run artifact or release asset directory without network access:

```bash
python3 tools/verify_release_evidence.py ./release-assets
```

Add `--identity`/`--issuer` to also verify the Sigstore bundles with Cosign.
See [RELEASE.md](RELEASE.md) for adopter and maintainer verification commands.

The PyPI workflow uses Trusted Publishing and does not store a long-lived PyPI
API token in the repository. Package ownership, PyPI Trusted Publisher
configuration, GitHub branch protection, release-tag restrictions, and
protected environment approvals remain operator/maintainer responsibilities.
Follow the [maintainer release setup](MAINTAINER-RELEASE-SETUP.md) before
creating a real version tag. The final GitHub release-record job now has
`contents: write` only inside the protected `release` environment; dry runs
remain non-publishing.

## Operator responsibilities

A self-hosted deployment should additionally:

- Mirror or approve dependencies through the organization's package policy.
- Scan the base image and final image in the deployment registry.
- Use signed images and admission policy where available.
- Maintain a vulnerability exception register with expiry dates and owners.
- Rebuild promptly for critical cryptography, HTTP, database, or OS findings;
  do not treat a nonblocking CI result as a security exception or certification.
- Preserve release SBOMs and provenance records for the retention period.
- Re-run scans after base-image, Python-runtime, or dependency changes.

---

**Previous:** [Security profiles](SECURITY-PROFILES.md) · **Next:** [Maintainer release setup](MAINTAINER-RELEASE-SETUP.md)

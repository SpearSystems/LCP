# LCP Supply-Chain Security

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
- **Trivy** scanning of the built reference image for HIGH and CRITICAL issues,
  including unfixed findings. The Trivy action is pinned to the signed v0.36.0
  commit rather than a mutable version tag.
- **CodeQL** static analysis for Python.

`.github/dependabot.yml` opens weekly update pull requests for Python and
GitHub Actions dependencies. Update pull requests must pass the normal tests,
security checks, and package builds before merging.

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
5. Resolve or formally accept every HIGH/CRITICAL dependency and image finding.
6. Pin the deployment image by digest and verify its provenance/signature where
   the registry supports it.
7. Confirm that no secrets, real PII, credentials, or private configuration are
   present in the source, SBOM, image, or release artifacts.

The tag-triggered release workflow enforces the dependency audit, creates
release SBOM artifacts, scans the reference image, and makes the package-build
jobs wait for that gate. Keep the source and container SBOMs with the release
record even though CI stores them as artifacts rather than commits.

The PyPI workflow uses Trusted Publishing and does not store a long-lived PyPI
API token in the repository. Package ownership, PyPI Trusted Publisher
configuration, GitHub branch protection, and environment approvals remain
operator/maintainer responsibilities.

## Operator responsibilities

A self-hosted deployment should additionally:

- Mirror or approve dependencies through the organization's package policy.
- Scan the base image and final image in the deployment registry.
- Use signed images and admission policy where available.
- Maintain a vulnerability exception register with expiry dates and owners.
- Rebuild promptly for critical cryptography, HTTP, database, or OS findings.
- Preserve release SBOMs and provenance records for the retention period.
- Re-run scans after base-image, Python-runtime, or dependency changes.

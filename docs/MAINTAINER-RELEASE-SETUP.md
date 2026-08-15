# GitHub branch protection and release-environment setup

> **Maintainer page · Page 5A of 6**
>
> This page is the repository-side setup checklist for maintainers operating LCP
> in an organization that handles regulated or high-scrutiny production data.
> GitHub settings are outside the repository, so they must be applied by a
> repository or organization administrator and recorded as infrastructure
> configuration.

## Security boundary

The repository workflows are deliberately fail-closed, but a workflow file
cannot create GitHub branch rules, environment reviewers, package ownership, or
organization identity policy. Apply the controls below before allowing a real
`v<SDK_VERSION>` tag to publish artifacts.

The release workflow has two distinct paths:

- **Manual dry run:** validates metadata and registry availability, generates
  and verifies signed evidence, and uploads a temporary workflow artifact. It
  does not publish packages, create a GitHub release, or use the protected
  release-record job.
- **Versioned tag:** waits for the required test, security, SDK, package, and
  container workflows. Package publication and the final GitHub release-record
  job use the protected `release` environment.

## Protect `main`

In **Settings → Rules → Rulesets** (or the equivalent branch-protection
settings), create a ruleset targeting `main` with these minimum controls:

- Require a pull request before merging.
- Require at least two approvals for regulated deployments; use a separate
  CODEOWNERS/security approval for changes under `.github/workflows/`,
  `schemas/`, `verticals/`, `implementations/reference-platform/`, and
  `governance/`.
- Dismiss stale approvals when new commits are pushed.
- Require all conversations to be resolved.
- Require the branch to be up to date before merging, or use a merge queue that
  provides the same guarantee.
- Restrict who can push directly to `main` and who can create or delete release
  tags.
- Block force pushes and branch deletion.
- Require signed commits if the organization's identity and signing-key
  lifecycle can support it. Do not enable this halfway through a release; make
  the first enforcement commit part of a planned migration.
- Do not allow ordinary administrators to bypass the ruleset. Keep any break-
  glass bypass list short, named, time-bound, and audited.

### Required pull-request checks

After the first successful pull request, select the exact check names shown by
GitHub. The current workflow job names are:

- `Conformance, SDK, platform, and Postgres integration`
- `Supply-chain tooling and Kyverno fixtures`
- `Ephemeral Kyverno admission enforcement`
- `Dependency review`
- `Python dependency audit`
- `Generate source SBOM`
- `Build and scan reference image`
- `CodeQL`
- `Canonical schemas and generated models`
- `Python SDK`
- `TypeScript SDK`
- `Go SDK`
- `C# SDK`
- `Java SDK`
- `PHP SDK`
- `Rust SDK`
- `Ruby SDK`
- `Kotlin SDK`
- `Swift SDK`

Do not require tag-only publication checks as pull-request checks unless the
workflow is also configured to run for pull requests. Reconfirm required-check
names after renaming a workflow job; GitHub treats the check name as an API
identity.

## Create the protected `release` environment

In **Settings → Environments**, create an environment named exactly `release`:

1. Require at least two reviewers from independent release/security roles.
2. Prevent the person who initiated the deployment from approving their own
   deployment.
3. Restrict deployment branches and tags to the protected release pattern
   `v*`. Prefer a repository ruleset that permits only authorized maintainers
   to create signed, annotated Semver tags.
4. Do not add general-purpose secrets to the environment. Add only the
   registry credentials and signing material required by the workflows below,
   and prefer OIDC or short-lived credentials wherever the registry supports
   it.
5. Review the environment's deployment history as part of every release record.

The current workflows consume the following environment secrets or trusted
identities:

| Target | Configuration | Recommended control |
|---|---|---|
| PyPI | Trusted Publishers for `lcp-sdk`, `lcp-mcp-server`, and `lcp-reference-platform` | Bind each project to `SpearSystems/LCP`, `python-release.yml`, environment `release`; do not add a PyPI API token. |
| npm | Trusted publisher for `@spearsystems/lcp-sdk` | Bind the exact repository and `sdk-release.yml` workflow; retain provenance. |
| NuGet | `NUGET_USER` consumed by `NuGet/login` | Use NuGet trusted login and a protected environment; do not store a reusable API key. |
| Maven Central | `MAVEN_CENTRAL_USERNAME`, `MAVEN_CENTRAL_PASSWORD`, `MAVEN_GPG_PRIVATE_KEY`, `MAVEN_GPG_PASSPHRASE` | Store only as environment secrets; rotate and audit the signing key and Central Portal token. |
| Kotlin Maven | `MAVEN_REPOSITORY_URL`, `MAVEN_CENTRAL_USERNAME`, `MAVEN_CENTRAL_PASSWORD` | Use the distinct `com.spearsystems:lcp-sdk-kotlin` coordinate and protected repository. |
| crates.io | OIDC trusted publisher | Bind the exact `sdk-release.yml` workflow and repository. Do not use `CARGO_REGISTRY_TOKEN`. |
| RubyGems | OIDC trusted publisher | Bind the exact `sdk-release.yml` workflow and repository. |
| GHCR | `GITHUB_TOKEN` with job-scoped package permission | Keep the image package private or access-controlled until its release policy is reviewed; publish by digest and verify before deployment. |

The workflow file is the source of the requested identity paths, but the
registry's trusted-publisher configuration is the enforcement point. A valid
OIDC token from another repository or workflow must not be accepted.

## GitHub Actions and OIDC settings

Confirm that the organization allows GitHub Actions for this repository and
that the workflows may request the permissions they declare:

- `id-token: write` only for jobs that sign, publish, or attest.
- `packages: write` only for the GHCR publication job.
- `attestations: write` and `artifact-metadata: write` only for the container
  attestation job.
- `contents: write` only for the protected final GitHub release-record job.
- `actions: read` for release workflow run gating and artifact download.

Keep fork pull requests on read-only permissions. Do not grant write
permissions to `pull_request` workflows that process untrusted code.

Enable Dependabot or an equivalent controlled update process, require the
normal security and SDK checks on dependency updates, and review changes to
third-party action SHAs as supply-chain changes rather than ordinary version
bumps.

## Release procedure

1. Confirm `main` is green and the working tree contains no unreviewed changes.
2. Update `SDK_VERSION` and package metadata together.
3. Run the local metadata, schema, conformance, and language compatibility
   checks documented in [`RELEASE.md`](RELEASE.md).
4. Run the non-publishing release dry run against the exact candidate SHA:

   ```bash
   gh workflow run release.yml \
     --repo SpearSystems/LCP \
     --ref main \
     -f tag=v0.1.0 \
     -f target_sha="$(git rev-parse HEAD)"
   ```

5. Download the dry-run artifact and verify the manifest, package evidence,
   SBOMs, and Sigstore bundles before approving a release ticket.
6. Create and push the exact signed tag only after the dry run and approvals
   pass:

   ```bash
   git tag -s v0.1.0 -m "LCP v0.1.0"
   git push origin v0.1.0
   ```

7. Confirm all tag workflows pass. Approve only the package publication jobs
   and the final `create-release` job in the `release` environment after
   reviewing the commit, scan artifacts, image digest, and package coordinates.
8. Verify the published release with [`RELEASE.md`](RELEASE.md), then record
   the signed manifest, source/container SBOMs, provenance, scanner reports,
   reviewer identities, approvals, and deployment digest in the release record.

## Break-glass and recovery

If a release is suspected to be compromised:

1. Pause the `release` environment and revoke its pending approvals.
2. Disable or quarantine the affected package version and container digest;
   never overwrite an immutable package or reuse a digest.
3. Revoke the affected OIDC trusted-publisher binding or signing credential if
   compromise is possible.
4. Preserve the signed release manifest, workflow run, artifact attestations,
   approvals, and audit logs.
5. Publish a corrective patch only after security review and re-run the full
   dry-run and release gates.

Review branch rules, environment reviewers, trusted-publisher bindings, and
signing credentials at least quarterly and after any maintainer, repository,
workflow-path, or organization ownership change.

---

**Previous:** [Supply-chain security](SUPPLY-CHAIN-SECURITY.md) · **Next:** [Container signing and provenance](CONTAINER-SUPPLY-CHAIN.md)

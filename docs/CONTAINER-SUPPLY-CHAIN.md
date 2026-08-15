# Container Signing and Provenance Verification

The reference platform's container release workflow publishes a digest-addressed
image, creates GitHub SLSA build-provenance and CycloneDX SBOM attestations,
signs the image with Sigstore keyless signing, and verifies all of them before
the workflow succeeds.

The workflow is intentionally separate from the package release workflow:

- `.github/workflows/container-release.yml` runs for pushes to `main`, `v*`
  tags, and manual `workflow_dispatch` runs. Main-branch builds receive an
  immutable `sha-<commit>` tag; release tags retain their version tag.
- `.github/workflows/security.yml` builds the image on normal pushes and pull
  requests, runs the blocking OS-package scan, retains the full Trivy report,
  and smoke-tests the runtime imports.
- The workflow publishes to
  `ghcr.io/<github-owner>/lcp-reference-platform:<sha-or-version-tag>` and
  records the image digest in the build output. Do not deploy a mutable tag
  without resolving and recording its digest. Main-branch images are for CI
  verification; the Kyverno example intentionally trusts only version-tag
  release identities.

## CI release controls

The container release job requires these GitHub permissions:

- `packages: write` to publish to GHCR.
- `id-token: write` for Sigstore keyless signing and attestation certificates.
- `attestations: write` and `artifact-metadata: write` for GitHub's provenance
  service and linked artifact metadata.
- `contents: read` for the source checkout.

The job disables BuildKit's registry attestation exporter for the GHCR push,
generates a CycloneDX SBOM for the pushed digest, and calls `actions/attest`
once for SLSA
provenance and once for the SBOM. It signs the same digest with Cosign and
verifies the signature's workflow identity and OIDC issuer. Later verification
steps use the GitHub CLI to verify both the build and CycloneDX attestations
against the repository.

GitHub Artifact Attestations are subject to GitHub plan and repository
visibility requirements. If a deployment cannot use GitHub's attestation
service, use the equivalent organization-controlled Sigstore/KMS process and
keep the same requirements: sign the immutable image digest, publish provenance
and SBOM attestations, and verify them before admission.

## Verify a published image manually

Set the immutable image reference and the expected GitHub Actions workflow
identity. The identity must be changed if the workflow is renamed or moved.

```bash
export IMAGE='ghcr.io/spearsystems/lcp-reference-platform@sha256:<published-digest>'
export WORKFLOW_IDENTITY='https://github.com/SpearSystems/LCP/.github/workflows/container-release.yml@refs/tags/v<version>'

cosign verify "$IMAGE" \
  --certificate-identity "$WORKFLOW_IDENTITY" \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'

# GitHub's attestation verifier checks the repository binding and provenance.
gh attestation verify \
  "oci://${IMAGE}" \
  --repo SpearSystems/LCP

# The SBOM is a separate CycloneDX attestation on the same image digest.
gh attestation verify \
  "oci://${IMAGE}" \
  --predicate-type 'https://cyclonedx.org/bom' \
  --repo SpearSystems/LCP
```

For manual-dispatch builds, use the workflow identity for the branch that was
run, for example `@refs/heads/main`, and do not accept a manual build into a
regulated environment unless the branch and approval controls are appropriate.

A keyless signature is recorded in the Sigstore transparency log. The identity
and issuer constraints are part of verification; checking only that *some*
signature exists is insufficient.

## Kubernetes admission enforcement

[`implementations/reference-platform/kubernetes/verify-images-kyverno.example.yaml`](../implementations/reference-platform/kubernetes/verify-images-kyverno.example.yaml)
is a Kyverno example that requires all three controls:

1. A valid Cosign keyless signature from the container release workflow.
2. An SLSA provenance attestation with the same expected workflow identity.
3. A signed CycloneDX SBOM attestation with the same expected workflow identity.

The three checks are separate Kyverno `verifyImages` rules because Kyverno
requires a rule to verify either image signatures or image attestations, not
both at once.

Before applying it:

1. Replace `ghcr.io/REPLACE_ME/lcp-reference-platform*` with the exact
   registry path used by the organization.
2. Replace the example workflow owner/repository/branch or tag identity if the
   release workflow is forked or renamed.
3. Configure Kyverno registry credentials when the image is private.
4. Confirm Kyverno can reach the registry and Sigstore transparency log, or
   configure an approved internal mirror and trust root.
5. Start with `validationFailureAction: Audit` in a non-production cluster,
   inspect admission reports, then change to `Enforce` after the image path and
   identities are verified.
6. Test both API and worker Deployments. The policy matches Pods and Kyverno
   should be configured to generate equivalent checks for the workload
   controllers used by the cluster.

The policy mutates tags to digests and requires immutable digests after
verification. This prevents a signed tag from being moved after admission.
For maintainer-side branch rules, environment approvals, and trusted registry
publisher setup, see [GitHub branch protection and release-environment setup](MAINTAINER-RELEASE-SETUP.md).

CI also runs a real ephemeral admission check in the `Test` workflow. It uses a
pinned kind node image and kind binary, starts a pinned local OCI Distribution
registry, builds disposable scratch images, creates ephemeral test signing
keys, installs a SHA-256-verified Kyverno release manifest, and applies
key-backed fixture policies in `Enforce` mode. It submits server-side dry-run
Pods using only the local registry, so the live test does not depend on
Kyverno's public test-image registry. The job must observe Kyverno rejecting
unsigned, wrong-workflow, missing-provenance, and missing-SBOM images; a generic
webhook or cluster failure does not count as a passing rejection. The reviewed
upstream-image CLI fixture remains separate and is used only to validate the
production-style keyless policy syntax.

The local registry uses HTTP and Kyverno's `allowInsecureRegistry` test flag
only inside the disposable cluster. This is intentionally not a production
recommendation; production deployments must use TLS or an approved private
registry trust configuration. The fixture details are documented in
[`kubernetes/tests/verify-images/README.md`](../implementations/reference-platform/kubernetes/tests/verify-images/README.md).

```bash
kubectl apply --dry-run=server -f \
  implementations/reference-platform/kubernetes/verify-images-kyverno.example.yaml
```

Use the cluster's policy engine and version documentation as the authority for
final syntax. Admission verification is not a replacement for registry ACLs,
network policy, runtime isolation, vulnerability response, or image rebuilds.

## Cloud-neutral alternatives

The same control can be implemented with another admission system or registry:

- **Cosign + OPA/Gatekeeper:** verify the signature certificate identity and
  issuer, then require a digest and approved provenance predicate.
- **Notary Project / registry-native signing:** use an organization-managed
  signing key or KMS key, publish the provenance and SBOM as OCI referrers, and
  enforce the key identity at admission.
- **Managed Kubernetes policy:** configure the provider's image-signature
  admission integration to require the exact image repository, digest, signer,
  and provenance predicate.

In every case, bind the policy to the actual release workflow or signing key,
not merely to the image repository. Rotate keys or workflow identities through
a reviewed policy change, and keep old identities only for the documented
migration window.

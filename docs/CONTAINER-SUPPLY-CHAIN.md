# Container Signing and Provenance Verification

The reference platform's container release workflow publishes a digest-addressed
image, creates a GitHub SLSA build-provenance attestation, signs the image with
Sigstore keyless signing, and verifies both before the workflow succeeds.

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

The job uses BuildKit `provenance: mode=max` and `sbom: true`, then calls
`actions/attest` with the pushed image digest. It signs the same digest with
Cosign and verifies the signature's workflow identity and OIDC issuer. A later
verification step uses the GitHub CLI to verify the build attestation against
the repository.

GitHub Artifact Attestations are subject to GitHub plan and repository
visibility requirements. If a deployment cannot use GitHub's attestation
service, use the equivalent organization-controlled Sigstore/KMS process and
keep the same requirements: sign the immutable image digest, publish provenance
and SBOM attestations, and verify them before admission.

## Verify a published image manually

Set the immutable image reference and the expected GitHub Actions workflow
identity. The identity must be changed if the workflow is renamed or moved.

```bash
export IMAGE='ghcr.io/SpearSystems/lcp-reference-platform@sha256:<published-digest>'
export WORKFLOW_IDENTITY='https://github.com/SpearSystems/LCP/.github/workflows/container-release.yml@refs/tags/v<version>'

cosign verify "$IMAGE" \
  --certificate-identity "$WORKFLOW_IDENTITY" \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'

# GitHub's attestation verifier checks the repository binding and provenance.
gh attestation verify \
  "oci://${IMAGE}" \
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
is a Kyverno example that requires both:

1. A valid Cosign keyless signature from the container release workflow.
2. An SLSA provenance attestation with the same expected workflow identity.

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

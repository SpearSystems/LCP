# Kubernetes deployment example

These manifests demonstrate a cloud-neutral Kubernetes shape for the LCP
reference platform. They are intentionally not a complete turnkey production
cluster configuration.

## Before applying

1. Build and publish the reference-platform image to your registry.
2. Provide a supported Postgres service; do not run the database in this
   example for production.
3. Copy `secret.example.yaml` to a private, access-controlled Secret manifest
   or use External Secrets/KMS integration.
4. Replace the example image, database URL, tenant ID, resource limits,
   namespaces, network policies, ingress, and residency controls.
5. Configure a production WSGI server or adapt the image entry point to the
   organization's process manager.
6. Publish the image through the signed container workflow, resolve its digest,
   and replace the example image reference with that digest.
7. Add a TLS ingress/WAF and private egress policy.

## Apply

```bash
kubectl create namespace lcp
kubectl apply -n lcp -f secret.example.yaml   # replace with a real secret workflow
kubectl apply -n lcp -f configmap.yaml
kubectl apply -n lcp -f deployment.yaml
kubectl apply -n lcp -f service.yaml
kubectl apply -n lcp -f pod-disruption-budget.yaml
# Optional; tune against queue age and database capacity before applying.
kubectl apply -n lcp -f hpa.example.yaml
```

The API and worker are separate deployments. Both use the same image and
configuration; only the entry point differs. The worker needs access to the
same Postgres database but does not need a public Service.

## Production additions

- `network-policy.example.yaml` restricting database and buyer egress.
- `pod-disruption-budget.yaml` and multi-zone scheduling.
- `hpa.example.yaml`, extended with queue-age and database-capacity signals.
- External Secrets/KMS and key rotation, including controlled re-encryption of persisted envelopes.
- Ingress TLS, WAF, DDoS protection, and request-size limits.
- Postgres HA, backups, encryption, and restore drills.
- Centralized redacted logs, metrics, traces, and alerts.
- Image signing, admission policy, SBOM verification, and vulnerability scans.
- `verify-images-kyverno.example.yaml` — optional Kyverno enforcement for
  keyless signatures, SLSA provenance, and CycloneDX SBOM attestations.
- `tests/verify-images/` — CI fixtures proving unsigned, wrong-workflow,
  missing-provenance, and missing-SBOM images are rejected.

See [container signing and provenance verification](../../../docs/CONTAINER-SUPPLY-CHAIN.md)
for Cosign/GitHub verification commands and cloud-neutral admission guidance.

## Policy test fixtures

The `tests/verify-images/` fixture uses Kyverno's public verification-test
images and expects rejection for four negative cases:

- an unsigned image;
- an image signed by a different workflow/identity;
- an image without the required SLSA provenance attestation; and
- an image without the required CycloneDX SBOM attestation.

Run it with the Kyverno CLI and registry access:

```bash
kyverno test implementations/reference-platform/kubernetes/tests/verify-images \
  --registry --detailed-results
```

The GitHub test workflow installs Kyverno CLI `v1.18.2`, verifies its pinned
Linux archive with the release's Sigstore bundle, and then runs this fixture.
Keep that version and verification identity under review when upgrading the
policy engine or its CLI.

The fixture is a policy-behavior test, not a substitute for a live admission
test against the organization's registry, Kyverno version, credentials, and
network policy.

The CI `admission` job also creates a disposable, pinned kind cluster, installs
Kyverno from a digest-checked release manifest, applies these policies in
`Enforce` mode, and submits the four fixtures through the Kubernetes API server
with server-side dry runs. It fails unless Kyverno rejects the unsigned,
wrong-workflow, missing-provenance, and missing-SBOM cases through the live
admission webhook.

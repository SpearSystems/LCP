# Kyverno image-verification fixtures

This directory contains two related test surfaces:

- `kyverno-test.yaml` is the reviewed Kyverno CLI fixture. It exercises the
  production-style keyless and GitHub Artifact Attestation policy shapes using
  Kyverno's upstream test images. It is useful for checking policy syntax and
  compatibility with the Kyverno CLI.
- `tools/test_ephemeral_kyverno_admission.sh` is the live admission test. It
  does **not** use the upstream test-image registry. The script starts a
  pinned, disposable OCI Distribution registry, builds a local `scratch`
  image, pushes four local tags, creates short-lived test signing keys, and
  generates temporary key-backed signature/attestation policies.

The live fixture proves that the admission webhook rejects:

1. an unsigned image;
2. an image signed by an untrusted fixture key (the wrong workflow case);
3. an image missing the required SLSA provenance attestation; and
4. an image missing the required CycloneDX SBOM attestation.

The fixture uses an HTTP registry and sets Kyverno's
`allowInsecureRegistry` ConfigMap key only inside an ephemeral kind cluster.
This is deliberately not a production setting. The production example remains
keyless, digest-enforcing, and transparency-log aware in
[`verify-images-kyverno.example.yaml`](../../verify-images-kyverno.example.yaml).

Run the live test when Docker, kind, kubectl, and cosign are available:

```bash
bash tools/test_ephemeral_kyverno_admission.sh
```

Cosign v3 enables signing-config by default, which rejects
`--tlog-upload=false`. The fixture therefore passes
`--use-signing-config=false` alongside `--tlog-upload=false` so the
disposable local signatures keep the proven v2 semantics (local key, no
transparency-log upload, HTTP registry allowed).

The pinned kind node and registry images are infrastructure dependencies, not
application test fixtures. CI still verifies their declared versions and
cleans up the cluster, registry, generated keys, and local images on exit.

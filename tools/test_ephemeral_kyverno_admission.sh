#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${KYVERNO_KIND_CLUSTER_NAME:-lcp-kyverno-admission}"
KYVERNO_VERSION="${KYVERNO_VERSION:-v1.18.2}"
KYVERNO_INSTALL_SHA256="${KYVERNO_INSTALL_SHA256:-3dcd43eaf11f0719084217148cd0c82a8fa49faa9b1a783ea5bea2cf84041bda}"
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.35.5@sha256:ce977ae6d65918d0b58a5f8b5e940429c2ce42fa3a5619ec2bbc60b949c0ac95}"
WORK_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/lcp-kyverno-admission"
CONTEXT="kind-${CLUSTER_NAME}"

mkdir -p "${WORK_DIR}"

cleanup() {
  kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

command -v kind >/dev/null
command -v kubectl >/dev/null
command -v curl >/dev/null

kind create cluster \
  --name "${CLUSTER_NAME}" \
  --image "${KIND_NODE_IMAGE}" \
  --wait 180s

kubectl --context "${CONTEXT}" cluster-info

install_manifest="${WORK_DIR}/kyverno-install.yaml"
base_url="https://github.com/kyverno/kyverno/releases/download/${KYVERNO_VERSION}"
curl --fail --silent --show-error --location --retry 3 \
  "${base_url}/install.yaml" --output "${install_manifest}"
echo "${KYVERNO_INSTALL_SHA256}  ${install_manifest}" | sha256sum --check --strict

kubectl --context "${CONTEXT}" apply --server-side -f "${install_manifest}"
kubectl --context "${CONTEXT}" wait --for=condition=Established \
  crd/clusterpolicies.kyverno.io --timeout=180s
kubectl --context "${CONTEXT}" -n kyverno rollout status \
  deployment/kyverno-admission-controller --timeout=180s

policy_dir="${ROOT_DIR}/implementations/reference-platform/kubernetes/tests/verify-images"
for policy in signature-policy.yaml provenance-policy.yaml sbom-policy.yaml; do
  kubectl --context "${CONTEXT}" apply -f "${policy_dir}/${policy}"
done

for policy_name in lcp-fixture-signature-policy lcp-fixture-provenance-policy lcp-fixture-sbom-policy; do
  ready=""
  for _ in $(seq 1 90); do
    ready="$(kubectl --context "${CONTEXT}" get clusterpolicy "${policy_name}" \
      -o jsonpath='{.status.ready}' 2>/dev/null || true)"
    if [[ "${ready}" == "true" ]]; then
      break
    fi
    sleep 2
  done
  if [[ "${ready}" != "true" ]]; then
    kubectl --context "${CONTEXT}" get clusterpolicy "${policy_name}" -o yaml
    echo "Kyverno policy ${policy_name} did not become ready" >&2
    exit 1
  fi
done

kubectl --context "${CONTEXT}" get clusterpolicy

assert_rejected() {
  local pod_name="$1"
  local image="$2"
  local output_file="${WORK_DIR}/${pod_name}.output"

  set +e
  kubectl --context "${CONTEXT}" create --dry-run=server -f - >"${output_file}" 2>&1 <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
spec:
  containers:
    - name: app
      image: ${image}
EOF
  local status=$?
  set -e

  if [[ "${status}" -eq 0 ]]; then
    cat "${output_file}"
    echo "Expected ${pod_name} (${image}) to be rejected" >&2
    exit 1
  fi

  if ! grep -Eiq 'denied|rejected|failed to verify|verification failed' "${output_file}"; then
    cat "${output_file}"
    echo "${pod_name} failed for a reason other than Kyverno image verification" >&2
    exit 1
  fi

  echo "Admission correctly rejected ${pod_name} (${image})"
  cat "${output_file}"
}

# These four cases exercise unsigned images, the wrong signing workflow, and
# missing/untrusted provenance and SBOM attestations through the live webhook.
assert_rejected unsigned-image ghcr.io/kyverno/test-verify-image:unsigned
assert_rejected wrong-workflow-image ghcr.io/kyverno/test-verify-image:signed-keyless
assert_rejected missing-provenance-image ghcr.io/kyverno/test-verify-image:signed-by-someone-else
assert_rejected missing-sbom-image ghcr.io/kyverno/test-verify-image:signed-by-someone-else

echo "Ephemeral Kyverno admission rejection test passed"

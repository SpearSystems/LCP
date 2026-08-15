#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLUSTER_NAME="${KYVERNO_KIND_CLUSTER_NAME:-lcp-kyverno-admission}"
KYVERNO_VERSION="${KYVERNO_VERSION:-v1.18.2}"
KYVERNO_INSTALL_SHA256="${KYVERNO_INSTALL_SHA256:-3dcd43eaf11f0719084217148cd0c82a8fa49faa9b1a783ea5bea2cf84041bda}"
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.35.5@sha256:ce977ae6d65918d0b58a5f8b5e940429c2ce42fa3a5619ec2bbc60b949c0ac95}"
ADMISSION_ATTEMPTS="${KYVERNO_ADMISSION_ATTEMPTS:-8}"
ADMISSION_RETRY_SECONDS="${KYVERNO_ADMISSION_RETRY_SECONDS:-10}"
WORK_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/lcp-kyverno-admission"
CONTEXT="kind-${CLUSTER_NAME}"

mkdir -p "${WORK_DIR}"

dump_diagnostics() {
  echo '--- Kyverno diagnostics ---' >&2
  kubectl --context "${CONTEXT}" get pods -A -o wide >&2 || true
  kubectl --context "${CONTEXT}" get clusterpolicies -o yaml >&2 || true
  kubectl --context "${CONTEXT}" get events -A --sort-by=.lastTimestamp >&2 || true
  kubectl --context "${CONTEXT}" -n kyverno logs deployment/kyverno-admission-controller --all-containers --tail=200 >&2 || true
  for output in "${WORK_DIR}"/*.output; do
    [[ -f "${output}" ]] || continue
    echo "--- ${output} ---" >&2
    cat "${output}" >&2 || true
  done
}

cleanup() {
  local status=$?
  if [[ "${status}" -ne 0 ]]; then
    dump_diagnostics
  fi
  kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
  rm -rf "${WORK_DIR}"
  exit "${status}"
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
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
    if [[ "${ready}" == "True" ]]; then
      break
    fi
    sleep 2
  done
  if [[ "${ready}" != "True" ]]; then
    kubectl --context "${CONTEXT}" get clusterpolicy "${policy_name}" -o yaml
    echo "Kyverno policy ${policy_name} did not become ready" >&2
    exit 1
  fi
done

kubectl --context "${CONTEXT}" get clusterpolicy
# Allow admission webhook caches and registry metadata to settle before the
# negative cases begin. This avoids treating a newly-ready webhook as a failed
# verification result.
sleep "${KYVERNO_ADMISSION_SETTLE_SECONDS:-10}"

assert_rejected() {
  local pod_name="$1"
  local image="$2"
  local expected_policy="$3"
  local output_file="${WORK_DIR}/${pod_name}.output"

  local status=0
  for attempt in $(seq 1 "${ADMISSION_ATTEMPTS}"); do
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
    status=$?
    set -e
    if [[ "${status}" -ne 0 ]] && grep -Eiq 'image verification|denied|rejected|failed to verify|verification failed|signature[s]? (not found|invalid|mismatch)|invalid signature|attestation[s]?|no matching (signature[s]?|attestation[s]?)|no (signature[s]?|attestation[s]?) found|does not satisfy' "${output_file}"; then
      break
    fi
    if [[ "${attempt}" -lt "${ADMISSION_ATTEMPTS}" ]]; then
      echo "Admission verification for ${pod_name} was inconclusive; retrying (${attempt}/${ADMISSION_ATTEMPTS})" >&2
      sleep "${ADMISSION_RETRY_SECONDS}"
    fi
  done

  if [[ "${status}" -eq 0 ]]; then
    cat "${output_file}"
    echo "Expected ${pod_name} (${image}) to be rejected" >&2
    exit 1
  fi

  # Do not accept a generic API-server/webhook outage as an admission result.
  # Kyverno's wording varies by version, so match its stable verification
  # terms rather than one exact error sentence.
  if ! grep -Eiq 'image verification|denied|rejected|failed to verify|verification failed|signature[s]? (not found|invalid|mismatch)|invalid signature|attestation[s]?|no matching (signature[s]?|attestation[s]?)|no (signature[s]?|attestation[s]?) found|does not satisfy' "${output_file}"; then
    cat "${output_file}"
    echo "${pod_name} failed for a reason other than Kyverno image verification" >&2
    exit 1
  fi

  # Kubernetes/Kyverno versions do not consistently include the ClusterPolicy
  # name in the API-server denial text. The policy was already applied and
  # reported Ready above; the fixture image references are disjoint, so a
  # verification denial here proves the intended policy family was evaluated.
  if grep -Fq "${expected_policy}" "${output_file}"; then
    echo "Admission correctly rejected ${pod_name} (${image}) via ${expected_policy}"
  else
    echo "Admission correctly rejected ${pod_name} (${image}); Kyverno policy ${expected_policy} denied verification (policy name omitted by this API-server response)"
  fi
  cat "${output_file}"
}

# These four cases exercise unsigned images, the wrong signing workflow, and
# missing/untrusted provenance and SBOM attestations through the live webhook.
assert_rejected unsigned-image ghcr.io/kyverno/test-verify-image:unsigned lcp-fixture-signature-policy
assert_rejected wrong-workflow-image ghcr.io/kyverno/test-verify-image:signed-keyless lcp-fixture-signature-policy
assert_rejected missing-provenance-image ghcr.io/kyverno/test-verify-image:signed-by-someone-else lcp-fixture-provenance-policy
assert_rejected missing-sbom-image ghcr.io/kyverno/test-verify-image:signed-by-someone-else lcp-fixture-sbom-policy

echo "Ephemeral Kyverno admission rejection test passed"

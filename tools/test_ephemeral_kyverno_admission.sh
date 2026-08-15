#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${KYVERNO_KIND_CLUSTER_NAME:-lcp-kyverno-admission}"
KYVERNO_VERSION="${KYVERNO_VERSION:-v1.18.2}"
KYVERNO_INSTALL_SHA256="${KYVERNO_INSTALL_SHA256:-3dcd43eaf11f0719084217148cd0c82a8fa49faa9b1a783ea5bea2cf84041bda}"
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.35.5@sha256:ce977ae6d65918d0b58a5f8b5e940429c2ce42fa3a5619ec2bbc60b949c0ac95}"
REGISTRY_NAME="${KYVERNO_REGISTRY_NAME:-lcp-kyverno-registry}"
REGISTRY_PORT="${KYVERNO_REGISTRY_PORT:-5001}"
REGISTRY_IMAGE="${KYVERNO_REGISTRY_IMAGE:-registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373}"
REGISTRY_PUSH_HOST="${KYVERNO_REGISTRY_PUSH_HOST:-127.0.0.1:${REGISTRY_PORT}}"
REGISTRY_KUBE_HOST="${KYVERNO_REGISTRY_KUBE_HOST:-${REGISTRY_NAME}:5000}"
FIXTURE_REPOSITORY="lcp/kyverno-fixture"
ADMISSION_ATTEMPTS="${KYVERNO_ADMISSION_ATTEMPTS:-8}"
ADMISSION_RETRY_SECONDS="${KYVERNO_ADMISSION_RETRY_SECONDS:-10}"
WORK_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/lcp-kyverno-admission"
CONTEXT="kind-${CLUSTER_NAME}"

mkdir -p "${WORK_DIR}"

# The registry container is an OCI transport fixture only. It is pinned to a
# multi-architecture digest; the test images and signing keys are generated
# inside this disposable run and never come from a public test-image registry.

dump_diagnostics() {
  echo '--- Kyverno diagnostics ---' >&2
  kubectl --context "${CONTEXT}" get pods -A -o wide >&2 || true
  kubectl --context "${CONTEXT}" get clusterpolicies -o yaml >&2 || true
  kubectl --context "${CONTEXT}" get events -A --sort-by=.lastTimestamp >&2 || true
  kubectl --context "${CONTEXT}" -n kyverno logs deployment/kyverno-admission-controller --all-containers --tail=200 >&2 || true
  docker ps -a --filter "name=^/${REGISTRY_NAME}$" >&2 || true
  docker logs "${REGISTRY_NAME}" --tail=200 >&2 || true
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
  docker rm -f "${REGISTRY_NAME}" >/dev/null 2>&1 || true
  rm -rf "${WORK_DIR}"
  exit "${status}"
}
trap cleanup EXIT

command -v cosign >/dev/null
command -v docker >/dev/null
command -v kind >/dev/null
command -v kubectl >/dev/null
command -v curl >/dev/null

# Start the disposable registry on the host bridge. It is attached to the kind
# network after the cluster exists, while host-side cosign uses localhost:5001.
docker rm -f "${REGISTRY_NAME}" >/dev/null 2>&1 || true
docker run \
  --detach \
  --restart=always \
  --label io.spearsystems.lcp.fixture=true \
  --publish "127.0.0.1:${REGISTRY_PORT}:5000" \
  --network bridge \
  --name "${REGISTRY_NAME}" \
  "${REGISTRY_IMAGE}"

kind_config="${WORK_DIR}/kind-config.yaml"
cat > "${kind_config}" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      config_path = "/etc/containerd/certs.d"
EOF

kind create cluster \
  --name "${CLUSTER_NAME}" \
  --image "${KIND_NODE_IMAGE}" \
  --config "${kind_config}" \
  --wait 180s

# Configure kind's containerd to route localhost:5001 to the registry
# container. Kubernetes dry-run admission does not pull an image, but this
# keeps the fixture usable for an optional real Pod pull as well.
registry_dir="/etc/containerd/certs.d/localhost:${REGISTRY_PORT}"
for node in $(kind get nodes --name "${CLUSTER_NAME}"); do
  docker exec "${node}" mkdir -p "${registry_dir}"
  printf '[host."http://%s:5000"]\n' "${REGISTRY_NAME}" | \
    docker exec -i "${node}" sh -c "cat > '${registry_dir}/hosts.toml'"
done

docker network connect --alias "${REGISTRY_NAME}" kind "${REGISTRY_NAME}" 2>/dev/null || true

registry_ready=false
for _ in $(seq 1 60); do
  if curl --fail --silent "http://${REGISTRY_PUSH_HOST}/v2/" >/dev/null; then
    registry_ready=true
    break
  fi
  sleep 1
done
if [[ "${registry_ready}" != true ]]; then
  echo "Local OCI registry did not become ready at ${REGISTRY_PUSH_HOST}" >&2
  exit 1
fi

kubectl --context "${CONTEXT}" cluster-info

install_manifest="${WORK_DIR}/kyverno-install.yaml"
base_url="https://github.com/kyverno/kyverno/releases/download/${KYVERNO_VERSION}"
curl --fail --silent --show-error --location --retry 3 \
  "${base_url}/install.yaml" --output "${install_manifest}"
echo "${KYVERNO_INSTALL_SHA256}  ${install_manifest}" | sha256sum --check --strict

kubectl --context "${CONTEXT}" apply --server-side -f "${install_manifest}"
# The local registry intentionally uses HTTP inside the disposable test. This
# flag is never part of the production deployment example.
kubectl --context "${CONTEXT}" -n kyverno patch deployment kyverno-admission-controller \
  --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--allowInsecureRegistry=true"}]'
kubectl --context "${CONTEXT}" wait --for=condition=Established \
  crd/clusterpolicies.kyverno.io --timeout=180s
kubectl --context "${CONTEXT}" -n kyverno rollout status \
  deployment/kyverno-admission-controller --timeout=180s

# Build one scratch image and publish four independent tags into the local
# registry. The first image is unsigned; the second is signed by an untrusted
# fixture key; the last two are signed by the trusted fixture key but have one
# required attestation intentionally omitted.
image_dir="${WORK_DIR}/image"
mkdir -p "${image_dir}"
cat > "${image_dir}/Dockerfile" <<'EOF'
FROM scratch
LABEL org.opencontainers.image.title="LCP Kyverno admission fixture"
LABEL org.opencontainers.image.description="Disposable local image used by LCP admission tests"
EOF

push_ref="${REGISTRY_PUSH_HOST}/${FIXTURE_REPOSITORY}"
kube_ref="${REGISTRY_KUBE_HOST}/${FIXTURE_REPOSITORY}"
docker build --platform linux/amd64 --tag "${push_ref}:unsigned" "${image_dir}"
for tag in wrong-workflow missing-provenance missing-sbom; do
  docker tag "${push_ref}:unsigned" "${push_ref}:${tag}"
done
for tag in unsigned wrong-workflow missing-provenance missing-sbom; do
  docker push "${push_ref}:${tag}"
done

generate_key_pair() {
  local destination="$1"
  mkdir -p "${destination}"
  (cd "${destination}" && COSIGN_PASSWORD='' cosign generate-key-pair >/dev/null)
}

generate_key_pair "${WORK_DIR}/trusted-key"
generate_key_pair "${WORK_DIR}/untrusted-key"
trusted_key="${WORK_DIR}/trusted-key/cosign.key"
trusted_public_key="${WORK_DIR}/trusted-key/cosign.pub"
untrusted_key="${WORK_DIR}/untrusted-key/cosign.key"

COSIGN_PASSWORD='' cosign sign --yes --key "${untrusted_key}" \
  --tlog-upload=false --allow-insecure-registry "${push_ref}:wrong-workflow"
for tag in missing-provenance missing-sbom; do
  COSIGN_PASSWORD='' cosign sign --yes --key "${trusted_key}" \
    --tlog-upload=false --allow-insecure-registry "${push_ref}:${tag}"
done

cat > "${WORK_DIR}/provenance.json" <<EOF
{
  "buildType": "https://github.com/SpearSystems/LCP/.github/workflows/test.yml",
  "builder": {"id": "https://github.com/SpearSystems/LCP"},
  "invocation": {"parameters": {"fixture": "local"}}
}
EOF
cat > "${WORK_DIR}/sbom.json" <<'EOF'
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "serialNumber": "urn:uuid:00000000-0000-4000-8000-000000000001",
  "version": 1,
  "components": []
}
EOF

# Give each attestation policy a positive control on the other attestation so
# the negative cases prove the missing predicate rather than merely a missing
# signature.
COSIGN_PASSWORD='' cosign attest --yes --key "${trusted_key}" \
  --tlog-upload=false --allow-insecure-registry \
  --predicate "${WORK_DIR}/sbom.json" \
  --type https://cyclonedx.org/bom "${push_ref}:missing-provenance"
COSIGN_PASSWORD='' cosign attest --yes --key "${trusted_key}" \
  --tlog-upload=false --allow-insecure-registry \
  --predicate "${WORK_DIR}/provenance.json" \
  --type https://slsa.dev/provenance/v1 "${push_ref}:missing-sbom"

indent_key() {
  sed 's/^/                      /' "${trusted_public_key}"
}

policy_dir="${WORK_DIR}/policies"
mkdir -p "${policy_dir}"
cat > "${policy_dir}/signature-policy.yaml" <<EOF
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: lcp-local-signature-policy
spec:
  validationFailureAction: Enforce
  background: false
  webhookConfiguration:
    failurePolicy: Fail
    timeoutSeconds: 30
  rules:
    - name: require-local-fixture-signature
      match:
        any:
          - resources:
              kinds:
                - Pod
      verifyImages:
        - imageReferences:
            - ${kube_ref}:unsigned
            - ${kube_ref}:wrong-workflow
          required: true
          verifyDigest: true
          attestors:
            - count: 1
              entries:
                - keys:
                    rekor:
                      ignoreTlog: true
                    publicKeys: |-
$(indent_key)
EOF
cat > "${policy_dir}/provenance-policy.yaml" <<EOF
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: lcp-local-provenance-policy
spec:
  validationFailureAction: Enforce
  background: false
  webhookConfiguration:
    failurePolicy: Fail
    timeoutSeconds: 30
  rules:
    - name: require-local-provenance
      match:
        any:
          - resources:
              kinds:
                - Pod
      verifyImages:
        - imageReferences:
            - ${kube_ref}:missing-provenance
          verifyDigest: true
          attestations:
            - predicateType: https://slsa.dev/provenance/v1
              attestors:
                - count: 1
                  entries:
                    - keys:
                        rekor:
                          ignoreTlog: true
                        publicKeys: |-
$(sed 's/^/                          /' "${trusted_public_key}")
EOF
cat > "${policy_dir}/sbom-policy.yaml" <<EOF
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: lcp-local-sbom-policy
spec:
  validationFailureAction: Enforce
  background: false
  webhookConfiguration:
    failurePolicy: Fail
    timeoutSeconds: 30
  rules:
    - name: require-local-cyclonedx-sbom
      match:
        any:
          - resources:
              kinds:
                - Pod
      verifyImages:
        - imageReferences:
            - ${kube_ref}:missing-sbom
          verifyDigest: true
          attestations:
            - predicateType: https://cyclonedx.org/bom
              attestors:
                - count: 1
                  entries:
                    - keys:
                        rekor:
                          ignoreTlog: true
                        publicKeys: |-
$(sed 's/^/                          /' "${trusted_public_key}")
EOF

for policy in signature-policy.yaml provenance-policy.yaml sbom-policy.yaml; do
  kubectl --context "${CONTEXT}" apply -f "${policy_dir}/${policy}"
done

for policy_name in lcp-local-signature-policy lcp-local-provenance-policy lcp-local-sbom-policy; do
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

  if ! grep -Eiq 'image verification|denied|rejected|failed to verify|verification failed|signature[s]? (not found|invalid|mismatch)|invalid signature|attestation[s]?|no matching (signature[s]?|attestation[s]?)|no (signature[s]?|attestation[s]?) found|does not satisfy' "${output_file}"; then
    cat "${output_file}"
    echo "${pod_name} failed for a reason other than Kyverno image verification" >&2
    exit 1
  fi

  if grep -Fq "${expected_policy}" "${output_file}"; then
    echo "Admission correctly rejected ${pod_name} (${image}) via ${expected_policy}"
  else
    echo "Admission correctly rejected ${pod_name} (${image}); Kyverno policy ${expected_policy} denied verification"
  fi
  cat "${output_file}"
}

assert_rejected unsigned-image "${kube_ref}:unsigned" lcp-local-signature-policy
assert_rejected wrong-workflow-image "${kube_ref}:wrong-workflow" lcp-local-signature-policy
assert_rejected missing-provenance-image "${kube_ref}:missing-provenance" lcp-local-provenance-policy
assert_rejected missing-sbom-image "${kube_ref}:missing-sbom" lcp-local-sbom-policy

echo "Ephemeral Kyverno admission rejection test passed with a local OCI registry"

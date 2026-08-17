# LCP Production Deployment
> **Operations page · Page 4 of 6**

This guide describes the self-hosted deployment model for the reference LCP
platform. It is cloud-neutral; Kubernetes is provided as an example, not a
requirement.

## Recommended production topology

```text
Internet / private partner networks
              ↓
      TLS ingress / WAF / DDoS
              ↓
   LCP API deployment (N replicas)
              ↓
       Postgres primary/replicas
              ↓
   Delivery worker deployment (M replicas)
              ↓
      Buyer webhook endpoints
```

The API is intended to be stateless. Shared state belongs in Postgres and the
durable delivery queue. Do not put the database on the public network.

## Postgres

Install the production extra:

```bash
python3 -m pip install 'lcp-reference-platform[postgres]'
export LCP_DATABASE_URL='postgresql://lcp_app:<password>@postgres.internal:5432/lcp?sslmode=verify-full'
export LCP_PII_ENCRYPTION_KEY='<urlsafe-base64-32-byte-key>'
```

Use a dedicated database role with only the permissions required by the
application. Keep migrations under operator change control, encrypt database
connections, enable database encryption/backups, and test restores regularly.

The reference store creates its required tables idempotently and maintains
compatibility columns for upgrades. A production operator should wrap schema
changes in its normal migration/release process before rolling out multiple
API or worker versions. The normal CI workflow runs the full reference-platform
suite against a fresh Postgres 16 service; the integration test covers actual
Postgres transactions, encrypted persisted envelopes, duplicate idempotency,
and privacy erasure. Run it locally only against an isolated disposable
database, using the command in the [reference-platform README](../implementations/reference-platform/README.md#validate-a-real-postgres-deployment).

Production startup fails closed unless
`LCP_PII_ENCRYPTION_KEY` is configured; it is a URL-safe base64 32-byte AES-GCM
key used to protect persisted envelopes.

Offer discovery, quota lookup, and candidate routing run on the relational
`tenant_id`/`vertical`/`active` columns (kept in sync with `offer_json`), so no
SQLite-specific SQL reaches the production backend:

```bash
# Active offers for a tenant, optionally filtered by vertical.
curl -sS "$LCP_ENDPOINT/v1/lcp/offers?vertical=mortgage" \
  -H "Authorization: Bearer $LCP_API_KEY"

# Monthly quota/pacing for one offer.
curl -sS "$LCP_ENDPOINT/v1/lcp/offers/<offer_id>/quota" \
  -H "Authorization: Bearer $LCP_API_KEY"
```

Tenant isolation is enforced in the query shape (`WHERE active AND tenant_id =
? [AND vertical = ?]`); the integration suite runs the same discovery, quota,
and candidate-selection assertions against a real Postgres service.

## Secrets

Prefer a mounted secret-manager file over database-stored HMAC secrets:

```json
{
  "publisher_001": {
    "tenant_id": "publisher_tenant",
    "hmac_secret": "provided-by-your-secret-manager",
    "scopes": ["lead:submit", "offer:read", "lead:read"]
  },
  "buyer_001": {
    "tenant_id": "buyer_tenant",
    "hmac_secret": "provided-by-your-secret-manager",
    "scopes": ["bid:submit", "lead:read"]
  }
}
```

```bash
chmod 600 /run/secrets/lcp-credentials.json
export LCP_SECRETS_FILE=/run/secrets/lcp-credentials.json
```

Use KMS/HSM-backed secret management for the Regulated profile. Rotate the
active secret and retain the previous secret only for the documented migration
window. The application encryption key protects persisted envelopes with
AES-GCM; key rotation requires an operator-controlled re-encryption migration,
not simply replacing the environment variable.

## Environment settings

Important settings include:

| Variable | Production guidance |
|---|---|
| `LCP_DATABASE_URL` | Use Postgres; do not use SQLite for multi-node operation. |
| `LCP_SCHEMA_DIR` | Pin schemas to the deployed release. |
| `LCP_REQUIRE_AUTH` | Always `true`. |
| `LCP_SECRETS_FILE` | Mount from a secret manager with private permissions. |
| `LCP_PII_ENCRYPTION_KEY` | Required outside test mode; URL-safe base64 32-byte AES-GCM key. |
| `LCP_ALLOW_INSECURE_WEBHOOKS` | Always `false`. |
| `LCP_WEBHOOK_HOST_ALLOWLIST` | Prefer an explicit buyer egress allowlist. |
| `LCP_RATE_LIMIT_PER_MINUTE` | Set per traffic class and enforce edge quotas too. |
| `LCP_MAX_BODY_BYTES` | Keep bounded and review against vertical payload sizes. |
| `LCP_REPLAY_WINDOW_SECONDS` | Five minutes or shorter. |
| `LCP_ATTACHMENT_BACKEND` | `file` for a sandbox/single node; use `s3` for the production S3-compatible object-storage adapter. |
| `LCP_ATTACHMENT_DIRECTORY` | Used by the reference file backend only; keep it on residency-controlled encrypted storage. |
| `LCP_MAX_ATTACHMENT_BYTES` | Bound according to malware scanning, storage, and buyer requirements. |
| `LCP_ALLOWED_ATTACHMENT_CONTENT_TYPES` | Narrow to approved document/image types; scan before downstream release. |
| `LCP_ATTACHMENT_SCANNER` / `LCP_ATTACHMENT_SCAN_REQUIRED` | Use `clamav` and `true` in production; `none` is sandbox-only. |
| `LCP_ATTACHMENT_RESIDENCY` / `LCP_ATTACHMENT_ALLOWED_RESIDENCIES` | Declare and allow only the country/region policy for this deployment. |
| `LCP_ATTACHMENT_OBJECT_BUCKET` / `LCP_ATTACHMENT_OBJECT_PREFIX` | S3-compatible bucket and isolated key prefix for attachments. |
| `LCP_ATTACHMENT_OBJECT_REGION` / `LCP_ATTACHMENT_OBJECT_ENDPOINT_URL` | Provider region and optional private/S3-compatible endpoint. |
| `LCP_ATTACHMENT_OBJECT_KMS_KEY_ID` | Required for the S3 adapter; use a dedicated KMS key/grant with least privilege. |
| `LCP_ATTACHMENT_OBJECT_RESIDENCY` | Immutable residency of the selected object-store adapter; must be allowed by policy. |
| `LCP_ATTACHMENT_CLAMAV_HOST` / `LCP_ATTACHMENT_CLAMAV_PORT` | Private `clamd` service used for fail-closed malware scanning. |

## Kubernetes example

The example manifests are in
[`implementations/reference-platform/kubernetes/`](../implementations/reference-platform/kubernetes/).
They demonstrate:

- Separate API and worker deployments.
- A Postgres connection supplied through a Secret.
- A shared attachment volume for the reference encrypted file backend; use an
  object-store adapter for multi-zone production.
- A mounted LCP credential Secret.
- `/health/ready` readiness and `/health/live` liveness probes.
- Non-root containers and a read-only root filesystem where supported.
- Resource requests/limits.
- A ClusterIP service for an external ingress/controller.

The example intentionally does not include a public Ingress, cloud load
balancer, managed-Postgres resource, object-storage bucket, KMS key, or ClamAV
service. For production attachments, configure the S3-compatible adapter and
private scanner described in [MVA and attachments](MVA-ATTACHMENTS.md), then
bind the pod identity to only the bucket prefix and KMS key it needs. Add
those resources according to the chosen cloud, network, residency, and
organization policies. Before applying a production image, follow the
[container signing and provenance guide](CONTAINER-SUPPLY-CHAIN.md) and enforce
the verified digest with the selected admission controller.

## Scaling

There is no protocol-imposed lead-per-second ceiling. Capacity is a deployment
property determined by:

- API worker count and CPU/memory.
- Postgres write/read capacity.
- Queue/worker throughput.
- Buyer webhook latency and failure rate.
- Payload size and schema complexity.
- Network and WAF limits.

Scale by measuring p50/p95/p99 intake latency, database utilization, queue
age, delivery success rate, retry depth, and webhook latency. Do not increase
worker replicas without delivery leases and a database configuration that can
handle concurrent workers.

## Recovery targets

Until an operator supplies stricter business targets, the reference
production profile should plan for:

- **RPO:** 15 minutes for accepted lead data, assuming scheduled encrypted
  Postgres backups/WAL archiving.
- **RTO:** 60 minutes for a single-region service restoration.
- **Delivery recovery:** retryable deliveries remain durable and resume after
  worker restart; expired leases are reclaimable.

These are planning targets, not guarantees. Test them with restore drills,
worker termination, database failover, and region/network failure scenarios.

---

**Previous:** [Implementation decisions](IMPLEMENTATION-DECISIONS.md) · **Next:** [Operations runbook](OPERATIONS.md)

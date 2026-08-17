# LCP Reference Platform

Production-oriented reference HTTP platform and router for the Lead Context
Protocol. It accepts publisher leads, applies buyer offers, runs the
ping/bid/post flow, persists state, and delivers signed webhooks with retries.

This is an implementation foundation, not a hosted exchange. Production
operators must still provide TLS termination, secret management, database
backups, observability, access control, retention policies, and deployment
hardening.

## Components

- SQLite persistence for local/single-node use and a Postgres backend for multi-node production; replaceable through the `Store` boundary.
- HTTP intake for `lead`, `call`, `bid`, and lifecycle `event` messages.
- Declarative publisher form mapping with versioned brand/flow normalization and audit digests.
- Encrypted out-of-band attachment upload/download for contracts, evidence, and call records, with a local AES-GCM backend and production S3-compatible/SSE-KMS adapter, residency controls, and fail-closed ClamAV scanning.
- Structured call payable evaluation and per-offer monthly quota reporting.
- JSON Schema and strict ping-safe validation.
- Bearer API-key and HMAC authentication.
- Deterministic offer matching and auction selection, including versioned
  `lcp.platform.requirements` and `lcp.platform.service_area` extensions.
- At-least-once buyer webhook delivery.
- Standalone delivery worker for multi-process deployments.
- WSGI entry point for a production HTTP process manager.
- Admin CLI for credentials, offers, and controlled lead erasure.

See [the implementation decisions](../../docs/IMPLEMENTATION-DECISIONS.md) for
matching, signing, retry, and sandbox behavior. See the
[supply-chain security guide](../../docs/SUPPLY-CHAIN-SECURITY.md) for package,
image, and release controls, and the [container verification guide](../../docs/CONTAINER-SUPPLY-CHAIN.md)
for signing, provenance, and admission enforcement.

## Install

From the repository root:

```bash
# Local/single-node profile.
python3 -m pip install -e implementations/reference-platform
export LCP_SCHEMA_DIR="$PWD/schemas"
export LCP_DATABASE_PATH="$PWD/data/lcp.sqlite3"
# Generate once, store securely, and reuse for this database:
# python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
export LCP_PII_ENCRYPTION_KEY="<persisted-urlsafe-base64-32-byte-key>"

# Production profile.
python3 -m pip install -e 'implementations/reference-platform[production]'
export LCP_DATABASE_URL='postgresql://lcp_app:<password>@postgres.internal:5432/lcp?sslmode=verify-full'
export LCP_PII_ENCRYPTION_KEY='<urlsafe-base64-32-byte-key>'
```

The package targets Python 3.10+. The `[production]` extra adds Gunicorn, Psycopg, boto3, and the ClamAV client
for the Postgres/WSGI/object-storage deployment profile. Configure
`LCP_ATTACHMENT_DIRECTORY`, `LCP_MAX_ATTACHMENT_BYTES`, and
`LCP_ALLOWED_ATTACHMENT_CONTENT_TYPES` for the reference file backend. For
multi-node production, use `LCP_ATTACHMENT_BACKEND=s3` with an SSE-KMS key,
explicit residency, and a private ClamAV service; see
[the attachment deployment guide](../../docs/MVA-ATTACHMENTS.md).

## Validate a real Postgres deployment

The repository's normal CI runs the reference-platform suite against a fresh
Postgres 16 service, including encrypted intake, duplicate idempotency, and
privacy erasure. To run the same test locally, provision an isolated
throwaway database, install the Postgres extra, and set its URL:

```bash
python3 -m pip install -e 'implementations/reference-platform[production]'
export LCP_TEST_POSTGRES_URL='postgresql://lcp_test:<password>@127.0.0.1:5432/lcp_test'
PYTHONPATH=implementations/reference-platform \
  python3 -m unittest discover -s implementations/reference-platform/tests -v
```

Never point the integration test at a shared or production database. The test
creates its own uniquely named tenants and IDs but does not perform a full
schema reset.

## Configure credentials and offers

Do not put production credentials in source control. For local setup, add a
publisher and buyer credential with least-privilege scopes:

```bash
lcp-platform-admin credential upsert \
  --sender-id publisher_001 \
  --tenant-id publisher_tenant \
  --scope lead:submit --scope lead:read \
  --hmac-secret publisher-secret

lcp-platform-admin credential upsert \
  --sender-id buyer_001 \
  --tenant-id buyer_tenant \
  --scope bid:submit --scope lead:read \
  --hmac-secret buyer-secret
```

Create an offer file:

```json
{
  "offer_id": "mortgage-au",
  "buyer_id": "buyer_001",
  "active": true,
  "routing_mode": "auction",
  "vertical": "mortgage",
  "countries": ["AU"],
  "floor_price_cents": 1500,
  "currency": "AUD",
  "require_consent_evidence": true,
  "require_verified_phone": true,
  "reject_incentivized": true,
  "max_spam_risk_score": 25,
  "daily_cap": 100,
  "ping_timeout_seconds": 30,
  "webhook_url": "https://buyer.example/lcp/webhook"
}
```

Validate and store it:

```bash
lcp-platform-admin offer upsert --file offer.json

# Controlled privacy operation; verify the legal request and retain the audit record.
lcp-platform-admin privacy erase-lead --lead-id lead_abc123 --actor-id privacy_operator
```

Offer management is intentionally an operator/admin concern. The public LCP
surface exposes active offers through `GET /v1/lcp/offers`. For fast-changing
buyer requirements, use the versioned [requirement profiles and service areas](../../docs/REQUIREMENT-PROFILES.md)
without expanding the universal core or creating a new vertical for each
category.

## Run locally

For a single-process local deployment, the HTTP server and delivery worker run
together:

```bash
export LCP_REQUIRE_AUTH=true
# Keep this key in a secret manager for any non-test deployment.
# Generate once, store securely, and reuse for this database:
# python3 -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
export LCP_PII_ENCRYPTION_KEY="<persisted-urlsafe-base64-32-byte-key>"
lcp-platform
```

The default listener is `127.0.0.1:8080`. Configure `LCP_HOST`, `LCP_PORT`,
and the other settings in `lcp_platform/config.py` through environment
variables.

## Production process layout

For a production deployment, run the HTTP application and delivery worker
under a process supervisor:

```text
TLS reverse proxy
       ↓
WSGI/HTTP application processes  ←→  shared database
       ↑
Delivery worker process
```

The WSGI entry point is `lcp_platform.wsgi:application`. With the
`[production]` extra, a typical process command is:

```bash
gunicorn --bind 0.0.0.0:8080 --workers 3 --threads 4 \
  --access-logfile - lcp_platform.wsgi:application
```

The WSGI path handles binary attachments with the same behavior as the local
server: the attachment body limit (`LCP_MAX_ATTACHMENT_BYTES`) is selected
before `wsgi.input` is read, downloads stream raw encrypted bytes (never
`json.dumps`), and binary headers (`Content-Type`, `Content-Disposition`,
`Content-Length`) are forwarded. Upload and download with curl:

```bash
export LCP_ATTACHMENT_FILE=scanned-evidence.pdf
export LCP_ATTACHMENT_ID=att_evidence_001
export LCP_ATTACHMENT_SHA256="$(sha256sum "$LCP_ATTACHMENT_FILE" | cut -d' ' -f1)"

# Upload: Bearer-authenticated raw bytes. The route's body limit applies before
# wsgi.input is read; replace the lead and sender IDs with your own values.
curl -sS -X POST "$LCP_ENDPOINT/v1/lcp/attachments" \
  -H "Authorization: Bearer $LCP_API_KEY" \
  -H "X-LCP-Sender-Id: publisher_001" \
  -H "X-LCP-Lead-Id: <lead_id>" \
  -H "X-LCP-Attachment-Id: $LCP_ATTACHMENT_ID" \
  -H "X-LCP-Attachment-Purpose: supporting_document" \
  -H "X-LCP-Filename: $LCP_ATTACHMENT_FILE" \
  -H "X-LCP-Content-SHA256: $LCP_ATTACHMENT_SHA256" \
  -H "X-LCP-Idempotency-Key: publisher-attachment-001" \
  -H "Content-Type: application/pdf" \
  --data-binary @"$LCP_ATTACHMENT_FILE"

# Download: bytes are returned unchanged with the original content headers.
curl -sS "$LCP_ENDPOINT/v1/lcp/attachments/$LCP_ATTACHMENT_ID" \
  -H "Authorization: Bearer $LCP_API_KEY" \
  -H "X-LCP-Sender-Id: publisher_001" \
  -o evidence.bin
```

Run `lcp-platform-worker` as a separate supervised process. Place the
application behind TLS; the included threaded server is intended for local
operation and smoke tests.

SQLite is appropriate for a single-node deployment or controlled starting
point. Use a compatible production store implementation when operating at
multiple nodes or high throughput.

## HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/lcp/leads` | Publisher lead intake (direct or offer-routed) |
| `POST` | `/v1/lcp/calls` | Publisher call intake |
| `POST` | `/v1/lcp/bids` | Buyer bid submission |
| `POST` | `/v1/lcp/events` | Call outcomes and lifecycle/payable updates |
| `POST` | `/v1/lcp/attachments` | Authenticated scanned/encrypted binary upload |
| `GET` | `/v1/lcp/attachments/{attachment_id}` | Authenticated attachment download |
| `GET` | `/v1/lcp/offers/{offer_id}/quota` | Monthly payable quota and pacing report |
| `GET` | `/v1/lcp/leads/{lead_id}` | Lead status and lifecycle |
| `GET` | `/health/live` | Unauthenticated process liveness |
| `GET` | `/health/ready` | Database-backed readiness |
| `GET` | `/v1/lcp/metrics` | Admin-scoped aggregate worker and retention metrics |
| `GET` | `/v1/lcp/capabilities` | Capability discovery |
| `GET` | `/v1/lcp/offers` | Active offer discovery |
| `GET` | `/v1/lcp/schemas/{name}` | Schema discovery |

The complete transport contract is [api/lcp-openapi.yaml](../../api/lcp-openapi.yaml).

`/v1/lcp/metrics` requires the `platform:admin` scope in an authenticated
production deployment. It returns queue age, retry depth, lease expiry,
attachment scanner/deletion backlog, and dead-letter counts only; it never
returns lead IDs, buyer IDs, tenant IDs, URLs, or consumer payloads.

Exhausted delivery and routing jobs are durable dead letters. Operators can
inspect and recover them without reading payloads:

```bash
lcp-platform-admin dead-letter list --status OPEN
lcp-platform-admin dead-letter quarantine --job-id <job-id>
lcp-platform-admin dead-letter replay --job-id <job-id>
```

Lifecycle events are role-scoped and idempotent. A delivered buyer reports
`ACCEPTED`/`REJECTED`/`DISPUTED`; the publisher reports `CONSENT_WITHDRAWN`
and `ERASURE_REQUEST`; every applied transition writes an audit record with
actor, event, and previous status. Status reads are projected by role — a
buyer sees only its own match decisions, payables, and events:

```bash
# Buyer confirms the delivered lead (event -> audit row lead.accepted).
curl -sS -X POST "$LCP_ENDPOINT/v1/lcp/events" \
  -H "Authorization: Bearer $LCP_API_KEY" \
  -H "X-LCP-Sender-Id: buyer_001" \
  -H "X-LCP-Idempotency-Key: buyer-accept-001" \
  -H "Content-Type: application/json" \
  -d '{"lcp":{"version":"1.0.0","message":{"type":"event","id":"<uuid>","timestamp":"2026-08-18T00:00:00Z","sender_id":"buyer_001","receiver_id":"platform_001","idempotency_key":"buyer-accept-001"},"payload":{"lead_id":"<lead_id>","event":"ACCEPTED","timestamp":"2026-08-18T00:00:00Z"}}}'

# Publisher reads status; the response contains only publisher-scoped data.
curl -sS "$LCP_ENDPOINT/v1/lcp/leads/<lead_id>" \
  -H "Authorization: Bearer $LCP_API_KEY"
```

## Tenant and credential model

Every sender has a tenant ID and scopes. Examples include `lead:submit`,
`lead:read`, `bid:submit`, `event:submit`, `attachment:write`, `offer:read`,
and `platform:admin`. Lead status is
only visible to the submitting sender, an authorized buyer that received the
lead, or a platform administrator. Offers are routed within the configured
`LCP_ROUTING_TENANT_ID` and are not writable through participant endpoints.

Production credentials should be mounted from a secret manager using
`LCP_SECRETS_FILE`; the database-backed credential path is intended for local
administration. Production startup also requires `LCP_PII_ENCRYPTION_KEY`, a
URL-safe base64 encoding of a random 32-byte AES-GCM key. Persisted lead,
ping, bid, post, and event envelopes are encrypted and authenticated before
being written to SQLite or Postgres. Store that key in a KMS/HSM-backed secret
manager and plan an explicit re-encryption migration before rotating it.

## Authentication

HMAC requests use:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

The exact byte sequence is signed with HMAC-SHA256. Requests carry
`X-LCP-Timestamp`, `X-LCP-Idempotency-Key`, `X-LCP-Signature`, and
`X-LCP-Sender-Id`. The default replay window is five minutes.

Bearer requests use `Authorization: Bearer <token>` and still carry the
sender and idempotency headers for mutating operations.

## Package publishing

A repository-level GitHub Actions workflow can publish the SDK, MCP adapter,
and reference platform to PyPI through Trusted Publishing on `v*` tags. Each
package must first be configured as a PyPI Trusted Publisher; no long-lived
PyPI token is stored in the repository.

## Sandbox parity

The Docker sandbox uses this same platform package, schemas, matcher, auth,
and delivery worker. It uses a separate test database and should only receive
synthetic test messages marked with `X-LCP-Test: true` and envelope `test: true`.

```bash
docker compose -f implementations/reference-platform/docker-compose.yml up --build
```

See [examples/sandbox/README.md](../../examples/sandbox/README.md) for a
publisher/offer/buyer smoke test.

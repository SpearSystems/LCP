# LCP Reference Platform

Production-oriented reference HTTP platform and router for the Lead Context
Protocol. It accepts publisher leads, applies buyer offers, runs the
ping/bid/post flow, persists state, and delivers signed webhooks with retries.

This is an implementation foundation, not a hosted exchange. Production
operators must still provide TLS termination, secret management, database
backups, observability, access control, retention policies, and deployment
hardening.

## Components

- SQLite persistence by default; replaceable through the `Store` boundary.
- HTTP intake for `lead`, `call`, and `bid` messages.
- JSON Schema and strict ping-safe validation.
- Bearer API-key and HMAC authentication.
- Deterministic offer matching and auction selection.
- At-least-once buyer webhook delivery.
- Standalone delivery worker for multi-process deployments.
- WSGI entry point for a production HTTP process manager.
- Admin CLI for credentials and offers.

See [the implementation decisions](../../docs/IMPLEMENTATION-DECISIONS.md) for
matching, signing, retry, and sandbox behavior.

## Install

From the repository root:

```bash
python3 -m pip install -e implementations/reference-platform
export LCP_SCHEMA_DIR="$PWD/schemas"
export LCP_DATABASE_PATH="$PWD/data/lcp.sqlite3"
```

The package targets Python 3.10+.

## Configure credentials and offers

Do not put production credentials in source control. For local setup, add a
publisher and buyer credential:

```bash
lcp-platform-admin credential upsert \
  --sender-id publisher_001 \
  --hmac-secret publisher-secret

lcp-platform-admin credential upsert \
  --sender-id buyer_001 \
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
```

Offer management is intentionally an operator/admin concern. The public LCP
surface exposes active offers through `GET /v1/lcp/offers`.

## Run locally

For a single-process local deployment, the HTTP server and delivery worker run
together:

```bash
export LCP_REQUIRE_AUTH=true
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

The WSGI entry point is `lcp_platform.wsgi:application`. Run
`lcp-platform-worker` as a separate supervised process. Use a production WSGI
server/process manager and place the application behind TLS; the included
threaded server is intended for local operation and smoke tests.

SQLite is appropriate for a single-node deployment or controlled starting
point. Use a compatible production store implementation when operating at
multiple nodes or high throughput.

## HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/lcp/leads` | Publisher lead intake (direct or offer-routed) |
| `POST` | `/v1/lcp/calls` | Publisher call intake |
| `POST` | `/v1/lcp/bids` | Buyer bid submission |
| `GET` | `/v1/lcp/leads/{lead_id}` | Lead status and lifecycle |
| `GET` | `/v1/lcp/capabilities` | Capability discovery |
| `GET` | `/v1/lcp/offers` | Active offer discovery |
| `GET` | `/v1/lcp/schemas/{name}` | Schema discovery |

The complete transport contract is [api/lcp-openapi.yaml](../../api/lcp-openapi.yaml).

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

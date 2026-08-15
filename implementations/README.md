# LCP — Reference Implementations

> **Implementation hub · Page 4 of 6**
>
> [Previous: SDK hub](sdk/README.md) · [Next: deployment guide](../docs/DEPLOYMENT.md)

These implementations share the LCP schemas and signing profile but have
different responsibilities:

## sdk

The multi-language SDK program provides idiomatic clients and server helpers
for publishers, buyers, platforms, and webhook receivers. The shared contract
and support tiers are documented in [`../docs/SDK-ROADMAP.md`](../docs/SDK-ROADMAP.md).

See the [SDK index](sdk/README.md).

### sdk/python

Standalone Python SDK for envelope construction, validation, HMAC signing,
idempotency, webhook verification, and HTTP operations. It does not depend on
MCP or platform storage.

See [sdk/python/README.md](sdk/python/README.md).

## reference-platform

Production-oriented HTTP gateway/router with SQLite/Postgres persistence,
offer matching, durable routing and delivery jobs, signed webhook retries,
scoped credentials, AES-GCM envelope encryption, controlled lead erasure,
health endpoints, an admin CLI, and a Docker sandbox. It is a foundation for
an operator's deployment, not a hosted exchange or security certification.

See [reference-platform/README.md](reference-platform/README.md), the
[deployment guide](../docs/DEPLOYMENT.md), and the
[security architecture](../docs/SECURITY-ARCHITECTURE.md).

## mcp-server

The official LCP MCP adapter — a thin, stateless wrapper exposing an existing
LCP REST endpoint as agent tools. It does not receive webhooks, persist leads,
or run auctions.

| Tool | LCP Endpoint | Description |
|---|---|---|
| `submit_lead` | `POST /v1/lcp/leads` | Submit a lead message |
| `submit_call` | `POST /v1/lcp/calls` | Submit a call lead message |
| `query_lead_status` | `GET /v1/lcp/leads/{lead_id}` | Query lead status and lifecycle |
| `get_schema` | `GET /v1/lcp/schemas/{name}` | Retrieve a JSON Schema (falls back to local repo) |
| `get_capabilities` | `GET /v1/lcp/capabilities` | Discover endpoint capabilities |
| `list_offers` | `GET /v1/lcp/offers` | List active offers |
| `submit_bid` | `POST /v1/lcp/bids` | Submit a bid in response to a ping |

See [mcp-server/README.md](mcp-server/README.md) for setup and configuration.

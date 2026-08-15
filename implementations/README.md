# LCP — Reference Implementations

These implementations share the LCP schemas and signing profile but have
different responsibilities:

## sdk/python

Standalone Python SDK for envelope construction, validation, HMAC signing,
idempotency, and HTTP operations. It does not depend on MCP or platform
storage.

See [sdk/python/README.md](sdk/python/README.md).

## reference-platform

Production-oriented HTTP gateway/router with SQLite persistence, offer
matching, ping/bid/post orchestration, signed webhook retries, an admin CLI,
and a Docker sandbox. It is a foundation for an operator's deployment, not a
hosted exchange.

See [reference-platform/README.md](reference-platform/README.md).

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

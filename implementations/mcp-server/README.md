# LCP MCP Server — Reference Implementation

A thin MCP (Model Context Protocol) adapter that exposes LCP REST endpoints
as agent tools. The core protocol is REST+JSON; this server wraps it so AI
agents can interact with any LCP-compliant endpoint via MCP. It does not
host an LCP platform, persist leads, receive webhooks, or run auctions.

> **Architecture:** The LCP core never depends on MCP. This server is a
> stateless adapter — it translates MCP tool calls into HTTP requests and
> HTTP responses into MCP tool results. It works with any LCP-compliant
> endpoint (any LCP-compliant REST API).

## Tools

| Tool | LCP Endpoint | Description |
|---|---|---|
| `submit_lead` | `POST /v1/lcp/leads` | Submit a lead message |
| `submit_call` | `POST /v1/lcp/calls` | Submit a call lead message |
| `query_lead_status` | `GET /v1/lcp/leads/{lead_id}` | Query lead status and lifecycle |
| `get_schema` | `GET /v1/lcp/schemas/{name}` | Retrieve a JSON Schema (envelope, core, message type, offer, vertical) |
| `get_capabilities` | `GET /v1/lcp/capabilities` | Discover endpoint capabilities (versions, verticals, countries) |
| `list_offers` | `GET /v1/lcp/offers` | List active offers for the authenticated sender |
| `submit_bid` | `POST /v1/lcp/bids` | Submit a bid in response to a ping |

## Setup

```bash
# From the repository root while packages are unreleased.
python3 -m pip install -e implementations/sdk/python -e implementations/mcp-server

# After the packages are published, install the adapter and its shared SDK.
python3 -m pip install lcp-mcp-server lcp-sdk
```

The adapter delegates HTTP, HMAC signing, retries, and raw-body security
primitives to `lcp-sdk`; it only provides the MCP tool binding and local schema
fallback.

### Safety behavior

- **Local validation before send.** Every tool-created envelope (lead, call,
  bid) is validated with the bundled `lcp-sdk` schema validator before any
  request is made. Malformed payloads fail with `LCP-100` and never reach the
  network.
- **Schema lookup.** `get_schema` queries the REST endpoint first. If the
  endpoint is unreachable (transport error), it falls back to the repository's
  `schemas/` and `verticals/` directories; when the endpoint answers with an
  HTTP error (for example 401 or 404), that error is surfaced instead of being
  masked by the local fallback. Local schema resolution is confined to the
  approved directories — traversal and symlink escapes are rejected.
- **Encoded identifiers.** Lead IDs and schema names are percent-encoded per
  path segment before they are interpolated into URLs, so `?`, `#`, and `..`
  cannot alter the request.

## Configuration

The server reads its target endpoint from environment variables:

| Variable | Default | Description |
|---|---|---|
| `LCP_ENDPOINT` | `http://localhost:8000` | Base URL of the LCP-compliant REST API |
| `LCP_API_KEY` | (none) | Bearer token for authentication |
| `LCP_HMAC_SECRET` | (none) | HMAC shared secret (if using HMAC auth instead of API key) |
| `LCP_SENDER_ID` | (none) | Sender ID to use in outgoing messages and read requests |

## Authentication profile

HMAC requests use the canonical signing input:

```text
<timestamp>\n<idempotency-key>\n<raw-request-body>
```

The adapter sends `X-LCP-Sender-Id`, `X-LCP-Timestamp`,
`X-LCP-Idempotency-Key` for mutating requests, and `X-LCP-Signature`. Bearer
requests use `Authorization: Bearer <token>`.

## Run

```bash
# Stdio transport (for MCP clients like Claude Desktop, Hermes)
lcp-mcp-server

# Or via Python module
python -m lcp_mcp_server.server
```

## MCP Client Configuration

Add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lcp": {
      "command": "lcp-mcp-server",
      "env": {
        "LCP_ENDPOINT": "https://api.example.com",
        "LCP_API_KEY": "your-api-key",
        "LCP_SENDER_ID": "your-sender-id"
      }
    }
  }
}
```

## LicenseApache 2.0 — same as the LCP specification. See the [SDK support policy](../../docs/SDK-ROADMAP.md) for the MCP relationship and package compatibility.

# LCP MCP Server — Reference Implementation

A thin MCP (Model Context Protocol) adapter that exposes LCP REST endpoints
as agent tools. The core protocol is REST+JSON; this server wraps it so AI
agents can interact with any LCP-compliant endpoint via MCP.

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

## Setup

```bash
cd implementations/mcp-server
pip install -e .
```

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
<timestamp>\\n<idempotency-key>\\n<raw-request-body>
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

## License

Apache 2.0 — same as the LCP specification.
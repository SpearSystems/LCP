# LCP — Reference Implementations

## mcp-server

The official LCP MCP server — a thin adapter exposing LCP REST endpoints
as agent tools. Generic: works with any LCP-compliant endpoint.
SpearPointX is the first wired endpoint.

**Status: working** — 6 tools, stdio transport, local schema fallback.

| Tool | LCP Endpoint | Description |
|---|---|---|
| `submit_lead` | `POST /v1/lcp/leads` | Submit a lead message |
| `submit_call` | `POST /v1/lcp/calls` | Submit a call lead message |
| `query_lead_status` | `GET /v1/lcp/leads/{lead_id}` | Query lead status and lifecycle |
| `get_schema` | `GET /v1/lcp/schemas/{name}` | Retrieve a JSON Schema (falls back to local repo) |
| `get_capabilities` | `GET /v1/lcp/capabilities` | Discover endpoint capabilities |
| `list_offers` | `GET /v1/lcp/offers` | List active offers |

See [mcp-server/README.md](mcp-server/README.md) for setup and configuration.
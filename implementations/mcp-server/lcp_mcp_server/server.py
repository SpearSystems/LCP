"""LCP MCP Server — thin adapter wrapping LCP REST endpoints as MCP tools.

Tools map 1:1 to LCP REST endpoints per SPEC.md §10:
  submit_lead       -> POST /v1/lcp/leads
  submit_call       -> POST /v1/lcp/calls
  query_lead_status -> GET  /v1/lcp/leads/{lead_id}
  get_schema        -> GET  /v1/lcp/schemas/{name}  (falls back to local repo)
  get_capabilities  -> GET  /v1/lcp/capabilities
  list_offers       -> GET  /v1/lcp/offers

The server is stateless — it creates a fresh HTTP client per tool call.
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from lcp_sdk import build_envelope

from .client import LCPClient
from .schema_loader import list_schemas, load_schema

app: Server

def _make_envelope(msg_type: str, sender_id: str, receiver_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload using the shared Python SDK envelope builder."""
    return build_envelope(msg_type, sender_id, receiver_id, payload)


def _tool_result(data: dict[str, Any]) -> list[types.TextContent]:
    """Format a dict as an MCP text content response."""
    return [types.TextContent(type="text", text=json.dumps(data, indent=2))]


def _error_result(message: str, code: str = "LCP-500") -> list[types.TextContent]:
    """Format an error as an MCP text content response."""
    return _tool_result({"errors": [{"code": code, "message": message}]})


# ─── Tool definitions ────────────────────────────────────────────────────────

async def _list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="submit_lead",
            description="Submit a lead to an LCP-compliant endpoint. The payload should contain lead fields (consumer, location, compliance, attributes, channel, etc.). The server wraps it in an LCP envelope.",
            inputSchema={
                "type": "object",
                "properties": {
                    "receiver_id": {"type": "string", "description": "LCP receiver ID (e.g. 'spx')."},
                    "payload": {
                        "type": "object",
                        "description": "Lead payload — consumer, location, compliance, provenance, attributes, channel, etc. See LCP lead schema.",
                    },
                },
                "required": ["receiver_id", "payload"],
            },
        ),
        types.Tool(
            name="submit_call",
            description="Submit a call lead to an LCP-compliant endpoint. The payload should contain call fields (call block, consumer, location, compliance, attributes).",
            inputSchema={
                "type": "object",
                "properties": {
                    "receiver_id": {"type": "string", "description": "LCP receiver ID."},
                    "payload": {
                        "type": "object",
                        "description": "Call payload — call block (call_id, status, durations, recording, etc.), consumer, location, compliance, attributes. See LCP call schema.",
                    },
                },
                "required": ["receiver_id", "payload"],
            },
        ),
        types.Tool(
            name="query_lead_status",
            description="Query the status and lifecycle of a previously submitted lead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string", "description": "The lead ID to query."},
                },
                "required": ["lead_id"],
            },
        ),
        types.Tool(
            name="get_schema",
            description="Retrieve a JSON Schema by name. Core schemas: envelope, core, lead, call, ping, post, ack, event, bid, offer. Vertical schemas: 'verticals/mortgage', etc. Falls back to local repo files if the REST endpoint is unavailable.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Schema name (e.g. 'envelope', 'lead', 'verticals/mortgage').",
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="get_capabilities",
            description="Discover the capabilities of the LCP endpoint — supported versions, message types, verticals, countries, auth methods, events, conformance level.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="list_offers",
            description="List active offers available to the authenticated sender. Optionally filter by vertical.",
            inputSchema={
                "type": "object",
                "properties": {
                    "vertical": {
                        "type": "string",
                        "description": "Filter offers by vertical (e.g. 'mortgage').",
                    },
                },
            },
        ),
        types.Tool(
            name="submit_bid",
            description="Submit a bid in response to a ping. The platform collects all bids and selects the winner. The winner receives a post with full PII.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ping_id": {"type": "string", "description": "ID of the ping being responded to."},
                    "decision": {"type": "string", "enum": ["accept", "reject", "pass"], "description": "accept = bid for the lead; reject = do not want; pass = decline to bid."},
                    "bid_price_cents": {"type": "integer", "minimum": 0, "description": "Bid price in cents. Required when decision = accept."},
                    "currency": {"type": "string", "description": "ISO 4217 currency code (e.g. AUD, USD)."},
                    "estimated_contact_seconds": {"type": "integer", "minimum": 0, "description": "Estimated time-to-contact in seconds."},
                    "buyer_reference": {"type": "string", "description": "Buyer's internal tracking reference."},
                    "reject_reason": {"type": "string", "description": "Why rejected (optional)."},
                },
                "required": ["ping_id", "decision"],
            },
        ),
    ]


# ─── Tool call handler ───────────────────────────────────────────────────────

async def _call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    client = LCPClient()

    if name == "submit_lead":
        receiver_id = arguments.get("receiver_id", "")
        payload = arguments.get("payload", {})
        envelope = _make_envelope("lead", client.sender_id, receiver_id, payload)
        result = client.submit_lead(envelope)
        return _tool_result(result)

    elif name == "submit_call":
        receiver_id = arguments.get("receiver_id", "")
        payload = arguments.get("payload", {})
        envelope = _make_envelope("call", client.sender_id, receiver_id, payload)
        result = client.submit_call(envelope)
        return _tool_result(result)

    elif name == "query_lead_status":
        lead_id = arguments.get("lead_id", "")
        result = client.query_lead_status(lead_id)
        return _tool_result(result)

    elif name == "get_schema":
        schema_name = arguments.get("name", "")
        # Try the REST endpoint first
        try:
            result = client.get_schema(schema_name)
            if result.get("_ok"):
                return _tool_result(result)
        except Exception:
            pass  # Fall back to local repo
        # Fall back to local repo files
        schema = load_schema(schema_name)
        if schema:
            return _tool_result(schema)
        available = list_schemas()
        return _error_result(
            f"Schema '{schema_name}' not found. Available: {', '.join(available)}",
            code="LCP-007",
        )

    elif name == "get_capabilities":
        result = client.get_capabilities()
        return _tool_result(result)

    elif name == "list_offers":
        vertical = arguments.get("vertical")
        result = client.list_offers(vertical)
        return _tool_result(result)

    elif name == "submit_bid":
        ping_id = arguments.get("ping_id", "")
        decision = arguments.get("decision", "pass")
        bid_price = arguments.get("bid_price_cents", 0)
        currency = arguments.get("currency", "USD")
        payload = {
            "ping_id": ping_id,
            "decision": decision,
            "bid_price_cents": bid_price,
            "currency": currency,
        }
        for opt_field in ["estimated_contact_seconds", "buyer_reference", "reject_reason"]:
            if opt_field in arguments:
                payload[opt_field] = arguments[opt_field]
        receiver_id = "platform"  # bids go to the platform, not a buyer
        envelope = _make_envelope("bid", client.sender_id, receiver_id, payload)
        result = client.post("/v1/lcp/bids", envelope)
        return _tool_result(result)

    else:
        return _error_result(f"Unknown tool: {name}", code="LCP-006")


# ─── MCP SDK compatibility ───────────────────────────────────────────────────

# MCP 1.x exposed decorator methods on Server; MCP 2.x accepts callbacks in
# the constructor. Keep the adapter compatible with both supported API shapes
# while the dependency range transitions to the 2.x server implementation.
if hasattr(Server, "list_tools"):
    legacy_app = Server("lcp-mcp-server")
    list_tools = legacy_app.list_tools()(_list_tools)
    call_tool = legacy_app.call_tool()(_call_tool)
    app = legacy_app
else:
    async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=await _list_tools())

    async def call_tool(_context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        content = await _call_tool(params.name, params.arguments or {})
        return types.CallToolResult(content=content)

    app = Server(
        "lcp-mcp-server",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


# ─── Entrypoint ──────────────────────────────────────────────────────────────

async def _run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    """Entrypoint for the lcp-mcp-server command."""
    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()
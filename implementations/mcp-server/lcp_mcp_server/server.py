"""LCP MCP Server — thin adapter wrapping LCP REST endpoints as MCP tools.

Tools map 1:1 to LCP REST endpoints per SPEC.md §10:
  submit_lead       -> POST /v1/lcp/leads
  submit_leads_batch-> POST /v1/lcp/leads/batch
  submit_call       -> POST /v1/lcp/calls
  query_lead_status -> GET  /v1/lcp/leads/{lead_id}
  get_schema        -> GET  /v1/lcp/schemas/{name}  (falls back to local repo)
  get_capabilities  -> GET  /v1/lcp/capabilities
  list_offers       -> GET  /v1/lcp/offers

The server holds no protocol state: the compiled schema validator and the HTTP
client are process-wide singletons, rebuilt only when their configuration
changes, so tool calls neither re-read every schema nor rebuild client config
per request. Upstream HTTP is blocking, so every network call runs in a worker
thread and cannot stall the event loop for other tool calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any
from uuid import uuid4

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from lcp_sdk import build_envelope
from lcp_sdk.validation import SchemaValidator, ValidationError

from .client import LCPClient
from .schema_loader import list_schemas, load_schema

app: Server


def _platform_receiver_id() -> str | None:
    """Return the explicitly configured platform receiver for bid submissions."""
    receiver_id = os.environ.get("LCP_PLATFORM_ID", "").strip()
    return receiver_id or None


# ─── Shared validator and HTTP client ────────────────────────────────────────

# Both are process-wide and created on first use. Reset paths:
#   reset_validator() — the schema set changed (e.g. LCP_SCHEMA_DIR moved).
#   reset_client()    — the next call rebuilds; used on credential rotation.
#   close_client()    — shutdown and tests; drops the cached client and closes
#                       it so nothing outlives the server.
_ENV_CONFIG_KEYS = (
    "LCP_ENDPOINT",
    "LCP_SENDER_ID",
    "LCP_API_KEY",
    "LCP_HMAC_SECRET",
)

_state_lock = threading.Lock()
_validator: SchemaValidator | None = None
_shared_client: LCPClient | None = None
_shared_client_key: tuple[str | None, ...] | None = None


def _env_config_key() -> tuple[str | None, ...]:
    """Fingerprint the env-driven client configuration."""
    return tuple(os.environ.get(key) for key in _ENV_CONFIG_KEYS)


def _get_validator() -> SchemaValidator:
    """Return the compiled validator, building it once per process."""
    global _validator
    with _state_lock:
        if _validator is None:
            _validator = SchemaValidator()
        return _validator


def reset_validator() -> None:
    """Forget the compiled validator so the next call rebuilds it."""
    global _validator
    with _state_lock:
        _validator = None


def _get_client() -> LCPClient:
    """Return the shared client, rebuilding it when its env config changed."""
    global _shared_client, _shared_client_key
    key = _env_config_key()
    with _state_lock:
        if _shared_client is None or key != _shared_client_key:
            # A superseded client may still serve an in-flight worker thread;
            # it owns no process-wide resource, so dropping the reference is
            # enough and interrupting it would fail that call instead.
            _shared_client = LCPClient()
            _shared_client_key = key
        return _shared_client


def reset_client() -> None:
    """Forget the shared client; the next tool call rebuilds it."""
    global _shared_client, _shared_client_key
    with _state_lock:
        _shared_client = None
        _shared_client_key = None


def close_client() -> None:
    """Close and forget the shared client. Safe to call when none exists."""
    global _shared_client, _shared_client_key
    with _state_lock:
        client, _shared_client = _shared_client, None
        _shared_client_key = None
    if client is not None:
        client.close()


def _tool_result(data: dict[str, Any]) -> list[types.TextContent]:
    """Format a dict as an MCP text content response."""
    return [types.TextContent(type="text", text=json.dumps(data, indent=2))]


def _validated_envelope(
    msg_type: str,
    sender_id: str | None,
    receiver_id: str,
    payload: Any,
    *,
    version: str = "1.0.0",
) -> tuple[dict[str, Any] | None, list[types.TextContent] | None]:
    """Build and locally validate a tool-created envelope before it is sent.

    Returns ``(envelope, None)`` on success or ``(None, error_result)`` so the
    tool handler can return the error without ever reaching the network.
    """
    if not isinstance(payload, dict):
        return None, _error_result("Tool payload must be a JSON object", code="LCP-100")
    envelope = build_envelope(
        msg_type, sender_id or "", receiver_id, payload, version=version
    )
    try:
        _get_validator().require_valid_envelope(envelope)
    except ValidationError as exc:
        return None, _error_result(
            f"Tool payload failed local LCP validation: {'; '.join(exc.errors[:3])}",
            code="LCP-100",
        )
    return envelope, None


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
                    "receiver_id": {
                        "type": "string",
                        "description": "LCP receiver ID (e.g. 'spx').",
                    },
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
                    "receiver_id": {
                        "type": "string",
                        "description": "LCP receiver ID.",
                    },
                    "payload": {
                        "type": "object",
                        "description": "Call payload — call block (call_id, status, durations, recording, etc.), consumer, location, compliance, attributes. See LCP call schema.",
                    },
                },
                "required": ["receiver_id", "payload"],
            },
        ),
        types.Tool(
            name="submit_leads_batch",
            description=(
                "Submit up to 100 leads in one bounded batch (LEP-0001 / W2-03). "
                "Each item is an ordinary lead payload with its own optional "
                "idempotency_key; replaying an item returns its original result. "
                "The request is the LEP batch message — request_id plus items of "
                "{item_id, idempotency_key, payload} — and the platform answers "
                "207 with {request_id, summary, results}, one receipt per item "
                "in input order (item_id plus accepted/duplicate/rejected/error, "
                "lead_id, and error when failed) — one item's failure never "
                "fails its siblings, so retry only the failed items. The batch "
                "is refused whole (413) above 100 items or a 1 MiB body."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "receiver_id": {
                        "type": "string",
                        "description": "LCP receiver ID of the platform.",
                    },
                    "request_id": {
                        "type": "string",
                        "maxLength": 64,
                        "description": "Optional request identifier echoed in the response (generated when omitted).",
                    },
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "description": "Lead items, processed in the order given.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_id": {
                                    "type": "string",
                                    "maxLength": 64,
                                    "description": "Optional stable item identifier echoed in that item's receipt (generated when omitted).",
                                },
                                "payload": {
                                    "type": "object",
                                    "description": "Lead payload (consumer, location, compliance, attributes, channel, etc.). See the LCP lead schema.",
                                },
                                "idempotency_key": {
                                    "type": "string",
                                    "maxLength": 128,
                                    "description": "Per-item idempotency key. Replaying the same key returns the item's original receipt.",
                                },
                            },
                            "required": ["payload"],
                        },
                    },
                },
                "required": ["receiver_id", "items"],
            },
        ),
        types.Tool(
            name="subscribe_to_events",
            description=(
                "Register a tenant-scoped event subscription (LEP-0001 W3). "
                "event_types is an explicit allowlist drawn from the platform's "
                "capabilities().events — no wildcard exists. callback_url must "
                "pass the SSRF rule (public resolved address; http only in a "
                "sandbox). filter is a closed key set of core enums/ids "
                "(event_type, message_type, vertical, status, lead_id); consumer "
                "paths are refused by name. The response carries the "
                "per-subscription HMAC secret exactly once — deliveries are "
                "signed over <timestamp>\\n<event_id>\\n<body>. replay_from "
                "replays a cursor from GET /v1/lcp/events. The subscription "
                "feature must be advertised as `event_subscriptions`."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "receiver_id": {
                        "type": "string",
                        "description": "LCP receiver ID of the platform.",
                    },
                    "event_types": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "description": "An event name advertised in capabilities().events.",
                        },
                        "description": "Explicit event allowlist; unadvertised names are refused.",
                    },
                    "callback_url": {
                        "type": "string",
                        "description": "HTTPS callback URL. Public resolved addresses only.",
                    },
                    "filter": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "event_type": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ]
                            },
                            "message_type": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ]
                            },
                            "vertical": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ]
                            },
                            "status": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ]
                            },
                            "lead_id": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ]
                            },
                        },
                        "description": "Privacy-safe filter over core enums/ids only.",
                    },
                    "replay_from": {
                        "type": "string",
                        "description": "Opaque cursor from GET /v1/lcp/events; replays that event set deterministically.",
                    },
                },
                "required": ["receiver_id", "event_types", "callback_url"],
            },
        ),
        types.Tool(
            name="query_lead_status",
            description="Query the status and lifecycle of a previously submitted lead.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lead_id": {
                        "type": "string",
                        "description": "The lead ID to query.",
                    },
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
            name="get_request_history",
            description=(
                "Query the LCP request history (LEP-0005): paginated audit rows for "
                "requests this credential sent or received. Filters: request_id, "
                "lead_id, message_type, status, vertical, caller_phone_hash "
                "(per-pair HMAC), from/to (whole-second ISO timestamps), limit "
                "(1-200, default 50), cursor. Rows carry metadata only — never raw "
                "envelope bodies or plaintext contacts. Pagination is returned in "
                "the result's `pagination` object (MCP results have no headers)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "Exact message id (UUID).",
                    },
                    "lead_id": {
                        "type": "string",
                        "description": "Publisher record handle.",
                    },
                    "message_type": {
                        "type": "string",
                        "enum": ["lead", "call", "ping", "bid", "post", "ack", "event"],
                        "description": "'ack' is a legal filter with no stored rows in v1.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Open status vocabulary (§6).",
                    },
                    "vertical": {"type": "string"},
                    "caller_phone_hash": {
                        "type": "string",
                        "description": "64-hex per-pair HMAC of the caller's phone (never plaintext).",
                    },
                    "from": {
                        "type": "string",
                        "description": "Inclusive lower bound, YYYY-MM-DDTHH:MM:SSZ.",
                    },
                    "to": {
                        "type": "string",
                        "description": "Inclusive upper bound, YYYY-MM-DDTHH:MM:SSZ.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    },
                    "cursor": {
                        "type": "string",
                        "description": "Opaque cursor from a previous page.",
                    },
                },
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
                    "ping_id": {
                        "type": "string",
                        "description": "ID of the ping being responded to.",
                    },
                    "decision": {
                        "type": "string",
                        "enum": ["accept", "reject", "pass"],
                        "description": "accept = bid for the lead; reject = do not want; pass = decline to bid.",
                    },
                    "bid_price_cents": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Bid price in cents. Required when decision = accept.",
                    },
                    "currency": {
                        "type": "string",
                        "pattern": "^[A-Z]{3}$",
                        "description": "ISO 4217 currency code the bid is expressed in (e.g. AUD, USD). Required when decision = accept.",
                    },
                    "fx_rate": {
                        "type": "string",
                        "pattern": "^(?:[1-9][0-9]*\\.[0-9]{6,12}|0\\.[0-9]*[1-9][0-9]{5,11})$",
                        "description": "LEP-0010: native major units per 1 settlement (offer-currency) major unit. Required when currency differs from the offer's currency; inert on a same-currency bid. Plain decimal, 6-12 fractional digits, no exponent.",
                    },
                    "fx_source": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 64,
                        "description": "Named rate provider, or 'bilateral'. Required with fx_rate.",
                    },
                    "fx_timestamp": {
                        "type": "string",
                        "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
                        "description": "Canonical whole-second UTC instant the rate was observed. Required with fx_rate.",
                    },
                    "fx_expires_at": {
                        "type": "string",
                        "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
                        "description": "Canonical whole-second UTC end of the rate's validity; strictly later than fx_timestamp. Required with fx_rate.",
                    },
                    "estimated_contact_seconds": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Estimated time-to-contact in seconds.",
                    },
                    "buyer_reference": {
                        "type": "string",
                        "description": "Buyer's internal tracking reference.",
                    },
                    "reject_reason": {
                        "type": "string",
                        "description": "Why rejected (optional).",
                    },
                    "slots_claimed": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1,
                        "description": "v1.1 (LEP-0003 §2): buyer slots claimed if selected — single-slot only at v1.1; send only when the platform advertises a 1.x minor >= 1 element in lcp_versions (SPEC.md §8 capability gate).",
                    },
                },
                "required": ["ping_id", "decision"],
            },
        ),
        types.Tool(
            name="send_quote",
            description=(
                "Open a bounded agent-to-agent price negotiation (LEP-0009) for a "
                "ping this platform already sent. Platform-role tool: the platform "
                "states its ask, the buyer counters/accepts/refuses. Payload is "
                "ping-safe (integer minor units, ISO 4217 currency, ids, round "
                "counters, timestamps) with no consumer PII and no free text. "
                "Requires the platform to advertise feature `negotiation`."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "receiver_id": {
                        "type": "string",
                        "description": "Buyer that was pinged.",
                    },
                    "ping_id": {
                        "type": "string",
                        "description": "The ping this negotiation prices.",
                    },
                    "offer_id": {
                        "type": "string",
                        "description": "Must equal the ping's offer_id.",
                    },
                    "negotiation_id": {
                        "type": "string",
                        "pattern": "^neg_[A-Za-z0-9_-]{8,64}$",
                        "description": "Opaque negotiation id (generated when omitted).",
                    },
                    "price_cents": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "The ask in minor units (>= floor_price_cents).",
                    },
                    "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                    "floor_price_cents": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Restated from the ping.",
                    },
                    "max_rounds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "default": 3,
                    },
                    "rounds_remaining": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 5,
                        "description": "Defaults to max_rounds on the opening quote.",
                    },
                    "expires_at": {
                        "type": "string",
                        "description": "Absolute RFC 3339 UTC end, not later than the ping's expires_at.",
                    },
                    "correlation_id": {
                        "type": "string",
                        "description": "message.id of the ping being priced.",
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": "Stable key; a replay returns the original ack.",
                    },
                },
                "required": [
                    "receiver_id",
                    "ping_id",
                    "offer_id",
                    "price_cents",
                    "currency",
                    "floor_price_cents",
                    "expires_at",
                    "correlation_id",
                ],
            },
        ),
        types.Tool(
            name="send_counter",
            description=(
                "Answer the standing negotiation message (LEP-0009): decision "
                "`counter` moves the price within the monotone bounds, `accept` "
                "closes at the answered price and returns a single-use binding "
                "token, `refuse` closes with a reason. Buyer- or platform-role; "
                "`decision` is explicit so an agent cannot counter without stating "
                "a price."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "receiver_id": {
                        "type": "string",
                        "description": "The other party (defaults to LCP_PLATFORM_ID when the buyer is calling).",
                    },
                    "negotiation_id": {
                        "type": "string",
                        "pattern": "^neg_[A-Za-z0-9_-]{8,64}$",
                    },
                    "ping_id": {"type": "string"},
                    "offer_id": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["counter", "accept", "refuse"],
                    },
                    "price_cents": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Required unless decision = refuse; on accept it must equal the answered price.",
                    },
                    "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                    "floor_price_cents": {"type": "integer", "minimum": 0},
                    "rounds_remaining": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 5,
                        "description": "Your view before this message is applied; a stale echo is refused.",
                    },
                    "expires_at": {
                        "type": "string",
                        "description": "The negotiation's absolute end, unchanged.",
                    },
                    "rejection_reason": {
                        "type": "string",
                        "description": "Required when decision = refuse (open enum, e.g. below_floor_price, negotiation_closed).",
                    },
                    "correlation_id": {
                        "type": "string",
                        "description": "message.id of the message being answered.",
                    },
                    "idempotency_key": {"type": "string"},
                },
                "required": [
                    "negotiation_id",
                    "ping_id",
                    "offer_id",
                    "decision",
                    "currency",
                    "floor_price_cents",
                    "rounds_remaining",
                    "expires_at",
                    "correlation_id",
                ],
            },
        ),
        types.Tool(
            name="redeem_negotiation_token",
            description=(
                "Redeem a LEP-0009 binding token on the bid path: submit a bid with "
                "decision accept and the single-use token in place of "
                "bid_price_cents. First redemption creates the bid; replaying the "
                "same envelope returns the original acknowledgement and creates no "
                "second bid."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ping_id": {
                        "type": "string",
                        "description": "Must equal the negotiation's ping.",
                    },
                    "negotiation_binding_token": {
                        "type": "string",
                        "pattern": "^tok_[A-Za-z0-9_-]{32,128}$",
                    },
                    "correlation_id": {
                        "type": "string",
                        "description": "message.id of the ping.",
                    },
                    "bid_price_cents": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional; when present it must equal the token price.",
                    },
                    "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                    "estimated_contact_seconds": {"type": "integer", "minimum": 0},
                },
                "required": ["ping_id", "negotiation_binding_token", "correlation_id"],
            },
        ),
    ]


def _fetch_remote_schema(client: LCPClient, name: str) -> dict[str, Any] | None:
    """Fetch a schema in a worker thread; transport errors mean "unreachable"."""
    try:
        return client.get_schema(name)
    except Exception:
        return None  # transport error -> local fallback


async def _call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    client = _get_client()

    if name == "submit_lead":
        receiver_id = arguments.get("receiver_id", "")
        payload = arguments.get("payload", {})
        envelope, error = _validated_envelope(
            "lead", client.sender_id, receiver_id, payload
        )
        if error is not None:
            return error
        result = await asyncio.to_thread(client.submit_lead, envelope)
        return _tool_result(result)

    elif name == "submit_call":
        receiver_id = arguments.get("receiver_id", "")
        payload = arguments.get("payload", {})
        envelope, error = _validated_envelope(
            "call", client.sender_id, receiver_id, payload
        )
        if error is not None:
            return error
        result = await asyncio.to_thread(client.submit_call, envelope)
        return _tool_result(result)

    elif name == "submit_leads_batch":
        # LEP-0001 W2-03: same contract as POST /v1/lcp/leads/batch, per-item
        # receipts in input order. The adapter stays thin: it emits the LEP
        # wire shape ({request_id, items: [{item_id, idempotency_key,
        # payload: <lead payload>}]}) and returns the platform's receipts
        # verbatim — local per-item validation would fork the receipt
        # vocabulary and could never reflect a replay's original result.
        # item_id and idempotency_key are generated when the caller omits
        # them, so the tool's input stays ergonomic without inventing a
        # second wire contract.
        receiver_id = arguments.get("receiver_id", "")
        items = arguments.get("items")
        if not isinstance(items, list) or not items:
            return _error_result(
                "items must be a non-empty array of lead items", code="LCP-100"
            )
        batch = build_envelope(
            "batch",
            client.sender_id or "",
            receiver_id,
            {"request_id": "", "items": []},
            version="1.2.0",
        )
        request_id = arguments.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            request_id = f"batch-{uuid4().hex[:12]}"
        payload_items = []
        for index, item in enumerate(items):
            data = item if isinstance(item, dict) else {}
            item_id = data.get("item_id")
            if not isinstance(item_id, str) or not item_id:
                item_id = f"item-{index + 1:03d}"
            key = data.get("idempotency_key")
            if not isinstance(key, str) or not key:
                # request_id + item_id is stable across retries of the same
                # logical submission and stays inside the 128-char wire cap.
                key = f"{request_id}/{item_id}"[:128]
            payload = data.get("payload")
            payload_items.append(
                {
                    "item_id": item_id,
                    "idempotency_key": key,
                    # Keep the receipt list aligned with the input: an
                    # unusable entry becomes an item the platform rejects,
                    # not a shifted array the caller cannot map back.
                    "payload": payload if isinstance(payload, dict) else {},
                }
            )
        batch["lcp"]["payload"] = {
            "request_id": request_id,
            "items": payload_items,
        }
        result = await asyncio.to_thread(client.post, "/v1/lcp/leads/batch", batch)
        return _tool_result(result)

    elif name == "subscribe_to_events":
        # LEP-0001 W3-05: thin adapter over POST /v1/lcp/events/subscribe.
        # The platform owns allowlist, SSRF, filter, and tenant checks; the
        # adapter only assembles the documented payload and returns the
        # response (including the secret, which is returned exactly once).
        receiver_id = arguments.get("receiver_id", "")
        subscription_payload: dict[str, Any] = {
            "event_types": arguments.get("event_types"),
            "callback_url": arguments.get("callback_url"),
        }
        filter_values = arguments.get("filter")
        if isinstance(filter_values, dict):
            subscription_payload["filter"] = filter_values
        replay_from = arguments.get("replay_from")
        if isinstance(replay_from, str) and replay_from:
            subscription_payload["replay_from"] = replay_from
        envelope, error = _validated_envelope(
            "subscription",
            client.sender_id,
            receiver_id,
            subscription_payload,
            version="1.2.0",
        )
        if error is not None:
            return error
        assert envelope is not None
        result = await asyncio.to_thread(
            client.post, "/v1/lcp/events/subscribe", envelope
        )
        return _tool_result(result)

    elif name == "query_lead_status":
        lead_id = arguments.get("lead_id", "")
        result = await asyncio.to_thread(client.query_lead_status, lead_id)
        return _tool_result(result)

    elif name == "get_schema":
        schema_name = arguments.get("name", "")
        # Try the REST endpoint first. Fall back to local repo files only when
        # the endpoint is unreachable; when it answers (any HTTP status), its
        # error is surfaced instead of being masked by the local fallback.
        result = await asyncio.to_thread(_fetch_remote_schema, client, schema_name)
        if result is not None:
            if result.get("_ok"):
                return _tool_result(result)
            status = result.get("_status_code")
            body_text = str(result.get("error") or result.get("raw") or result)[:300]
            return _error_result(
                f"LCP endpoint rejected schema request (HTTP {status}): {body_text}",
                code=str(result.get("code") or "LCP-500"),
            )
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
        result = await asyncio.to_thread(client.get_capabilities)
        return _tool_result(result)

    elif name == "get_request_history":
        result = await asyncio.to_thread(
            client.get_request_history,
            request_id=arguments.get("request_id"),
            lead_id=arguments.get("lead_id"),
            message_type=arguments.get("message_type"),
            status=arguments.get("status"),
            vertical=arguments.get("vertical"),
            caller_phone_hash=arguments.get("caller_phone_hash"),
            from_=arguments.get("from"),
            to=arguments.get("to"),
            limit=arguments.get("limit"),
            cursor=arguments.get("cursor"),
        )
        return _tool_result(result)

    elif name == "list_offers":
        vertical = arguments.get("vertical")
        result = await asyncio.to_thread(client.list_offers, vertical)
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
        for opt_field in [
            "estimated_contact_seconds",
            "buyer_reference",
            "reject_reason",
            "slots_claimed",
            # LEP-0010 §3: the bid-side FX bundle rides the bid payload.
            "fx_rate",
            "fx_source",
            "fx_timestamp",
            "fx_expires_at",
        ]:
            if opt_field in arguments:
                payload[opt_field] = arguments[opt_field]
        receiver_id = _platform_receiver_id()  # bids go to the platform, not a buyer
        if receiver_id is None:
            return _error_result(
                "LCP_PLATFORM_ID is required for bid submission",
                code="LCP-100",
            )
        envelope, error = _validated_envelope(
            "bid", client.sender_id, receiver_id, payload
        )
        if error is not None:
            return error
        result = await asyncio.to_thread(client.post, "/v1/lcp/bids", envelope)
        return _tool_result(result)

    elif name == "send_quote":
        receiver_id = arguments.get("receiver_id")
        if not isinstance(receiver_id, str) or not receiver_id:
            return _error_result("receiver_id is required", code="LCP-100")
        max_rounds = arguments.get("max_rounds", 3)
        payload = {
            "negotiation_id": arguments.get("negotiation_id") or f"neg_{uuid4().hex}",
            "ping_id": arguments.get("ping_id", ""),
            "offer_id": arguments.get("offer_id", ""),
            "price_cents": arguments.get("price_cents"),
            "currency": arguments.get("currency", "USD"),
            "floor_price_cents": arguments.get("floor_price_cents"),
            "max_rounds": max_rounds,
            "rounds_remaining": arguments.get("rounds_remaining", max_rounds),
            "expires_at": arguments.get("expires_at", ""),
        }
        envelope = build_envelope(
            "quote",
            client.sender_id or "",
            receiver_id,
            payload,
            version="1.2.0",
            correlation_id=arguments.get("correlation_id"),
            idempotency_key=arguments.get("idempotency_key"),
        )
        errors = _get_validator().validate_envelope(envelope)
        if errors:
            return _error_result(
                f"Quote failed local LCP validation: {'; '.join(errors[:3])}",
                code="LCP-100",
            )
        result = await asyncio.to_thread(client.post, "/v1/lcp/quotes", envelope)
        return _tool_result(result)

    elif name == "send_counter":
        receiver_id = arguments.get("receiver_id") or _platform_receiver_id()
        if not isinstance(receiver_id, str) or not receiver_id:
            return _error_result(
                "receiver_id or LCP_PLATFORM_ID is required for a counter",
                code="LCP-100",
            )
        payload = {
            "negotiation_id": arguments.get("negotiation_id", ""),
            "ping_id": arguments.get("ping_id", ""),
            "offer_id": arguments.get("offer_id", ""),
            "decision": arguments.get("decision", ""),
            "currency": arguments.get("currency", "USD"),
            "floor_price_cents": arguments.get("floor_price_cents"),
            "rounds_remaining": arguments.get("rounds_remaining"),
            "expires_at": arguments.get("expires_at", ""),
        }
        for optional in ("price_cents", "rejection_reason"):
            if optional in arguments:
                payload[optional] = arguments[optional]
        envelope = build_envelope(
            "counter",
            client.sender_id or "",
            receiver_id,
            payload,
            version="1.2.0",
            correlation_id=arguments.get("correlation_id"),
            idempotency_key=arguments.get("idempotency_key"),
        )
        errors = _get_validator().validate_envelope(envelope)
        if errors:
            return _error_result(
                f"Counter failed local LCP validation: {'; '.join(errors[:3])}",
                code="LCP-100",
            )
        result = await asyncio.to_thread(client.post, "/v1/lcp/counters", envelope)
        return _tool_result(result)

    elif name == "redeem_negotiation_token":
        receiver_id = _platform_receiver_id()
        if receiver_id is None:
            return _error_result(
                "LCP_PLATFORM_ID is required for token redemption",
                code="LCP-100",
            )
        payload = {
            "ping_id": arguments.get("ping_id", ""),
            "decision": "accept",
            "negotiation_binding_token": arguments.get("negotiation_binding_token", ""),
        }
        for optional in ("bid_price_cents", "currency", "estimated_contact_seconds"):
            if optional in arguments:
                payload[optional] = arguments[optional]
        envelope = build_envelope(
            "bid",
            client.sender_id or "",
            receiver_id,
            payload,
            version="1.2.0",
            correlation_id=arguments.get("correlation_id"),
            idempotency_key=arguments.get("idempotency_key"),
        )
        errors = _get_validator().validate_envelope(envelope)
        if errors:
            return _error_result(
                f"Redemption failed local LCP validation: {'; '.join(errors[:3])}",
                code="LCP-100",
            )
        result = await asyncio.to_thread(client.post, "/v1/lcp/bids", envelope)
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

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        content = await _call_tool(params.name, params.arguments or {})
        return types.CallToolResult(content=content)

    app = Server(
        "lcp-mcp-server",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


# ─── Entrypoint ──────────────────────────────────────────────────────────────


async def _run() -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream, write_stream, app.create_initialization_options()
            )
    finally:
        close_client()


def main() -> None:
    """Entrypoint for the lcp-mcp-server command."""
    import asyncio

    asyncio.run(_run())


if __name__ == "__main__":
    main()

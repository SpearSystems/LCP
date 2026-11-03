"""SPEC-COVERAGE MCP-02 / MCP-03 gap closure for the reference MCP adapter.

MCP-02 — SPEC.md §10 "Binding rules (abstract)", verbatim (SPEC @
5d6000dbc0d70bccb0ed8ea62472c350d29755b0):

    "Capability discovery → tool list.
     JSON Schema → tool input schema."

and §10 "MCP binding (first concrete binding)":

    "The official reference MCP server (see implementations/mcp-server/)
     exposes: ``submit_lead``, ``submit_call``, ``submit_bid``,
     ``query_lead_status``, ``get_schema``, ``get_capabilities``,
     ``list_offers``."

The discovery half of the mapping is §8, verbatim:

    "`GET /v1/lcp/capabilities` → ``{ lcp_versions, message_types,
    verticals[{id, schema_version}], countries, auth_methods, events,
    conformance_level, delivery_windows[], capacity }``."

    "Version-string membership (v1.1, LEP-0003 F6, normative). Every
    ``lcp_versions`` element MUST be a full ``major.minor.patch`` semantic
    version (``^\\d+\\.\\d+\\.\\d+$``)."

MCP-03 — SPEC.md §10 "Agent identity", verbatim:

    "``provenance.agent{agent_id, acting_for, attestation}`` — first-class
    concept. The MCP binding maps this to MCP identity; other bindings
    (A2A, future) map it the same way."

and §3 (provenance table): ``agent_id``/``acting_for``/``attestation`` are
plain strings, the attestation being a "Signed verification token (JWT
recommended — see §9)" that the binding layer must not rewrite.

All tests stub the SDK transport client (``lcp_sdk.LCPClient.request``) so
no network is touched — the same interception pattern as ``test_mcp.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from lcp_sdk import LCPClient as SDKLCPClient


ROOT = Path(__file__).resolve().parents[3]

FULL_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _run_tool(name: str, arguments: dict) -> list:
    """Invoke the async tool handler from a synchronous test."""
    from lcp_mcp_server import server as server_module

    return asyncio.run(server_module._call_tool(name, arguments))


def _tool_input_schema(tool) -> dict:
    """Return a declared tool input schema across both supported MCP shapes.

    The supported dependency range (``mcp>=1.28.1,<2.4.0``) renamed the
    field attribute from ``inputSchema`` (1.x) to ``input_schema`` (2.x,
    where ``inputSchema`` survives only as the wire alias).
    """
    if hasattr(tool, "input_schema"):
        return tool.input_schema
    return tool.inputSchema


# The §8 discovery document exactly as the reference platform lands it
# (implementations/reference-platform/lcp_platform/router.py capabilities():
# full-semver ``lcp_versions`` with the 1.1.0 gate element, CONTACTED in
# ``events``), extended with the optional §8 members (delivery_windows,
# capacity) the OpenAPI ``Capabilities`` component also documents, so the
# adapter is pinned to preserve every advertised field, not just the
# always-present ones.
PLATFORM_DISCOVERY = {
    "lcp_versions": ["1.0.0", "1.1.0"],
    "message_types": ["lead", "call", "ping", "bid", "post", "ack", "event"],
    "verticals": [
        {"id": "insurance", "schema_version": "1.0.0"},
        {"id": "legal", "schema_version": "1.0.0"},
        {"id": "mortgage", "schema_version": "1.0.0"},
        {"id": "mva", "schema_version": "1.0.0"},
        {"id": "solar", "schema_version": "1.0.0"},
    ],
    "countries": ["AU", "US"],
    "auth_methods": ["bearer", "hmac"],
    "events": [
        "DELIVERED",
        "ACCEPTED",
        "REJECTED",
        "DISPUTED",
        "REFUNDED",
        "EXPIRED",
        "CONVERTED",
        "ARCHIVED",
        "CALL_OFFERED",
        "CALL_CONNECTED",
        "CALL_ENDED",
        "CALL_OUTCOME",
        "CONTACTED",
    ],
    "delivery_windows": [
        {
            "timezone": "Australia/Sydney",
            "available_from": "08:00",
            "available_to": "20:00",
            "days": ["mon", "tue", "wed", "thu", "fri"],
        }
    ],
    "capacity": {
        "daily_cap": 500,
        "daily_remaining": 120,
        "hourly_cap": 80,
        "hourly_remaining": 30,
        "concurrent_call_cap": 10,
        "reset_at": "2026-11-04T00:00:00Z",
    },
    "conformance_level": "L3",
}

AGENT_STUB_ENV = {
    "LCP_ENDPOINT": "http://platform.stub.test",
    "LCP_SENDER_ID": "pub_001",
}


@unittest.skipUnless(
    importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
)
class CapabilityDiscoveryMappingTests(unittest.TestCase):
    """MCP-02: capability discovery → tool list; schemas → tool input schemas."""

    def test_get_capabilities_maps_discovery_document_to_tool_result_verbatim(
        self,
    ) -> None:
        # §8: GET /v1/lcp/capabilities → { lcp_versions, message_types,
        # verticals[{id, schema_version}], countries, auth_methods, events,
        # conformance_level, delivery_windows[], capacity }. The adapter's
        # get_capabilities tool MUST surface that discovery document with
        # every field preserved — no dropped members, no rewritten versions.
        with (
            patch.dict(os.environ, AGENT_STUB_ENV),
            patch.object(
                SDKLCPClient, "request", return_value=dict(PLATFORM_DISCOVERY)
            ) as request,
        ):
            result = _run_tool("get_capabilities", {})

        request.assert_called_once_with("GET", "/v1/lcp/capabilities", params=None)
        doc = json.loads(result[0].text)
        for field, value in PLATFORM_DISCOVERY.items():
            self.assertEqual(
                doc.get(field), value, f"§8 field {field} not preserved verbatim"
            )

        # §8 version-string membership (LEP-0003 F6): every element is a
        # FULL major.minor.patch semver, and the landed 1.1.0 gate element
        # survives — the adapter must not normalize to "1.1" literals,
        # which the same section declares unsatisfiable.
        versions = doc["lcp_versions"]
        self.assertEqual(versions, ["1.0.0", "1.1.0"])
        for version in versions:
            self.assertRegex(version, FULL_SEMVER)
        self.assertNotIn("1.1", versions)
        # A v1.1-or-higher minor element is present, i.e. the set the slot
        # fields and CONTACTED reporting gate on (§8 capability gate).
        self.assertTrue(
            any(
                major == "1" and int(minor) >= 1
                for major, minor, _patch in (v.split(".") for v in versions)
            )
        )

        # LEP-0003 §4 (F5/T6): the §8 events surface advertises CONTACTED.
        self.assertIn("CONTACTED", doc["events"])
        # verticals keep the {id, schema_version} pair shape.
        for vertical in doc["verticals"]:
            self.assertEqual(sorted(vertical), ["id", "schema_version"])

    def test_tools_list_exposes_binding_tools_with_json_input_schemas(self) -> None:
        # §10 binding rules: "Capability discovery → tool list.
        # JSON Schema → tool input schema." §10 names the concrete v1.0
        # tool list; every exposed tool carries a JSON Schema input
        # document (type: object) so agent-side schema mapping has a target.
        from lcp_mcp_server.server import _list_tools

        tools = asyncio.run(_list_tools())
        names = {tool.name for tool in tools}
        for required in (
            "submit_lead",
            "submit_call",
            "submit_bid",
            "query_lead_status",
            "get_schema",
            "get_capabilities",
            "list_offers",
        ):
            self.assertIn(required, names)
        for tool in tools:
            schema = _tool_input_schema(tool)
            self.assertIsInstance(schema, dict, tool.name)
            self.assertEqual(schema.get("type"), "object", tool.name)


@unittest.skipUnless(
    importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
)
class AgentProvenancePassthroughTests(unittest.TestCase):
    """MCP-03: provenance.agent maps to binding identity, verbatim."""

    def test_submit_lead_forwards_provenance_agent_verbatim_to_endpoint(self) -> None:
        # §10 Agent identity: "provenance.agent{agent_id, acting_for,
        # attestation} — first-class concept. The MCP binding maps this to
        # MCP identity". The agent block — including the signed attestation
        # token — must reach the outbound envelope untouched: any drop or
        # normalization breaks §9 agent attestation verification.
        agent = {
            "agent_id": "agent_mobile_assistant_01",
            "acting_for": "spx",
            "attestation": (
                "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9"
                ".eyJzdWIiOiJhZ2VudF9tb2JpbGVfYXNzaXN0YW50XzAxIiwiYWN0aW5nX2ZvciI6InNweCJ9"
                ".c2lnbmF0dXJlX292ZXJfaXNfc2lnbmlmaWNhbnQ"
            ),
        }
        lead = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))
        payload = lead["lcp"]["payload"]
        payload["provenance"]["source_type"] = "agent"
        payload["provenance"]["agent"] = dict(agent)

        with (
            patch.dict(os.environ, AGENT_STUB_ENV),
            patch.object(
                SDKLCPClient,
                "request",
                return_value={
                    "lead_id": payload["lead_id"],
                    "ack": {"status": "RECEIVED"},
                },
            ) as request,
        ):
            result = _run_tool(
                "submit_lead", {"receiver_id": "spx", "payload": payload}
            )

        # The envelope left through the SDK transport to the §10 lead
        # endpoint with the agent block intact.
        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", "/v1/lcp/leads"))
        envelope = kwargs["payload"]
        self.assertEqual(envelope["lcp"]["message"]["type"], "lead")
        self.assertEqual(envelope["lcp"]["message"]["sender_id"], "pub_001")
        outbound_provenance = envelope["lcp"]["payload"]["provenance"]
        self.assertEqual(outbound_provenance["agent"], agent)
        self.assertEqual(outbound_provenance["source_type"], "agent")
        # attestation is a signed token: byte-for-byte unchanged.
        self.assertEqual(
            outbound_provenance["agent"]["attestation"], agent["attestation"]
        )
        self.assertIn("RECEIVED", result[0].text)


if __name__ == "__main__":
    unittest.main()

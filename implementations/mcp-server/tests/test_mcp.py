from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


def _run_tool(name: str, arguments: dict) -> list:
    """Invoke the async tool handler from a synchronous test."""
    from lcp_mcp_server import server as server_module

    return asyncio.run(server_module._call_tool(name, arguments))

from lcp_mcp_server.schema_loader import list_schemas, load_schema


ROOT = Path(__file__).resolve().parents[3]


class McpAdapterTests(unittest.TestCase):
    def test_local_schema_loader_rejects_traversal_and_symlink_escape(self) -> None:
        self.assertIsNotNone(load_schema("lead"))
        self.assertIsNotNone(load_schema("verticals/mortgage"))
        for name in (
            "../../../../etc/passwd",
            "schemas/../../etc/passwd",
            "verticals/../../schemas/core",
            "/etc/passwd",
            r"..\\..\\etc\\passwd",
        ):
            self.assertIsNone(load_schema(name), name)

    def test_schema_listing_stays_within_canonical_directories(self) -> None:
        names = list_schemas()
        self.assertIn("lead", names)
        self.assertIn("verticals/mortgage", names)
        self.assertTrue(all(".." not in name and not name.startswith("/") for name in names))

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed")
    def test_bid_receiver_defaults_to_documented_platform_id(self) -> None:
        # Importing the MCP server is intentionally deferred because the MCP
        # dependency is optional for local schema-only tooling.
        from lcp_mcp_server.server import _platform_receiver_id

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_platform_receiver_id(), "platform_001")
        with patch.dict(os.environ, {"LCP_PLATFORM_ID": "sandbox_platform"}):
            self.assertEqual(_platform_receiver_id(), "sandbox_platform")

    def test_lead_status_path_encodes_lead_id(self) -> None:
        from lcp_mcp_server.client import LCPClient

        client = LCPClient(endpoint="http://example.test", sender_id="sender_001")
        with patch.object(client._client, "request", return_value={"ok": True}) as request:
            client.query_lead_status("lead/with?weird#chars")
        self.assertEqual(request.call_args.args[1], "/v1/lcp/leads/lead%2Fwith%3Fweird%23chars")

    def test_schema_path_encodes_segments_and_neutralizes_traversal(self) -> None:
        from lcp_mcp_server.client import LCPClient

        client = LCPClient(endpoint="http://example.test", sender_id="sender_001")
        with patch.object(client._client, "request", return_value={"ok": True}) as request:
            client.get_schema("verticals/mortgage")
        self.assertEqual(request.call_args.args[1], "/v1/lcp/schemas/verticals/mortgage")
        with patch.object(client._client, "request", return_value={"ok": True}) as request:
            client.get_schema("schemas/../core")
        self.assertEqual(request.call_args.args[1], "/v1/lcp/schemas/schemas/%2E%2E/core")
        with patch.object(client._client, "request", return_value={"ok": True}) as request:
            client.get_schema("lead?x=1")
        self.assertEqual(request.call_args.args[1], "/v1/lcp/schemas/lead%3Fx%3D1")

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed")
    def test_get_schema_surfaces_endpoint_errors_instead_of_local_fallback(self) -> None:
        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        with patch.object(
            LCPClient, "get_schema", return_value={"_ok": False, "_status_code": 401, "error": "unauthorized"}
        ), patch.object(server_module, "load_schema", side_effect=AssertionError("local fallback must not run")):
            result = _run_tool("get_schema", {"name": "lead"})
        text = result[0].text
        self.assertIn("401", text)
        self.assertIn("unauthorized", text)

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed")
    def test_get_schema_falls_back_to_local_only_when_endpoint_unreachable(self) -> None:
        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        with patch.object(LCPClient, "get_schema", side_effect=ConnectionError("down")), patch.object(
            server_module, "load_schema", return_value={"title": "LCP Lead Message"}
        ) as load:
            result = _run_tool("get_schema", {"name": "lead"})
        load.assert_called_once_with("lead")
        self.assertIn("LCP Lead Message", result[0].text)

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed")
    def test_malformed_tool_payload_is_rejected_before_network(self) -> None:
        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        with patch.object(LCPClient, "submit_lead", side_effect=AssertionError("network must not be reached")):
            result = _run_tool(
                "submit_lead",
                {"receiver_id": "platform_001", "payload": {"lead_id": "lead_x"}},
            )
        text = result[0].text
        self.assertIn("LCP-100", text)
        self.assertIn("local LCP validation", text)

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed")
    def test_valid_tool_payload_reaches_the_endpoint(self) -> None:
        import json

        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        lead = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))
        payload = lead["lcp"]["payload"]
        with patch.object(
            LCPClient, "submit_lead", return_value={"_ok": True, "_status_code": 200, "lcp": {"ack": True}}
        ) as submit:
            result = _run_tool(
                "submit_lead", {"receiver_id": "platform_001", "payload": payload}
            )
        submit.assert_called_once()
        self.assertIn("ack", result[0].text)


if __name__ == "__main__":
    unittest.main()

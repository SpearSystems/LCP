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
        self.assertTrue(
            all(".." not in name and not name.startswith("/") for name in names)
        )

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_bid_receiver_requires_explicit_platform_id(self) -> None:
        # Importing the MCP server is intentionally deferred because the MCP
        # dependency is optional for local schema-only tooling.
        from lcp_mcp_server.server import _platform_receiver_id

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_platform_receiver_id())
        with patch.dict(os.environ, {"LCP_PLATFORM_ID": "sandbox_platform"}):
            self.assertEqual(_platform_receiver_id(), "sandbox_platform")

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_bid_without_platform_id_is_rejected_before_network(self) -> None:
        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                LCPClient,
                "post",
                side_effect=AssertionError("network must not be reached"),
            ),
        ):
            result = _run_tool(
                "submit_bid",
                {"ping_id": "ping_001", "decision": "pass"},
            )
        self.assertIn("LCP-100", result[0].text)
        self.assertIn("LCP_PLATFORM_ID is required", result[0].text)

    def test_lead_status_path_encodes_lead_id(self) -> None:
        from lcp_mcp_server.client import LCPClient

        client = LCPClient(endpoint="http://example.test", sender_id="sender_001")
        with patch.object(
            client._client, "request", return_value={"ok": True}
        ) as request:
            client.query_lead_status("lead/with?weird#chars")
        self.assertEqual(
            request.call_args.args[1], "/v1/lcp/leads/lead%2Fwith%3Fweird%23chars"
        )

    def test_schema_path_encodes_segments_and_neutralizes_traversal(self) -> None:
        from lcp_mcp_server.client import LCPClient

        client = LCPClient(endpoint="http://example.test", sender_id="sender_001")
        with patch.object(
            client._client, "request", return_value={"ok": True}
        ) as request:
            client.get_schema("verticals/mortgage")
        self.assertEqual(
            request.call_args.args[1], "/v1/lcp/schemas/verticals/mortgage"
        )
        with patch.object(
            client._client, "request", return_value={"ok": True}
        ) as request:
            client.get_schema("schemas/../core")
        self.assertEqual(
            request.call_args.args[1], "/v1/lcp/schemas/schemas/%2E%2E/core"
        )
        with patch.object(
            client._client, "request", return_value={"ok": True}
        ) as request:
            client.get_schema("lead?x=1")
        self.assertEqual(request.call_args.args[1], "/v1/lcp/schemas/lead%3Fx%3D1")

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_get_schema_surfaces_endpoint_errors_instead_of_local_fallback(
        self,
    ) -> None:
        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        with (
            patch.object(
                LCPClient,
                "get_schema",
                return_value={
                    "_ok": False,
                    "_status_code": 401,
                    "error": "unauthorized",
                },
            ),
            patch.object(
                server_module,
                "load_schema",
                side_effect=AssertionError("local fallback must not run"),
            ),
        ):
            result = _run_tool("get_schema", {"name": "lead"})
        text = result[0].text
        self.assertIn("401", text)
        self.assertIn("unauthorized", text)

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_get_schema_falls_back_to_local_only_when_endpoint_unreachable(
        self,
    ) -> None:
        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        with (
            patch.object(LCPClient, "get_schema", side_effect=ConnectionError("down")),
            patch.object(
                server_module, "load_schema", return_value={"title": "LCP Lead Message"}
            ) as load,
        ):
            result = _run_tool("get_schema", {"name": "lead"})
        load.assert_called_once_with("lead")
        self.assertIn("LCP Lead Message", result[0].text)

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_malformed_tool_payload_is_rejected_before_network(self) -> None:
        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        with patch.object(
            LCPClient,
            "submit_lead",
            side_effect=AssertionError("network must not be reached"),
        ):
            result = _run_tool(
                "submit_lead",
                {"receiver_id": "platform_001", "payload": {"lead_id": "lead_x"}},
            )
        text = result[0].text
        self.assertIn("LCP-100", text)
        self.assertIn("local LCP validation", text)

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_valid_tool_payload_reaches_the_endpoint(self) -> None:
        import json

        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        lead = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))
        payload = lead["lcp"]["payload"]
        with patch.object(
            LCPClient,
            "submit_lead",
            return_value={"_ok": True, "_status_code": 200, "lcp": {"ack": True}},
        ) as submit:
            result = _run_tool(
                "submit_lead", {"receiver_id": "platform_001", "payload": payload}
            )
        submit.assert_called_once()
        self.assertIn("ack", result[0].text)

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_schema_validator_is_constructed_once_across_tool_calls(self) -> None:
        import json

        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient
        from lcp_sdk.validation import SchemaValidator as RealSchemaValidator

        lead = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))
        payload = lead["lcp"]["payload"]
        constructions: list[int] = []

        class CountingValidator(RealSchemaValidator):
            def __init__(self) -> None:
                super().__init__()
                constructions.append(1)

        server_module.reset_validator()
        try:
            with (
                patch.object(server_module, "SchemaValidator", CountingValidator),
                patch.object(
                    LCPClient,
                    "submit_lead",
                    return_value={"_ok": True, "_status_code": 200},
                ),
            ):
                validator_ids = set()
                for _ in range(3):
                    _run_tool(
                        "submit_lead",
                        {"receiver_id": "platform_001", "payload": payload},
                    )
                    validator_ids.add(id(server_module._validator))
        finally:
            server_module.reset_validator()

        self.assertEqual(len(constructions), 1, "validator rebuilt per tool call")
        self.assertEqual(
            len(validator_ids), 1, "validator instance changed between calls"
        )

    @unittest.skipUnless(
        importlib.util.find_spec("mcp"), "MCP runtime dependency is not installed"
    )
    def test_concurrent_tool_calls_do_not_serialize_on_a_slow_upstream(self) -> None:
        import threading
        import time

        from lcp_mcp_server import server as server_module
        from lcp_mcp_server.client import LCPClient

        delay = 0.3
        lock = threading.Lock()
        in_flight = 0
        peak = 0

        def slow_get_capabilities(self) -> dict:
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            try:
                time.sleep(delay)
            finally:
                with lock:
                    in_flight -= 1
            return {"_ok": True, "_status_code": 200}

        async def gather_two():
            started = time.monotonic()
            results = await asyncio.gather(
                server_module._call_tool("get_capabilities", {}),
                server_module._call_tool("get_capabilities", {}),
            )
            return time.monotonic() - started, results

        with patch.object(LCPClient, "get_capabilities", slow_get_capabilities):
            elapsed, results = asyncio.run(gather_two())

        self.assertEqual(len(results), 2)
        self.assertEqual(peak, 2, "upstream calls never overlapped")
        self.assertLess(
            elapsed,
            delay * 2,
            f"concurrent tool calls serialized on the event loop ({elapsed:.2f}s)",
        )

    def test_shared_client_is_reused_and_closes_cleanly(self) -> None:
        from lcp_mcp_server import server as server_module

        class FakeClient:
            def __init__(self) -> None:
                self.close_calls = 0

            def close(self) -> None:
                self.close_calls += 1

        with patch.object(server_module, "LCPClient", FakeClient):
            server_module.reset_client()
            first = server_module._get_client()
            self.assertIs(first, server_module._get_client(), "client rebuilt per call")
            server_module.close_client()
            self.assertEqual(first.close_calls, 1)
            second = server_module._get_client()
            self.assertIsNot(first, second, "closed client was handed out again")
            server_module.close_client()
            self.assertEqual(second.close_calls, 1)
            server_module.close_client()  # nothing cached: no-op, no double close
            self.assertEqual(second.close_calls, 1)
        server_module.reset_client()

    def test_client_is_rebuilt_when_env_config_changes(self) -> None:
        from lcp_mcp_server import server as server_module

        class FakeClient:
            pass

        with patch.object(server_module, "LCPClient", FakeClient):
            server_module.reset_client()
            with patch.dict(os.environ, {"LCP_SENDER_ID": "sender_a"}):
                first = server_module._get_client()
                self.assertIs(first, server_module._get_client())
            with patch.dict(os.environ, {"LCP_SENDER_ID": "sender_b"}):
                self.assertIsNot(first, server_module._get_client())
        server_module.reset_client()


if __name__ == "__main__":
    unittest.main()

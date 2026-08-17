from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()

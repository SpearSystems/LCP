"""LEP-0005 §3: the MCP adapter MUST surface pagination inside the tool result.

MCP tool results are content blocks, not HTTP responses, so the three
pagination values cannot ride headers; the adapter extracts them from the
response headers and folds them into the result object.
"""

from __future__ import annotations

import unittest


class HistoryToolPaginationTests(unittest.TestCase):
    def test_cursor_is_extracted_from_a_next_link(self) -> None:
        from lcp_mcp_server.client import _cursor_from_link

        link = (
            "<https://api.example.com/v1/lcp/history/requests?limit=50"
            '&cursor=czI2LDIwMjYtMTEtMDNUMTA6MjE6MDA>; rel="next"'
        )
        self.assertEqual(
            "czI2LDIwMjYtMTEtMDNUMTA6MjE6MDA", _cursor_from_link(link)
        )

    def test_missing_or_non_next_link_yields_no_cursor(self) -> None:
        from lcp_mcp_server.client import _cursor_from_link

        self.assertIsNone(_cursor_from_link(None))
        self.assertIsNone(_cursor_from_link('<https://x/y>; rel="prev"'))


if __name__ == "__main__":
    unittest.main()

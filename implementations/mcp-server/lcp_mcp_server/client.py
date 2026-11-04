"""LCP HTTP client compatibility wrapper for the MCP adapter.

The MCP package intentionally delegates authentication, retries, and HTTP
transport to the standalone lcp-sdk package. This wrapper preserves the MCP
adapter's historical ``post``/``get`` response shape, including ``_ok`` and
``_status_code`` metadata.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from lcp_sdk import LCPClient as SDKLCPClient
from lcp_sdk import LCPHTTPError


def _quote_path_segment(value: str) -> str:
    """Percent-encode a URL path segment, neutralizing traversal and query chars.

    ``urllib.parse.quote`` leaves ``.`` and ``..`` unencoded, so ``..`` is
    escaped explicitly to keep ``/../`` from surviving into the request path.
    """
    return quote(str(value), safe="").replace("..", "%2E%2E")


def _quote_multi_segment(value: str) -> str:
    """Encode a slash-separated name (e.g. ``verticals/mortgage``) segment-wise.

    Legitimate schema names keep their ``/`` separators; every other character
    (including ``..``, ``?``, and ``#``) is percent-encoded.
    """
    return "/".join(_quote_path_segment(part) for part in str(value).split("/"))


class LCPClient:
    """MCP-facing compatibility wrapper around :class:`lcp_sdk.LCPClient`."""

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        hmac_secret: str | None = None,
        sender_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = SDKLCPClient(
            endpoint or os.environ.get("LCP_ENDPOINT", "http://localhost:8000"),
            sender_id=sender_id or os.environ.get("LCP_SENDER_ID", ""),
            api_key=api_key or os.environ.get("LCP_API_KEY"),
            hmac_secret=hmac_secret or os.environ.get("LCP_HMAC_SECRET"),
            timeout=timeout,
        )

    def close(self) -> None:
        """Release the underlying SDK client's resources. Idempotent.

        The SDK opens its HTTP connection per request today, so this closes
        whatever a future SDK release holds open rather than leaking it; the
        adapter already funnels shutdown through here.
        """
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    @property
    def endpoint(self) -> str:
        return self._client.endpoint

    @property
    def sender_id(self) -> str | None:
        return self._client.sender_id

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST an LCP envelope, preserving the legacy MCP response shape."""
        try:
            data = self._client.request("POST", path, payload=payload)
            return _with_metadata(data, 200, True)
        except LCPHTTPError as exc:
            body = exc.body if isinstance(exc.body, dict) else {"raw": exc.body}
            return _with_metadata(body, exc.status_code, False)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET from the LCP endpoint, preserving the legacy response shape."""
        try:
            data = self._client.request("GET", path, params=params)
            return _with_metadata(data, 200, True)
        except LCPHTTPError as exc:
            body = exc.body if isinstance(exc.body, dict) else {"raw": exc.body}
            return _with_metadata(body, exc.status_code, False)

    def submit_lead(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.post("/v1/lcp/leads", envelope)

    def submit_call(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.post("/v1/lcp/calls", envelope)

    def query_lead_status(self, lead_id: str) -> dict[str, Any]:
        return self.get(f"/v1/lcp/leads/{_quote_path_segment(lead_id)}")

    def get_schema(self, name: str) -> dict[str, Any]:
        return self.get(f"/v1/lcp/schemas/{_quote_multi_segment(name)}")

    def get_capabilities(self) -> dict[str, Any]:
        return self.get("/v1/lcp/capabilities")

    def list_offers(self, vertical: str | None = None) -> dict[str, Any]:
        params = {"vertical": vertical} if vertical else None
        return self.get("/v1/lcp/offers", params=params)

    def get_with_headers(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """GET returning ``(legacy-shaped body, response headers)`` (LEP-0005).

        MCP tool results are content blocks, not HTTP responses: the adapter
        MUST surface pagination values inside the result object, so it needs
        the headers the plain ``get`` helper drops.
        """
        try:
            data, headers = self._client.request_with_headers(
                "GET", path, params=params
            )
            return _with_metadata(data, 200, True), dict(headers)
        except LCPHTTPError as exc:
            body = exc.body if isinstance(exc.body, dict) else {"raw": exc.body}
            return _with_metadata(body, exc.status_code, False), {}

    def get_request_history(
        self,
        *,
        request_id: str | None = None,
        lead_id: str | None = None,
        message_type: str | None = None,
        status: str | None = None,
        vertical: str | None = None,
        caller_phone_hash: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """LEP-0005 history query with pagination surfaced in the result."""
        params = {
            key: value
            for key, value in {
                "request_id": request_id,
                "lead_id": lead_id,
                "message_type": message_type,
                "status": status,
                "vertical": vertical,
                "caller_phone_hash": caller_phone_hash,
                "from": from_,
                "to": to,
                "limit": limit,
                "cursor": cursor,
            }.items()
            if value is not None
        }
        result, headers = self.get_with_headers(
            "/v1/lcp/history/requests", params=params or None
        )
        if result.get("_ok"):
            result["pagination"] = {
                "result_count": _as_int(headers.get("x-lcp-result-count")),
                "total_results": _as_int(headers.get("x-lcp-total-results")),
                "next_cursor": _cursor_from_link(headers.get("link")),
            }
        return result


def _with_metadata(data: dict[str, Any], status_code: int, ok: bool) -> dict[str, Any]:
    result = dict(data)
    result["_status_code"] = status_code
    result["_ok"] = ok
    return result


def _as_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _cursor_from_link(link: str | None) -> str | None:
    """Extract ``cursor`` from a ``Link: <url>; rel="next"`` header."""
    if not link:
        return None
    for part in link.split(","):
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">", start + 1)
        if start < 0 or end < 0:
            continue
        url = part[start + 1 : end]
        # urllib keeps this dependency-free and correct for encoded cursors.
        from urllib.parse import parse_qs, urlparse

        values = parse_qs(urlparse(url).query).get("cursor")
        if values:
            return values[0]
    return None

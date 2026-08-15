"""LCP HTTP client compatibility wrapper for the MCP adapter.

The MCP package intentionally delegates authentication, retries, and HTTP
transport to the standalone lcp-sdk package. This wrapper preserves the MCP
adapter's historical ``post``/``get`` response shape, including ``_ok`` and
``_status_code`` metadata.
"""

from __future__ import annotations

import os
from typing import Any

from lcp_sdk import LCPClient as SDKLCPClient
from lcp_sdk import LCPHTTPError


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
        return self.get(f"/v1/lcp/leads/{lead_id}")

    def get_schema(self, name: str) -> dict[str, Any]:
        return self.get(f"/v1/lcp/schemas/{name}")

    def get_capabilities(self) -> dict[str, Any]:
        return self.get("/v1/lcp/capabilities")

    def list_offers(self, vertical: str | None = None) -> dict[str, Any]:
        params = {"vertical": vertical} if vertical else None
        return self.get("/v1/lcp/offers", params=params)


def _with_metadata(data: dict[str, Any], status_code: int, ok: bool) -> dict[str, Any]:
    result = dict(data)
    result["_status_code"] = status_code
    result["_ok"] = ok
    return result

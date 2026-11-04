"""HTTP-independent request dispatch for the reference platform."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .auth import AuthenticationError, header
from .observability import reset_request_id, set_request_id
from .router import Platform, RequestError


_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

# LEP-0001 W2-01 REST surface. The path lives with the HTTP binding; the caps
# and the intake loop live in the router.
BATCH_ROUTE = "/v1/lcp/leads/batch"

# LEP-0001 W3 REST surface. Registration is POST on the registration resource;
# reads and deactivation live on the subscription collection.
SUBSCRIBE_ROUTE = "/v1/lcp/events/subscribe"
SUBSCRIPTIONS_ROUTE = "/v1/lcp/events/subscriptions"
EVENTS_ROUTE = "/v1/lcp/events"

# LEP-0009 negotiation REST surface. The negotiation state machine lives in
# the router; these are the HTTP binding names.
QUOTE_ROUTE = "/v1/lcp/quotes"
COUNTER_ROUTE = "/v1/lcp/counters"


def _request_id(headers: dict[str, str]) -> str:
    supplied = header(headers, "X-LCP-Request-Id") or header(headers, "X-Request-Id")
    if supplied and _REQUEST_ID_PATTERN.fullmatch(supplied):
        return supplied
    return f"req_{uuid4().hex}"


class PlatformService:
    def __init__(self, platform: Platform):
        self.platform = platform

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> tuple[int, dict[str, str], Any]:
        headers = {key: value for key, value in (headers or {}).items()}
        request_id = _request_id(headers)
        request_token = set_request_id(request_id)
        try:
            header_bytes = sum(
                len(key.encode("utf-8")) + len(value.encode("utf-8"))
                for key, value in headers.items()
            )
            if header_bytes > self.platform.config.max_header_bytes:
                response = self._response(
                    431,
                    {
                        "errors": [
                            {
                                "code": "LCP-001",
                                "message": "Request headers are too large",
                            }
                        ]
                    },
                )
            else:
                parsed = urlsplit(path)
                route = parsed.path.rstrip("/") or "/"
                try:
                    if method == "POST":
                        response = self._post(route, headers, body)
                    elif method == "GET":
                        response = self._get(route, parsed.query, headers)
                    elif method == "DELETE":
                        response = self._delete(route, headers)
                    else:
                        response = self._response(
                            405,
                            {
                                "errors": [
                                    {"code": "LCP-001", "message": "Method not allowed"}
                                ]
                            },
                        )
                except AuthenticationError as exc:
                    response = self._response(
                        401, {"errors": [{"code": exc.code, "message": str(exc)}]}
                    )
                except RequestError as exc:
                    error: dict[str, Any] = {"code": exc.code, "message": str(exc)}
                    if exc.details is not None:
                        error["details"] = exc.details
                    response_headers = (
                        {"Retry-After": "60"} if exc.status_code == 429 else None
                    )
                    response = self._response(
                        exc.status_code, {"errors": [error]}, response_headers
                    )
                except json.JSONDecodeError:
                    response = self._response(
                        400,
                        {
                            "errors": [
                                {
                                    "code": "LCP-001",
                                    "message": "Request body must be JSON",
                                }
                            ]
                        },
                    )
                except Exception:
                    response = self._response(
                        500,
                        {
                            "errors": [
                                {"code": "LCP-500", "message": "Internal server error"}
                            ]
                        },
                    )

            status, response_headers, payload = response
            response_headers = dict(response_headers)
            response_headers.setdefault("X-LCP-Request-Id", request_id)
            if isinstance(payload, dict) and "errors" in payload:
                payload = dict(payload)
                payload.setdefault("request_id", request_id)
            return status, response_headers, payload
        finally:
            reset_request_id(request_token)

    def _post(
        self,
        route: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        max_body_bytes = (
            self.platform.config.max_attachment_bytes
            if route == "/v1/lcp/attachments"
            else self.platform.config.max_body_bytes
        )
        if len(body) > max_body_bytes:
            raise RequestError("Request body is too large", "LCP-001", 413)
        if route == BATCH_ROUTE and not self.platform.config.feature_batch:
            # LEP-0001 compensating constraint 2: with LCP_FEATURE_BATCH off the
            # surface is indistinguishable from an endpoint this build does not
            # have — the existing unknown-endpoint error shape, no per-item
            # receipts, nothing written.
            raise RequestError("Endpoint not found", "LCP-001", 404)
        if (
            route in {QUOTE_ROUTE, COUNTER_ROUTE}
            and not self.platform.config.feature_negotiation
        ):
            # LEP-0009 C3 shares the batch switch discipline: off means the
            # route does not exist, not a per-message rejection.
            raise RequestError("Endpoint not found", "LCP-001", 404)
        if route == SUBSCRIBE_ROUTE and not self.platform.config.feature_events:
            # LEP-0001 W3: same off-by-default discipline as batch and
            # negotiation.
            raise RequestError("Endpoint not found", "LCP-001", 404)
        if route == "/v1/lcp/attachments":
            return self._response(
                201, self.platform.upload_attachment(headers=headers, body=body)
            )
        content_type = header(headers, "Content-Type") or ""
        if not content_type.lower().split(";", 1)[0].strip() == "application/json":
            raise RequestError("Content-Type must be application/json", "LCP-001", 415)
        envelope = json.loads(body)
        if route in {
            "/v1/lcp/leads",
            "/v1/lcp/calls",
            "/v1/lcp/bids",
            "/v1/lcp/events",
            BATCH_ROUTE,
            QUOTE_ROUTE,
            COUNTER_ROUTE,
            SUBSCRIBE_ROUTE,
        }:
            message = envelope.get("lcp", {}).get("message", {})
            header_key = header(headers, "X-LCP-Idempotency-Key")
            if not header_key or header_key != message.get("idempotency_key"):
                raise RequestError(
                    "X-LCP-Idempotency-Key must match the envelope",
                    "LCP-012",
                    401,
                )
        if route == "/v1/lcp/leads":
            result = self.platform.ingest(envelope, headers=headers, raw_body=body)
        elif route == BATCH_ROUTE:
            # LEP-0001 W2-01: partial success is the contract, so the batch
            # response is always 207 with one receipt per input item.
            return self._response(
                207,
                self.platform.submit_batch(envelope, headers=headers, raw_body=body),
            )
        elif route == "/v1/lcp/calls":
            result = self.platform.ingest(envelope, headers=headers, raw_body=body)
        elif route == "/v1/lcp/bids":
            result = self.platform.submit_bid(envelope, headers=headers, raw_body=body)
        elif route == QUOTE_ROUTE:
            result = self.platform.submit_quote(
                envelope, headers=headers, raw_body=body
            )
        elif route == COUNTER_ROUTE:
            result = self.platform.submit_counter(
                envelope, headers=headers, raw_body=body
            )
        elif route == SUBSCRIBE_ROUTE:
            return self._response(
                201,
                self.platform.subscribe_events(
                    envelope, headers=headers, raw_body=body
                ),
            )
        elif route == "/v1/lcp/events":
            result = self.platform.submit_event(
                envelope, headers=headers, raw_body=body
            )
        else:
            raise RequestError("Endpoint not found", "LCP-001", 404)
        return self._response(200, result)

    def _get(
        self,
        route: str,
        query: str,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        if route in {"/health/live", "/v1/lcp/health/live"}:
            return self._response(200, {"status": "ok"})
        if route in {"/health/ready", "/v1/lcp/health/ready"}:
            try:
                self.platform.store.healthcheck()
            except Exception:
                return self._response(503, {"status": "not_ready"})
            return self._response(200, {"status": "ready"})
        if route in {"/metrics", "/v1/lcp/metrics"}:
            self._authenticate_read(headers, required_scope="platform:admin")
            return self._response(200, self.platform.store.metrics())
        if route == "/v1/lcp/capabilities":
            return self._response(200, self.platform.capabilities())
        if route == EVENTS_ROUTE:
            if not self.platform.config.feature_events:
                raise RequestError("Endpoint not found", "LCP-001", 404)
            return self._response(
                200, self.platform.events_page(query, headers=headers)
            )
        if route == SUBSCRIPTIONS_ROUTE:
            if not self.platform.config.feature_events:
                raise RequestError("Endpoint not found", "LCP-001", 404)
            return self._response(
                200, self.platform.list_event_subscriptions(headers=headers)
            )
        if route == "/v1/lcp/history/requests":
            sender_id = self._authenticate_read(headers, required_scope="history:read")
            payload, extra_headers = self.platform.request_history(
                query, sender_id=sender_id, headers=headers
            )
            return self._response(200, payload, extra_headers=extra_headers)
        if route.startswith("/v1/lcp/attachments/"):
            attachment_id = route.removeprefix("/v1/lcp/attachments/")
            content, attachment_headers = self.platform.download_attachment(
                attachment_id, headers=headers
            )
            attachment_headers["Content-Length"] = str(len(content))
            return 200, attachment_headers, content
        if route.startswith("/v1/lcp/schemas/"):
            return self._response(
                200, self.platform.schema(route.removeprefix("/v1/lcp/schemas/"))
            )
        if route == "/v1/lcp/offers":
            self._authenticate_read(headers, required_scope="offer:read")
            vertical = parse_qs(query).get("vertical", [None])[0]
            return self._response(200, self.platform.public_offers(vertical))
        if route.startswith("/v1/lcp/offers/") and route.endswith("/quota"):
            self._authenticate_read(headers, required_scope="offer:read")
            offer_id = (
                route.removeprefix("/v1/lcp/offers/").removesuffix("/quota").strip("/")
            )
            return self._response(200, self.platform.quota_status(offer_id))
        if route.startswith("/v1/lcp/leads/"):
            sender_id = self._authenticate_read(headers, required_scope="lead:read")
            lead_id = route.removeprefix("/v1/lcp/leads/")
            self.platform.authorize_lead_read(lead_id, sender_id)
            return self._response(
                200, self.platform.lead_status(lead_id, requester_id=sender_id)
            )
        raise RequestError("Endpoint not found", "LCP-001", 404)

    def _delete(
        self,
        route: str,
        headers: dict[str, str],
    ) -> tuple[int, dict[str, str], Any]:
        if route.startswith(f"{SUBSCRIPTIONS_ROUTE}/"):
            if not self.platform.config.feature_events:
                raise RequestError("Endpoint not found", "LCP-001", 404)
            subscription_id = route.removeprefix(f"{SUBSCRIPTIONS_ROUTE}/").strip("/")
            if not subscription_id or "/" in subscription_id:
                raise RequestError("Endpoint not found", "LCP-001", 404)
            return self._response(
                200,
                self.platform.disable_event_subscription(
                    subscription_id, headers=headers
                ),
            )
        raise RequestError("Endpoint not found", "LCP-001", 404)

    def _authenticate_read(
        self, headers: dict[str, str], *, required_scope: str
    ) -> str:
        return self.platform.auth.authenticate(
            sender_id=header(headers, "X-LCP-Sender-Id"),
            headers=headers,
            body=b"",
            mutating=False,
            required_scope=required_scope,
        )

    @staticmethod
    def _response(
        status: int,
        payload: Any,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], Any]:
        response_headers = {"Content-Type": "application/json; charset=utf-8"}
        response_headers.update(extra_headers or {})
        return status, response_headers, payload

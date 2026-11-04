"""LEP-0001 W3: signed event subscriptions, cursor replay, and isolation.

Contract pinned here:

* registration is tenant-scoped, allowlist-bounded, and returns the
  per-subscription secret exactly once;
* delivery is a background worker path that signs the exact bytes it sends
  over the LEP-0002 canonical input, retries with backoff, and dead-letters
  after the configured attempt budget;
* duplicates are legal at-least-once traffic — the same ``event_id`` may be
  delivered twice and a consumer dedupes on it;
* ``GET /v1/lcp/events?cursor=`` replays a deterministic event set and an
  expired cursor is an explicit error;
* filters are a closed key set of core enums/ids and consumer paths are
  refused by name; and
* ``LCP_FEATURE_EVENTS`` is off by default (the routes answer 404 when off).
"""

from __future__ import annotations

import copy
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import (
    EVENTS_ROUTE,
    SUBSCRIBE_ROUTE,
    SUBSCRIPTIONS_ROUTE,
    PlatformService,
)
from lcp_platform.storage import format_iso_datetime, now_iso

ROOT = Path(__file__).resolve().parents[3]
TENANT_A_SECRET = "tenant-a-secret"
TENANT_B_SECRET = "tenant-b-secret"
EXAMPLE_LEAD = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))


def _compact(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


class CallbackServer:
    """A local subscriber endpoint that records exact bodies and headers."""

    def __init__(self) -> None:
        self.status = 200
        self.requests: list[tuple[dict[str, str], bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.requests.append((dict(self.headers.items()), body))
                self.send_response(outer.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hooks/lcp"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class EventSubscriptionTests(unittest.TestCase):
    def _platform(
        self,
        *,
        feature_events: bool = True,
        require_auth: bool = False,
        max_delivery_attempts: int = 5,
        cursor_ttl: int = 86_400,
    ) -> Platform:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=require_auth,
                allow_insecure_webhooks=True,
                test_mode=True,
                feature_events=feature_events,
                max_delivery_attempts=max_delivery_attempts,
                event_cursor_ttl_seconds=cursor_ttl,
            )
        )
        self.addCleanup(platform.close)
        if require_auth:
            platform.upsert_credential(
                "pub_123",
                tenant_id="tenant_a",
                scopes=["lead:submit", "event:submit", "event:read", "event:subscribe"],
                hmac_secret=TENANT_A_SECRET,
            )
            platform.upsert_credential(
                "buyer_7",
                tenant_id="tenant_b",
                scopes=["lead:submit", "event:submit", "event:read", "event:subscribe"],
                hmac_secret=TENANT_B_SECRET,
            )
        return platform

    def _callback(self) -> CallbackServer:
        server = CallbackServer()
        self.addCleanup(server.close)
        return server

    def _ingest_lead(
        self, platform: Platform, *, lead_id: str, sender: str, key: str
    ) -> None:
        envelope = copy.deepcopy(EXAMPLE_LEAD)
        message = envelope["lcp"]["message"]
        message.update(
            {
                "id": str(uuid4()),
                "timestamp": now_iso(),
                "sender_id": sender,
                "idempotency_key": key,
                "test": True,
            }
        )
        payload = envelope["lcp"]["payload"]
        payload["lead_id"] = lead_id
        payload.pop("expiry", None)
        headers = self._headers_for(
            envelope,
            sender,
            TENANT_A_SECRET if sender == "pub_123" else TENANT_B_SECRET,
            signed=platform.config.require_auth,
        )
        platform.ingest(envelope, headers=headers, raw_body=_compact(envelope))

    def _make_event(
        self,
        platform: Platform,
        *,
        lead_id: str,
        sender: str,
        event_name: str = "REJECTED",
        key: str | None = None,
    ) -> str:
        envelope = {
            "lcp": {
                "version": "1.2.0",
                "message": {
                    "id": str(uuid4()),
                    "type": "event",
                    "timestamp": now_iso(),
                    "sender_id": sender,
                    "receiver_id": "platform_001",
                    "correlation_id": None,
                    "idempotency_key": key or f"event-{uuid4().hex}",
                    "test": True,
                },
                "payload": {
                    "lead_id": lead_id,
                    "event": event_name,
                    "timestamp": now_iso(),
                },
            }
        }
        headers = self._headers_for(
            envelope,
            sender,
            TENANT_A_SECRET if sender == "pub_123" else TENANT_B_SECRET,
            signed=platform.config.require_auth,
        )
        platform.submit_event(envelope, headers=headers, raw_body=_compact(envelope))
        return str(envelope["lcp"]["message"]["id"])

    @staticmethod
    def _headers_for(
        envelope: dict[str, Any], sender: str, secret: str, *, signed: bool
    ) -> dict[str, str]:
        message = envelope["lcp"]["message"]
        headers = {
            "Content-Type": "application/json",
            "X-LCP-Test": "true",
            "X-LCP-Sender-Id": sender,
            "X-LCP-Idempotency-Key": message["idempotency_key"],
        }
        if signed:
            timestamp = now_iso()
            headers["X-LCP-Timestamp"] = timestamp
            headers["X-LCP-Signature"] = signature_for(
                secret, timestamp, message["idempotency_key"], _compact(envelope)
            )
        return headers

    @staticmethod
    def _read_headers(sender: str, secret: str) -> dict[str, str]:
        timestamp = now_iso()
        return {
            "X-LCP-Sender-Id": sender,
            "X-LCP-Timestamp": timestamp,
            "X-LCP-Signature": signature_for(secret, timestamp, None, b""),
        }

    def _subscribe(
        self,
        platform: Platform,
        *,
        sender: str = "buyer_7",
        callback_url: str,
        event_types: list[str] | None = None,
        filter_values: dict[str, Any] | None = None,
        key: str = "sub-key-1",
        replay_from: str | None = None,
        secret: str = TENANT_B_SECRET,
    ):
        payload: dict[str, Any] = {
            "event_types": event_types or ["REJECTED"],
            "callback_url": callback_url,
        }
        if filter_values is not None:
            payload["filter"] = filter_values
        if replay_from is not None:
            payload["replay_from"] = replay_from
        envelope = {
            "lcp": {
                "version": "1.2.0",
                "message": {
                    "id": str(uuid4()),
                    "type": "subscription",
                    "timestamp": now_iso(),
                    "sender_id": sender,
                    "receiver_id": "platform_001",
                    "correlation_id": None,
                    "idempotency_key": key,
                    "test": True,
                },
                "payload": payload,
            }
        }
        headers = self._headers_for(
            envelope, sender, secret, signed=platform.config.require_auth
        )
        service = PlatformService(platform)
        return service.dispatch(
            "POST", SUBSCRIBE_ROUTE, headers=headers, body=_compact(envelope)
        )

    def _get(self, platform: Platform, path: str, *, sender: str, secret: str):
        service = PlatformService(platform)
        return service.dispatch(
            "GET",
            path,
            headers=self._read_headers(sender, secret),
        )

    def _delete(self, platform: Platform, path: str, *, sender: str, secret: str):
        service = PlatformService(platform)
        return service.dispatch(
            "DELETE", path, headers=self._read_headers(sender, secret)
        )

    # ─── Capability gate ───────────────────────────────────────────────────

    def test_feature_off_answers_like_an_unknown_endpoint(self) -> None:
        platform = self._platform(feature_events=False)
        service = PlatformService(platform)
        status, _, payload = service.dispatch(
            "POST",
            SUBSCRIBE_ROUTE,
            headers={"Content-Type": "application/json"},
            body=b"{}",
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["errors"][0]["code"], "LCP-001")
        for path in (EVENTS_ROUTE, SUBSCRIPTIONS_ROUTE):
            status, _, _ = service.dispatch("GET", path, headers={})
            self.assertEqual(status, 404, path)
        status, _, _ = service.dispatch(
            "DELETE", f"{SUBSCRIPTIONS_ROUTE}/sub_x", headers={}
        )
        self.assertEqual(status, 404)
        capabilities = platform.capabilities()
        self.assertNotIn("event_subscriptions", capabilities["features"])
        self.assertNotIn("subscription", capabilities["message_types"])

    def test_feature_on_advertises_subscription_surface(self) -> None:
        platform = self._platform()
        capabilities = platform.capabilities()
        self.assertEqual(capabilities["features"]["event_subscriptions"], "1.2.0")
        self.assertIn("subscription", capabilities["message_types"])

    # ─── Registration, isolation, secret-once ──────────────────────────────

    def test_registration_secret_returned_once_and_tenant_scoped(self) -> None:
        platform = self._platform(require_auth=True)
        callback = self._callback()
        status, _, created = self._subscribe(platform, callback_url=callback.url)
        self.assertEqual(status, 201, created)
        self.assertEqual(created["subscription_id"][:4], "sub_")
        self.assertGreaterEqual(len(created["secret"]), 32)
        self.assertTrue(created["active"])
        subscription_id = created["subscription_id"]

        status, _, listed = self._get(
            platform,
            SUBSCRIPTIONS_ROUTE,
            sender="buyer_7",
            secret=TENANT_B_SECRET,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [row["subscription_id"] for row in listed["subscriptions"]],
            [subscription_id],
        )
        self.assertNotIn("secret", listed["subscriptions"][0])

        # Another tenant's credential cannot see or disable it.
        status, _, other = self._get(
            platform, SUBSCRIPTIONS_ROUTE, sender="pub_123", secret=TENANT_A_SECRET
        )
        self.assertEqual(status, 200)
        self.assertEqual(other["subscriptions"], [])
        status, _, forbidden = self._delete(
            platform,
            f"{SUBSCRIPTIONS_ROUTE}/{subscription_id}",
            sender="pub_123",
            secret=TENANT_A_SECRET,
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["errors"][0]["code"], "LCP-002")

        status, _, disabled = self._delete(
            platform,
            f"{SUBSCRIPTIONS_ROUTE}/{subscription_id}",
            sender="buyer_7",
            secret=TENANT_B_SECRET,
        )
        self.assertEqual(status, 200)
        self.assertFalse(disabled["active"])
        self.assertNotIn("secret", disabled)

    def test_unknown_event_type_is_refused(self) -> None:
        platform = self._platform()
        callback = self._callback()
        status, _, payload = self._subscribe(
            platform,
            callback_url=callback.url,
            event_types=["CONSUMER_EMAIL_CHANGED"],
        )
        self.assertEqual(status, 400)
        error = payload["errors"][0]
        self.assertEqual(error["code"], "LCP-100")
        self.assertEqual(error["details"]["reason"], "unadvertised_event_type")
        self.assertEqual(error["details"]["field"], "event_types")

    def test_consumer_filter_is_refused_by_name(self) -> None:
        platform = self._platform()
        callback = self._callback()
        status, _, payload = self._subscribe(
            platform,
            callback_url=callback.url,
            filter_values={"consumer.email": "jane@example.com"},
        )
        self.assertEqual(status, 400)
        error = payload["errors"][0]
        self.assertEqual(error["details"]["reason"], "filter_field_pii")
        self.assertEqual(error["details"]["field"], "consumer.email")

    def test_private_callback_url_is_refused_outside_sandbox(self) -> None:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                test_mode=True,
                allow_insecure_webhooks=False,
                feature_events=True,
            )
        )
        self.addCleanup(platform.close)
        status, _, payload = self._subscribe(
            platform, callback_url="http://127.0.0.1:9/hooks"
        )
        self.assertEqual(status, 400)
        self.assertIn(
            payload["errors"][0]["details"]["reason"],
            {"callback_url_private_address", "callback_url_invalid"},
        )

    # ─── Delivery/signature/retry/DLQ ──────────────────────────────────────

    def test_delivery_signs_exact_bytes_with_stable_event_id(self) -> None:
        platform = self._platform()
        callback = self._callback()
        self._ingest_lead(
            platform, lead_id="lead_w3_sig", sender="pub_123", key="w3-sig"
        )
        status, _, created = self._subscribe(
            platform,
            callback_url=callback.url,
            filter_values={"lead_id": "lead_w3_sig"},
        )
        self.assertEqual(status, 201, created)
        self._make_event(platform, lead_id="lead_w3_sig", sender="pub_123")

        platform.process_once()
        self.assertEqual(len(callback.requests), 1)
        headers, body = callback.requests[0]
        normalized = {key.lower(): value for key, value in headers.items()}
        event_id = normalized["x-lcp-event-id"]
        self.assertEqual(normalized["x-lcp-idempotency-key"], event_id)
        expected = signature_for(
            created["secret"],
            normalized["x-lcp-timestamp"],
            event_id,
            body,
        )
        self.assertEqual(normalized["x-lcp-signature"], expected)
        delivered = json.loads(body)
        self.assertEqual(delivered["lcp"]["message"]["id"], event_id)

        deliveries = platform.store.list_event_deliveries(created["subscription_id"])
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["event_id"], event_id)
        self.assertEqual(deliveries[0]["status"], "DELIVERED")

    def test_retry_with_backoff_then_dead_letter(self) -> None:
        platform = self._platform(max_delivery_attempts=2)
        callback = self._callback()
        callback.status = 500
        self._ingest_lead(
            platform, lead_id="lead_w3_dlq", sender="pub_123", key="w3-dlq"
        )
        status, _, created = self._subscribe(
            platform,
            callback_url=callback.url,
            filter_values={"lead_id": "lead_w3_dlq"},
        )
        self.assertEqual(status, 201, created)
        self._make_event(platform, lead_id="lead_w3_dlq", sender="pub_123")

        platform.process_once()
        deliveries = platform.store.list_event_deliveries(created["subscription_id"])
        self.assertEqual(len(deliveries), 1)
        first = deliveries[0]
        self.assertEqual(first["status"], "RETRY")
        self.assertEqual(first["attempts"], 1)
        self.assertGreater(first["next_attempt_at"], now_iso())

        # Backoff is real time; force the retry due instead of sleeping.
        platform.store.mark_event_delivery(
            first["delivery_id"],
            status="RETRY",
            attempts=first["attempts"],
            next_attempt_at=now_iso(),
        )
        platform.process_once()
        deliveries = platform.store.list_event_deliveries(created["subscription_id"])
        self.assertEqual(deliveries[0]["status"], "FAILED")
        self.assertEqual(deliveries[0]["attempts"], 2)
        dead_letters = platform.store.list_dead_letters()
        self.assertEqual(len(dead_letters), 1)
        self.assertEqual(dead_letters[0]["queue_type"], "event_subscription")
        self.assertEqual(dead_letters[0]["resource_id"], deliveries[0]["delivery_id"])

    def test_duplicate_delivery_is_tolerated(self) -> None:
        platform = self._platform()
        callback = self._callback()
        self._ingest_lead(
            platform, lead_id="lead_w3_dup", sender="pub_123", key="w3-dup"
        )
        status, _, created = self._subscribe(
            platform,
            callback_url=callback.url,
            filter_values={"lead_id": "lead_w3_dup"},
        )
        self.assertEqual(status, 201, created)
        self._make_event(platform, lead_id="lead_w3_dup", sender="pub_123")
        platform.process_once()
        deliveries = platform.store.list_event_deliveries(created["subscription_id"])
        self.assertEqual(len(deliveries), 1)
        # At-least-once: the same delivery may be attempted again; a consumer
        # dedupes on event_id, and the body for a given event is identical.
        platform.store.mark_event_delivery(
            deliveries[0]["delivery_id"],
            status="PENDING",
            attempts=deliveries[0]["attempts"],
            next_attempt_at=now_iso(),
        )
        platform.process_once()
        self.assertEqual(len(callback.requests), 2)
        first_headers, first_body = callback.requests[0]
        second_headers, second_body = callback.requests[1]
        self.assertEqual(first_body, second_body)
        self.assertEqual(
            {k.lower(): v for k, v in first_headers.items()}["x-lcp-event-id"],
            {k.lower(): v for k, v in second_headers.items()}["x-lcp-event-id"],
        )

    # ─── Cursor read/replay ────────────────────────────────────────────────

    def test_cursor_paging_and_replay_identity(self) -> None:
        platform = self._platform()
        callback = self._callback()
        self._ingest_lead(platform, lead_id="lead_w3_a", sender="pub_123", key="w3-a")
        self._ingest_lead(platform, lead_id="lead_w3_b", sender="pub_123", key="w3-b")
        self._make_event(platform, lead_id="lead_w3_a", sender="pub_123", key="evt-a")
        self._make_event(platform, lead_id="lead_w3_b", sender="pub_123", key="evt-b")

        status, _, first_page = self._get(
            platform,
            f"{EVENTS_ROUTE}?limit=1",
            sender="pub_123",
            secret=TENANT_A_SECRET,
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(first_page["events"]), 1)
        self.assertTrue(first_page["has_more"])
        cursor = first_page["next_cursor"]

        status, _, second_page = self._get(
            platform,
            f"{EVENTS_ROUTE}?cursor={cursor}",
            sender="pub_123",
            secret=TENANT_A_SECRET,
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(second_page["events"]), 1)

        expected_ids = {
            second_page["events"][0]["lcp"]["message"]["id"],
        }

        # Two independent replay subscriptions from the same cursor produce
        # the same event set deterministically.
        delivered: list[set[str]] = []
        for key in ("replay-a", "replay-b"):
            status, _, created = self._subscribe(
                platform,
                callback_url=callback.url,
                event_types=["REJECTED"],
                key=key,
                replay_from=cursor,
            )
            self.assertEqual(status, 201, created)
            platform.process_once()
            rows = platform.store.list_event_deliveries(created["subscription_id"])
            delivered.append({row["event_id"] for row in rows})
        self.assertEqual(delivered[0], delivered[1])
        self.assertEqual(delivered[0], expected_ids)

    def test_expired_cursor_is_an_explicit_error(self) -> None:
        platform = self._platform(cursor_ttl=60)
        callback = self._callback()
        self._ingest_lead(
            platform, lead_id="lead_w3_old", sender="pub_123", key="w3-old"
        )
        self._make_event(
            platform, lead_id="lead_w3_old", sender="pub_123", key="evt-old"
        )
        status, _, page = self._get(
            platform,
            f"{EVENTS_ROUTE}?limit=1",
            sender="pub_123",
            secret=TENANT_A_SECRET,
        )
        self.assertEqual(status, 200)
        # Age the underlying event beyond the configured cursor TTL, then
        # mint a cursor from it: the cursor carries that old timestamp.
        old = format_iso_datetime(datetime.now(timezone.utc) - timedelta(seconds=120))
        with platform.store.transaction() as db:
            db.execute("UPDATE event_stream SET created_at = ?", (old,))
        status, _, page = self._get(
            platform,
            f"{EVENTS_ROUTE}?limit=1",
            sender="pub_123",
            secret=TENANT_A_SECRET,
        )
        self.assertEqual(status, 200)
        cursor = page["next_cursor"]

        status, _, payload = self._get(
            platform,
            f"{EVENTS_ROUTE}?cursor={cursor}",
            sender="pub_123",
            secret=TENANT_A_SECRET,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["details"]["reason"], "cursor_expired")

        status, _, payload = self._subscribe(
            platform, callback_url=callback.url, replay_from=cursor
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["details"]["reason"], "cursor_expired")

    def test_cursor_is_tenant_scoped(self) -> None:
        platform = self._platform(require_auth=True)
        self._ingest_lead(
            platform, lead_id="lead_w3_tenant", sender="pub_123", key="w3-t"
        )
        self._make_event(
            platform, lead_id="lead_w3_tenant", sender="pub_123", key="evt-t"
        )
        status, _, page = self._get(
            platform,
            f"{EVENTS_ROUTE}?limit=1",
            sender="pub_123",
            secret=TENANT_A_SECRET,
        )
        self.assertEqual(status, 200)
        cursor = page["next_cursor"]
        # Sequences are allocated per tenant: tenant_a's cursor cannot name
        # tenant_b's stream (tenant_b has no events at that point), and no
        # tenant_a event is ever exposed to tenant_b.
        status, _, payload = self._get(
            platform,
            f"{EVENTS_ROUTE}?cursor={cursor}",
            sender="buyer_7",
            secret=TENANT_B_SECRET,
        )
        self.assertEqual(status, 400)
        self.assertIn(
            payload["errors"][0]["details"]["reason"],
            {"cursor_expired", "cursor_unknown"},
        )
        status, _, page_b = self._get(
            platform, EVENTS_ROUTE, sender="buyer_7", secret=TENANT_B_SECRET
        )
        self.assertEqual(status, 200)
        self.assertEqual(page_b["events"], [])

    def test_subscription_only_receives_its_own_tenant_events(self) -> None:
        platform = self._platform(require_auth=True)
        callback = self._callback()
        self._ingest_lead(
            platform, lead_id="lead_w3_iso", sender="pub_123", key="w3-iso"
        )
        # buyer_7 is in tenant_b; the event belongs to tenant_a.
        status, _, created = self._subscribe(
            platform,
            callback_url=callback.url,
            event_types=["REJECTED"],
            filter_values={"lead_id": "lead_w3_iso"},
        )
        self.assertEqual(status, 201, created)
        self._make_event(platform, lead_id="lead_w3_iso", sender="pub_123")
        platform.process_once()
        rows = platform.store.list_event_deliveries(created["subscription_id"])
        self.assertEqual(rows, [])
        self.assertEqual(callback.requests, [])


if __name__ == "__main__":
    unittest.main()

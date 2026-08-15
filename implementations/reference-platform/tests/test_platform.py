from __future__ import annotations

import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import tempfile
import time
import unittest

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.matching import match_offer
from lcp_platform.messages import _envelope
from lcp_platform.router import Platform, RequestError
from lcp_platform.service import PlatformService
from lcp_platform.storage import Store, now_iso


ROOT = Path(__file__).resolve().parents[3]


class PlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.platform.upsert_credential("buyer_001", hmac_secret="buyer-secret")

    def tearDown(self) -> None:
        self.platform.close()

    def load_lead(self) -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440099"
        lead["lcp"]["message"]["idempotency_key"] = "test-lead-001"
        lead["lcp"]["payload"]["lead_id"] = "lead_test_001"
        return lead

    def offer(self, **overrides: object) -> dict:
        offer = {
            "offer_id": "mortgage-au",
            "buyer_id": "buyer_001",
            "active": True,
            "vertical": "mortgage",
            "countries": ["AU"],
            "floor_price_cents": 1500,
            "currency": "AUD",
            "require_consent_evidence": True,
            "webhook_url": "http://127.0.0.1:9999/buyer",
            "ping_timeout_seconds": 30,
        }
        offer.update(overrides)
        return offer

    def test_delivery_leases_prevent_double_claim(self) -> None:
        event = _envelope(
            "event",
            "platform_001",
            "buyer_001",
            {"lead_id": "lead_test_001", "event": "DELIVERED", "timestamp": now_iso()},
        )
        self.platform.store.insert_delivery(
            lead_id="lead_test_001",
            ping_id=None,
            offer_id="mortgage-au",
            buyer_id="buyer_001",
            kind="event",
            envelope=event,
            webhook_url="http://127.0.0.1/webhook",
        )
        first = self.platform.store.claim_due_deliveries("worker-1")
        second = self.platform.store.claim_due_deliveries("worker-2")
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_external_secret_file_can_authenticate_without_db_secret(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as handle:
            json.dump(
                {
                    "pub_123": {
                        "tenant_id": "publisher_tenant",
                        "hmac_secret": "external-secret",
                        "scopes": ["lead:submit", "lead:read"],
                    }
                },
                handle,
            )
            secret_path = Path(handle.name)
        os.chmod(secret_path, 0o600)
        secure_platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                secrets_file=secret_path,
                test_mode=True,
            )
        )
        try:
            body = json.dumps(self.load_lead(), separators=(",", ":")).encode()
            timestamp = now_iso()
            key = "test-lead-001"
            headers = {
                "X-LCP-Sender-Id": "pub_123",
                "X-LCP-Timestamp": timestamp,
                "X-LCP-Idempotency-Key": key,
                "X-LCP-Signature": signature_for("external-secret", timestamp, key, body),
                "Content-Type": "application/json",
                "X-LCP-Test": "true",
            }
            status, _, response = PlatformService(secure_platform).dispatch(
                "POST", "/v1/lcp/leads", headers=headers, body=body
            )
            self.assertEqual(status, 200, response)
            self.assertEqual(response["lcp"]["payload"]["status"], "RECEIVED")
        finally:
            secure_platform.close()
            secret_path.unlink()

    def test_production_policy_rejects_insecure_webhook(self) -> None:
        secure_platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                test_mode=True,
            )
        )
        try:
            with self.assertRaises(RequestError):
                secure_platform.upsert_offer(self.offer(webhook_url="http://127.0.0.1/webhook"))
        finally:
            secure_platform.close()

    def test_idempotency_key_reuse_with_different_body_is_rejected(self) -> None:
        self.platform.upsert_offer(self.offer())
        first = self.load_lead()
        self.platform.ingest(first, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        second = self.load_lead()
        second["lcp"]["payload"]["lead_id"] = "different-lead"
        with self.assertRaises(RequestError) as context:
            self.platform.ingest(second, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertIn("Idempotency key was reused", str(context.exception))

    def test_direct_offer_creates_post_without_auction(self) -> None:
        self.platform.upsert_offer(self.offer(routing_mode="direct"))
        ack = self.platform.ingest(self.load_lead(), headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(len(self.platform.store.list_pings("lead_test_001")), 0)
        self.assertEqual(self.platform.store.get_lead("lead_test_001")["status"], "POSTED")

    def test_http_service_accepts_canonical_hmac_request(self) -> None:
        secure_platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                test_mode=True,
            )
        )
        try:
            secure_platform.upsert_credential("pub_123", hmac_secret="publisher-secret")
            body = json.dumps(self.load_lead(), separators=(",", ":")).encode()
            timestamp = now_iso()
            key = "test-lead-001"
            headers = {
                "X-LCP-Sender-Id": "pub_123",
                "X-LCP-Timestamp": timestamp,
                "X-LCP-Idempotency-Key": key,
                "X-LCP-Signature": signature_for("publisher-secret", timestamp, key, body),
                "Content-Type": "application/json",
                "X-LCP-Test": "true",
            }
            status, _, response = PlatformService(secure_platform).dispatch(
                "POST", "/v1/lcp/leads", headers=headers, body=body
            )
            self.assertEqual(status, 200)
            self.assertEqual(response["lcp"]["payload"]["status"], "RECEIVED")
        finally:
            secure_platform.close()

    def test_ping_bid_post_and_delivery_event_flow(self) -> None:
        received: list[str] = []
        platform = self.platform

        class BuyerHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                message = json.loads(self.rfile.read(length))
                message_type = message["lcp"]["message"]["type"]
                received.append(message_type)
                if message_type == "ping":
                    ping = message["lcp"]["payload"]
                    bid = _envelope(
                        "bid",
                        "buyer_001",
                        "platform_001",
                        {
                            "ping_id": ping["ping_id"],
                            "decision": "accept",
                            "bid_price_cents": 2200,
                            "currency": ping["currency"],
                            "estimated_contact_seconds": 45,
                        },
                        correlation_id=message["lcp"]["message"]["id"],
                        test=True,
                    )
                    platform.submit_bid(
                        bid,
                        headers={"X-LCP-Test": "true"},
                        raw_body=json.dumps(bid, separators=(",", ":")).encode(),
                    )
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), BuyerHandler)
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            offer = self.offer(
                offer_id="auction-test",
                ping_timeout_seconds=1,
                webhook_url=f"http://127.0.0.1:{server.server_port}/webhook",
            )
            self.platform.upsert_offer(offer)
            self.platform.ingest(self.load_lead(), headers={"X-LCP-Test": "true"}, raw_body=b"{}")
            self.platform.process_once()
            time.sleep(1.2)
            self.platform.process_once()
            self.platform.process_once()
            self.platform.process_once()
            self.assertIn("ping", received)
            self.assertIn("post", received)
            self.assertIn("event", received)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_matching_returns_explainable_reasons(self) -> None:
        lead = self.load_lead()["lcp"]["payload"]
        result = match_offer(
            self.offer(require_verified_phone=True),
            lead,
        )
        self.assertFalse(result.matched)
        self.assertIn("phone_not_verified", result.reasons)

    def test_intake_persists_and_creates_non_pii_ping(self) -> None:
        self.platform.upsert_offer(self.offer())
        lead = self.load_lead()
        ack = self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")

        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(ack["lcp"]["payload"]["lead_id"], "lead_test_001")
        pings = self.platform.store.list_pings("lead_test_001")
        self.assertEqual(len(pings), 1)
        ping = json.loads(pings[0]["envelope_json"])
        self.assertEqual(ping["lcp"]["message"]["type"], "ping")
        self.assertNotIn("consumer", ping["lcp"]["payload"])
        ping_json = json.dumps(ping)
        self.assertNotIn('"email":', ping_json)
        self.assertNotIn('"first_name":', ping_json)
        self.assertNotIn('"last_name":', ping_json)
        self.assertEqual(
            self.platform.store.get_lead("lead_test_001")["status"], "PINGED"
        )
        decisions = self.platform.store.list_match_decisions("lead_test_001")
        self.assertEqual(decisions[0]["offer_id"], "mortgage-au")
        self.assertTrue(decisions[0]["matched"])

    def test_idempotent_intake_returns_duplicate_ack(self) -> None:
        self.platform.upsert_offer(self.offer())
        lead = self.load_lead()
        first = self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        second = self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(first["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(second["lcp"]["payload"]["status"], "DUPLICATE")

    def test_health_endpoints_do_not_expose_data(self) -> None:
        service = PlatformService(self.platform)
        status, _, live = service.dispatch("GET", "/health/live")
        self.assertEqual((status, live), (200, {"status": "ok"}))
        status, _, ready = service.dispatch("GET", "/health/ready")
        self.assertEqual((status, ready), (200, {"status": "ready"}))

    def test_security_defaults_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            PlatformConfig(database_path=Path(":memory:"), require_auth=False)
        with self.assertRaises(ValueError):
            PlatformConfig(
                database_path=Path(":memory:"),
                require_auth=True,
                allow_insecure_webhooks=True,
            )
        with self.assertRaises(ValueError):
            PlatformConfig(database_path=Path(":memory:"), require_auth=True)

    def test_http_requires_json_and_idempotency_headers(self) -> None:
        service = PlatformService(self.platform)
        body = json.dumps(self.load_lead(), separators=(",", ":")).encode()
        status, _, response = service.dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={"Content-Type": "application/json", "X-LCP-Test": "true"},
            body=body,
        )
        self.assertEqual(status, 401)
        self.assertEqual(response["errors"][0]["code"], "LCP-012")
        status, _, response = service.dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={
                "Content-Type": "text/plain",
                "X-LCP-Test": "true",
                "X-LCP-Idempotency-Key": "test-lead-001",
            },
            body=body,
        )
        self.assertEqual(status, 415)
        self.assertEqual(response["errors"][0]["code"], "LCP-001")

    def test_durable_routing_job_recovers_after_intake_boundary(self) -> None:
        self.platform.upsert_offer(self.offer())
        lead = self.load_lead()
        self.assertTrue(self.platform.store.insert_lead(lead, status="NEW"))
        self.assertEqual(self.platform.store.get_lead("lead_test_001")["status"], "NEW")
        self.platform.process_once()
        self.assertEqual(len(self.platform.store.list_pings("lead_test_001")), 1)
        self.assertEqual(self.platform.store.get_lead("lead_test_001")["status"], "PINGED")

    def test_persisted_envelopes_are_authenticated_and_encrypted(self) -> None:
        key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
        store = Store(Path(":memory:"), pii_encryption_key=key)
        try:
            lead = self.load_lead()
            self.assertTrue(store.insert_lead(lead, status="NEW"))
            row = store.get_lead("lead_test_001")
            self.assertTrue(row["envelope_json"].startswith("enc:v1:"))
            self.assertNotIn("jane.smith@example.com", row["envelope_json"])
            self.assertEqual(store.decode_envelope(row["envelope_json"])["lcp"]["payload"]["consumer"]["email"], "jane.smith@example.com")
        finally:
            store.close()

    def test_erasure_redacts_pii_and_cancels_delivery(self) -> None:
        self.platform.upsert_offer(self.offer(routing_mode="direct"))
        self.platform.ingest(
            self.load_lead(),
            headers={"X-LCP-Test": "true"},
            raw_body=b"{}",
        )
        self.platform.erase_lead("lead_test_001", actor_id="privacy_operator")
        lead = self.platform.store.get_lead("lead_test_001")
        self.assertEqual(lead["status"], "ERASED")
        self.assertNotIn("jane.smith@example.com", lead["envelope_json"])
        payload = self.platform.store.decode_envelope(lead["envelope_json"])["lcp"]["payload"]
        self.assertNotIn("consumer", payload)
        delivery = self.platform.store._connection.execute(
            "SELECT status, envelope_json FROM deliveries WHERE lead_id = ?",
            ("lead_test_001",),
        ).fetchone()
        self.assertEqual(delivery["status"], "REDACTED")
        self.assertNotIn("jane.smith@example.com", delivery["envelope_json"])
        audit = self.platform.store.list_audit_events("lead", "lead_test_001")
        self.assertIn("lead.erased", [event["action"] for event in audit])

    def test_production_endpoint_rejects_test_traffic(self) -> None:
        key = base64.urlsafe_b64encode(b"p" * 32).decode("ascii")
        secure_platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                pii_encryption_key=key,
            )
        )
        try:
            with self.assertRaises(RequestError) as context:
                secure_platform.ingest(
                    self.load_lead(),
                    headers={"X-LCP-Test": "true"},
                    raw_body=b"{}",
                )
            self.assertEqual(context.exception.code, "LCP-013")
        finally:
            secure_platform.close()


if __name__ == "__main__":
    unittest.main()

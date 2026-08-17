from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from uuid import uuid4

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
        # The checked-in example intentionally demonstrates an historical
        # expiry. Routing tests use a non-expiring intake and cover expiry in
        # dedicated cases below.
        lead["lcp"]["payload"].pop("expiry", None)
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

    def test_lifecycle_events_enforce_transitions_roles_and_idempotency(self) -> None:
        from lcp_platform.storage import InvalidStatusTransition

        publisher_id = "publisher_lifecycle"
        buyer_id = "buyer_lifecycle"
        ping_only_buyer = "buyer_ping_only"
        for sender_id in (publisher_id, buyer_id, ping_only_buyer):
            self.platform.upsert_credential(sender_id, hmac_secret=f"{sender_id}-secret", scopes=[])

        lead = self.load_lead()
        lead["lcp"]["message"]["sender_id"] = publisher_id
        lead_id = lead["lcp"]["payload"]["lead_id"]
        self.assertTrue(self.platform.store.insert_lead(lead, status="POSTED"))
        with self.assertRaises(InvalidStatusTransition):
            self.platform.store.update_lead_status(lead_id, "CONVERTED")

        post = _envelope(
            "post",
            "platform_001",
            buyer_id,
            {"lead_id": lead_id, "offer_id": "lifecycle-offer"},
            test=True,
        )
        self.platform.store.insert_delivery(
            lead_id=lead_id,
            ping_id=None,
            offer_id="lifecycle-offer",
            buyer_id=buyer_id,
            kind="post",
            envelope=post,
            webhook_url="http://127.0.0.1:9/post",
        )
        self.platform.store._connection.execute(
            "UPDATE deliveries SET status = 'DELIVERED' WHERE message_id = ?",
            (post["lcp"]["message"]["id"],),
        )
        ping_only = _envelope(
            "ping",
            "platform_001",
            ping_only_buyer,
            {"ping_id": "ping-only-lifecycle", "lead_id": lead_id},
            test=True,
        )
        self.platform.store.insert_delivery(
            lead_id=lead_id,
            ping_id="ping-only-lifecycle",
            offer_id="ping-only-offer",
            buyer_id=ping_only_buyer,
            kind="ping",
            envelope=ping_only,
            webhook_url="http://127.0.0.1:9/ping",
        )
        self.platform.store._connection.execute(
            "UPDATE deliveries SET status = 'DELIVERED' WHERE message_id = ?",
            (ping_only["lcp"]["message"]["id"],),
        )

        invalid = _envelope(
            "event",
            buyer_id,
            "platform_001",
            {"lead_id": lead_id, "event": "CONVERTED", "timestamp": now_iso()},
            test=True,
        )
        with self.assertRaises(RequestError) as invalid_error:
            self.platform.submit_event(invalid, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(invalid_error.exception.code, "LCP-004")
        self.assertEqual(self.platform.store.get_lead(lead_id)["status"], "POSTED")

        accepted = _envelope(
            "event",
            buyer_id,
            "platform_001",
            {"lead_id": lead_id, "event": "ACCEPTED", "timestamp": now_iso()},
            test=True,
        )
        first_ack = self.platform.submit_event(
            accepted, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )
        self.assertEqual(first_ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self.platform.store.get_lead(lead_id)["status"], "ACCEPTED")
        duplicate_ack = self.platform.submit_event(
            accepted, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )
        self.assertEqual(duplicate_ack["lcp"]["payload"]["status"], "DUPLICATE")

        conflicting = deepcopy(accepted)
        conflicting["lcp"]["payload"]["event"] = "CONVERTED"
        with self.assertRaises(RequestError) as conflict_error:
            self.platform.submit_event(
                conflicting, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
            )
        self.assertEqual(conflict_error.exception.code, "LCP-005")

        ping_event = _envelope(
            "event",
            ping_only_buyer,
            "platform_001",
            {"lead_id": lead_id, "event": "ACCEPTED", "timestamp": now_iso()},
            test=True,
        )
        with self.assertRaises(RequestError) as role_error:
            self.platform.submit_event(
                ping_event, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
            )
        self.assertEqual(role_error.exception.code, "LCP-002")

    def test_legal_transition_table_matches_conformance_graph(self) -> None:
        # The runtime table and the conformance runner's published graph are
        # hand-maintained duplicates. Keep them identical: the implementation-
        # only NEW -> POSTED direct-delivery edge is a code-level carve-out in
        # update_lead_status(reason="direct_delivery"), not part of either table.
        from lcp_platform.storage import LEGAL_LEAD_TRANSITIONS

        vectors_path = str(ROOT / "test-vectors")
        sys.path.insert(0, vectors_path)
        try:
            from conformance import LEGAL_TRANSITIONS
        finally:
            sys.path.remove(vectors_path)
        self.assertEqual(LEGAL_LEAD_TRANSITIONS, LEGAL_TRANSITIONS)

    def test_lifecycle_transitions_write_audit_records(self) -> None:
        publisher_id = "publisher_audit_events"
        buyer_id = "buyer_audit_events"
        for sender_id in (publisher_id, buyer_id):
            self.platform.upsert_credential(
                sender_id, hmac_secret=f"{sender_id}-secret", scopes=[]
            )

        def delivered_lead(lead_id: str, message_id: str) -> None:
            lead = self.load_lead()
            lead["lcp"]["message"]["sender_id"] = publisher_id
            lead["lcp"]["message"]["id"] = message_id
            lead["lcp"]["message"]["idempotency_key"] = f"{lead_id}-key"
            lead["lcp"]["payload"]["lead_id"] = lead_id
            self.assertTrue(self.platform.store.insert_lead(lead, status="POSTED"))
            post = _envelope(
                "post",
                "platform_001",
                buyer_id,
                {"lead_id": lead_id, "offer_id": "audit-offer"},
                test=True,
            )
            self.platform.store.insert_delivery(
                lead_id=lead_id,
                ping_id=None,
                offer_id="audit-offer",
                buyer_id=buyer_id,
                kind="post",
                envelope=post,
                webhook_url="http://127.0.0.1:9/post",
            )
            self.platform.store._connection.execute(
                "UPDATE deliveries SET status = 'DELIVERED' WHERE message_id = ?",
                (post["lcp"]["message"]["id"],),
            )

        def audit_rows(lead_id: str) -> list[dict[str, str]]:
            rows = self.platform.store._connection.execute(
                """
                SELECT action, actor_id, metadata_json FROM audit_events
                WHERE resource_id = ? ORDER BY created_at
                """,
                (lead_id,),
            ).fetchall()
            return [dict(row) for row in rows]

        accepted_id = "lead_audit_accepted_001"
        delivered_lead(accepted_id, "550e8400-e29b-41d4-a716-446655440201")
        accepted = _envelope(
            "event",
            buyer_id,
            "platform_001",
            {"lead_id": accepted_id, "event": "ACCEPTED", "timestamp": now_iso()},
            test=True,
        )
        ack = self.platform.submit_event(
            accepted, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        accepted_audit = [
            row for row in audit_rows(accepted_id) if row["action"] == "lead.accepted"
        ]
        self.assertEqual(len(accepted_audit), 1)
        self.assertEqual(accepted_audit[0]["actor_id"], buyer_id)
        self.assertEqual(
            json.loads(accepted_audit[0]["metadata_json"])["previous_status"],
            "POSTED",
        )

        rejected_id = "lead_audit_rejected_001"
        delivered_lead(rejected_id, "550e8400-e29b-41d4-a716-446655440202")
        rejected = _envelope(
            "event",
            buyer_id,
            "platform_001",
            {"lead_id": rejected_id, "event": "REJECTED", "timestamp": now_iso()},
            test=True,
        )
        ack = self.platform.submit_event(
            rejected, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        rejected_audit = [
            row for row in audit_rows(rejected_id) if row["action"] == "lead.rejected"
        ]
        self.assertEqual(len(rejected_audit), 1)
        self.assertEqual(rejected_audit[0]["actor_id"], buyer_id)

        invalid_id = "lead_audit_invalid_001"
        delivered_lead(invalid_id, "550e8400-e29b-41d4-a716-446655440203")
        invalid = _envelope(
            "event",
            buyer_id,
            "platform_001",
            {"lead_id": invalid_id, "event": "CONVERTED", "timestamp": now_iso()},
            test=True,
        )
        with self.assertRaises(RequestError) as context:
            self.platform.submit_event(
                invalid, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
            )
        self.assertEqual(context.exception.code, "LCP-004")
        self.assertEqual(self.platform.store.get_lead(invalid_id)["status"], "POSTED")
        self.assertFalse(
            [
                row
                for row in audit_rows(invalid_id)
                if row["action"] == "lead.converted"
            ]
        )

    def test_matching_returns_explainable_reasons(self) -> None:
        lead = self.load_lead()["lcp"]["payload"]
        result = match_offer(
            self.offer(require_verified_phone=True),
            lead,
        )
        self.assertFalse(result.matched)
        self.assertIn("phone_not_verified", result.reasons)

    def test_delivery_window_filters_vertical_and_channel(self) -> None:
        lead = self.load_lead()["lcp"]["payload"]
        now = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
        window = {
            "timezone": "UTC",
            "available_from": "09:00",
            "available_to": "17:00",
            "days": ["mon"],
            "verticals": ["mortgage"],
            "channels": ["form"],
        }
        matched = match_offer(self.offer(delivery_windows=[window]), lead, now=now)
        self.assertTrue(matched.matched, matched.reasons)

        wrong_vertical = dict(window, verticals=["insurance"])
        result = match_offer(
            self.offer(delivery_windows=[wrong_vertical]),
            lead,
            now=now,
        )
        self.assertFalse(result.matched)
        self.assertIn("outside_delivery_window", result.reasons)

        wrong_channel = dict(window, channels=["call"])
        result = match_offer(
            self.offer(delivery_windows=[wrong_channel]),
            lead,
            now=now,
        )
        self.assertFalse(result.matched)
        self.assertIn("outside_delivery_window", result.reasons)

        outside_hours = match_offer(
            self.offer(delivery_windows=[window]),
            lead,
            now=datetime(2026, 8, 17, 18, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(outside_hours.matched)
        self.assertIn("outside_delivery_window", outside_hours.reasons)

    def test_retention_boundaries_stop_expired_and_unconsented_routing(self) -> None:
        self.platform.upsert_offer(self.offer(offer_id="retention-offer"))
        expired = self.load_lead()
        expired["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440101"
        expired["lcp"]["message"]["idempotency_key"] = "expired-lead-001"
        expired["lcp"]["payload"]["lead_id"] = "lead_expired_001"
        expired["lcp"]["payload"]["expiry"] = {"expires_at": "2020-01-01T00:00:00Z"}
        self.platform.ingest(expired, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(self.platform.store.get_lead("lead_expired_001")["status"], "EXPIRED")
        self.assertEqual(self.platform.store.list_pings("lead_expired_001"), [])

        consent_expired = self.load_lead()
        consent_expired["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440102"
        consent_expired["lcp"]["message"]["idempotency_key"] = "expired-consent-001"
        consent_expired["lcp"]["payload"]["lead_id"] = "lead_consent_expired_001"
        consent_expired["lcp"]["payload"]["compliance"]["consent_expires_at"] = "2020-01-01T00:00:00Z"
        self.platform.ingest(consent_expired, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(self.platform.store.list_pings("lead_consent_expired_001"), [])
        self.platform.process_once()
        consent_row = self.platform.store.get_lead("lead_consent_expired_001")
        self.assertEqual(consent_row["suppressed"], 1)
        self.assertTrue(self.platform.store.is_lead_suppressed("lead_consent_expired_001"))

        purpose_offer = self.offer(
            offer_id="purpose-offer",
            extensions={"lcp.platform.required_consent_purposes": ["marketing"]},
        )
        self.platform.upsert_offer(purpose_offer)
        purpose_lead = self.load_lead()
        purpose_lead["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440103"
        purpose_lead["lcp"]["message"]["idempotency_key"] = "purpose-mismatch-001"
        purpose_lead["lcp"]["payload"]["lead_id"] = "lead_purpose_mismatch_001"
        purpose_lead["lcp"]["payload"]["compliance"]["consent_purposes"] = ["email"]
        self.platform.ingest(purpose_lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        purpose_decision = next(
            decision for decision in self.platform.store.list_match_decisions("lead_purpose_mismatch_001")
            if decision["offer_id"] == "purpose-offer"
        )
        self.assertFalse(purpose_decision["matched"])
        self.assertIn("consent_purpose_missing:marketing", purpose_decision["reasons"])

    def test_consent_events_trigger_suppression_and_erasure(self) -> None:
        publisher_id = "publisher_privacy_events"
        self.platform.upsert_credential(publisher_id, hmac_secret="privacy-events-secret", scopes=[])
        lead = self.load_lead()
        lead["lcp"]["message"]["sender_id"] = publisher_id
        lead["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440104"
        lead["lcp"]["message"]["idempotency_key"] = "privacy-events-lead-001"
        lead["lcp"]["payload"]["lead_id"] = "lead_privacy_events_001"
        self.assertTrue(self.platform.store.insert_lead(lead, status="POSTED"))

        withdrawn = _envelope(
            "event",
            publisher_id,
            "platform_001",
            {"lead_id": "lead_privacy_events_001", "event": "CONSENT_WITHDRAWN", "timestamp": now_iso()},
            test=True,
        )
        withdrawal_ack = self.platform.submit_event(
            withdrawn, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )
        self.assertEqual(withdrawal_ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertTrue(self.platform.store.is_lead_suppressed("lead_privacy_events_001"))

        erasure = _envelope(
            "event",
            publisher_id,
            "platform_001",
            {"lead_id": "lead_privacy_events_001", "event": "ERASURE_REQUEST", "timestamp": now_iso()},
            test=True,
        )
        erasure_ack = self.platform.submit_event(
            erasure, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )
        self.assertEqual(erasure_ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self.platform.store.get_lead("lead_privacy_events_001")["status"], "ERASED")

    def test_lead_status_is_projected_by_requester_role(self) -> None:
        publisher_id = "publisher_status_view"
        winner_id = "buyer_status_winner"
        non_winner_id = "buyer_status_nonwinner"
        lead = self.load_lead()
        lead["lcp"]["message"]["sender_id"] = publisher_id
        lead["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440105"
        lead["lcp"]["message"]["idempotency_key"] = "status-view-lead-001"
        lead["lcp"]["payload"]["lead_id"] = "lead_status_view_001"
        self.assertTrue(self.platform.store.insert_lead(lead, status="POSTED"))
        self.platform.store.insert_match_decision(
            lead_id="lead_status_view_001", offer_id="winner-offer", buyer_id=winner_id,
            matched=True, reasons=[],
        )
        self.platform.store.insert_match_decision(
            lead_id="lead_status_view_001", offer_id="nonwinner-offer", buyer_id=non_winner_id,
            matched=False, reasons=["outside_delivery_window"],
        )
        self.platform.store.record_payable(
            offer_id="winner-offer", lead_id="lead_status_view_001", buyer_id=winner_id,
            month_key="2026-08", channel="form", status="payable", price_cents=2200, currency="AUD",
        )
        self.platform.store.record_payable(
            offer_id="nonwinner-offer", lead_id="lead_status_view_001", buyer_id=non_winner_id,
            month_key="2026-08", channel="form", status="not_payable", reason="not_winner",
        )
        ping = _envelope("ping", "platform_001", non_winner_id, {"ping_id": "status-ping-001"}, test=True)
        self.platform.store.insert_delivery(
            lead_id="lead_status_view_001", ping_id="status-ping-001", offer_id="nonwinner-offer",
            buyer_id=non_winner_id, kind="ping", envelope=ping, webhook_url="http://127.0.0.1:9/ping",
        )
        post = _envelope("post", "platform_001", winner_id, {"lead_id": "lead_status_view_001"}, test=True)
        self.platform.store.insert_delivery(
            lead_id="lead_status_view_001", ping_id=None, offer_id="winner-offer",
            buyer_id=winner_id, kind="post", envelope=post, webhook_url="http://127.0.0.1:9/post",
        )
        self.platform.store._connection.execute(
            "UPDATE deliveries SET status = 'DELIVERED' WHERE message_id = ?",
            (post["lcp"]["message"]["id"],),
        )

        publisher_view = self.platform.lead_status("lead_status_view_001", requester_id=publisher_id)
        self.assertEqual(publisher_view["view"], "publisher")
        self.assertEqual(
            {item["buyer_id"] for item in publisher_view["match_decisions"]},
            {winner_id, non_winner_id},
        )
        winner_view = self.platform.lead_status("lead_status_view_001", requester_id=winner_id)
        self.assertEqual(winner_view["view"], "buyer")
        self.assertEqual({item["buyer_id"] for item in winner_view["match_decisions"]}, {winner_id})
        self.assertEqual({item["buyer_id"] for item in winner_view["payable_records"]}, {winner_id})
        with self.assertRaises(RequestError):
            self.platform.authorize_lead_read("lead_status_view_001", non_winner_id)
        with self.assertRaises(RequestError):
            self.platform.lead_status("lead_status_view_001", requester_id=non_winner_id)

    def test_indexed_offer_candidates_are_conservative_and_reindex_updates(self) -> None:
        payload = self.load_lead()["lcp"]["payload"]
        payload["attributes"] = {
            "vertical": "home_services",
            "service_type": "roofing",
        }
        payload["location"] = {
            "country_code": "AU",
            "state_region": "NSW",
            "postal_code": "2000",
        }
        offers = [
            {
                "offer_id": "candidate-roofing-au",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "home_services",
                "countries": ["AU"],
                "attribute_in": {"service_type": ["roofing"]},
            },
            {
                "offer_id": "candidate-gutters-au",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "home_services",
                "countries": ["AU"],
                "attribute_in": {"service_type": ["gutters"]},
            },
            {
                "offer_id": "candidate-roofing-us",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "home_services",
                "countries": ["US"],
                "attribute_in": {"service_type": ["roofing"]},
            },
            {
                "offer_id": "candidate-uncertain",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "home_services",
                "countries": ["AU"],
                "extensions": None,
            },
        ]
        for offer in offers:
            self.platform.store.upsert_offer(offer)

        candidates = {
            offer["offer_id"]
            for offer in self.platform.store.list_offer_candidates(
                payload,
                tenant_id="default",
            )
        }
        self.assertEqual(
            candidates,
            {"candidate-roofing-au", "candidate-uncertain"},
        )

        updated = dict(offers[0])
        updated["countries"] = ["US"]
        self.platform.store.upsert_offer(updated)
        candidates_after_update = {
            offer["offer_id"]
            for offer in self.platform.store.list_offer_candidates(
                payload,
                tenant_id="default",
            )
        }
        self.assertNotIn("candidate-roofing-au", candidates_after_update)
        self.assertIn("candidate-uncertain", candidates_after_update)

    def test_offer_discovery_filters_active_tenant_and_vertical(self) -> None:
        offers = [
            {
                "offer_id": "discovery-default-mortgage",
                "buyer_id": "buyer_001",
                "tenant_id": "tenant-a",
                "vertical": "mortgage",
                "active": True,
                "countries": ["AU"],
            },
            {
                "offer_id": "discovery-default-solar",
                "buyer_id": "buyer_001",
                "tenant_id": "tenant-a",
                "vertical": "solar",
                "active": True,
                "countries": ["AU"],
            },
            {
                "offer_id": "discovery-other-tenant",
                "buyer_id": "buyer_001",
                "tenant_id": "tenant-b",
                "vertical": "mortgage",
                "active": True,
                "countries": ["AU"],
            },
            {
                "offer_id": "discovery-inactive",
                "buyer_id": "buyer_001",
                "tenant_id": "tenant-a",
                "vertical": "mortgage",
                "active": False,
                "countries": ["AU"],
            },
        ]
        for offer in offers:
            self.platform.store.upsert_offer(offer)

        tenant_offers = self.platform.store.list_offers(tenant_id="tenant-a")
        self.assertEqual(
            {offer["offer_id"] for offer in tenant_offers},
            {"discovery-default-mortgage", "discovery-default-solar"},
        )
        mortgage_offers = self.platform.store.list_offers(
            vertical="mortgage", tenant_id="tenant-a"
        )
        self.assertEqual(
            [offer["offer_id"] for offer in mortgage_offers],
            ["discovery-default-mortgage"],
        )
        self.assertEqual(
            [offer["offer_id"] for offer in self.platform.store.list_offers(active_only=False, tenant_id="tenant-a")],
            ["discovery-default-mortgage", "discovery-default-solar", "discovery-inactive"],
        )

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

    def test_expiry_uses_legal_transition_graph(self) -> None:
        lead = self.load_lead()
        self.assertTrue(self.platform.store.insert_lead(lead, status="NEW"))
        self.assertTrue(self.platform.store.expire_lead("lead_test_001"))
        self.assertEqual(self.platform.store.get_lead("lead_test_001")["status"], "EXPIRED")
        self.assertFalse(self.platform.store.expire_lead("lead_test_001"))

        terminal = deepcopy(self.load_lead())
        terminal["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440100"
        terminal["lcp"]["message"]["idempotency_key"] = "terminal-expiry-test-001"
        terminal["lcp"]["payload"]["lead_id"] = "lead_terminal_expiry_001"
        self.assertTrue(self.platform.store.insert_lead(terminal, status="ACCEPTED"))
        self.assertFalse(self.platform.store.expire_lead("lead_terminal_expiry_001"))
        self.assertEqual(
            self.platform.store.get_lead("lead_terminal_expiry_001")["status"],
            "ACCEPTED",
        )

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

    def test_conditional_bid_fields_accept_reject_pass_without_price(self) -> None:
        buyer_id = "buyer_001"

        def _create_ping_and_get_id() -> tuple[str, str]:
            offer_id = f"bid-offer-{uuid4().hex[:8]}"
            self.platform.upsert_offer(self.offer(offer_id=offer_id, ping_timeout_seconds=5))
            lead = self.load_lead()
            lead["lcp"]["message"]["sender_id"] = "publisher_001"
            lead["lcp"]["message"]["id"] = str(uuid4())
            lead_id = f"bid-test-{uuid4().hex[:8]}"
            lead["lcp"]["payload"]["lead_id"] = lead_id
            lead["lcp"]["message"]["idempotency_key"] = f"bid-test-{lead_id}"
            self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
            self.platform.process_once()
            pings = self.platform.store.list_pings(lead_id=lead_id)
            self.assertTrue(pings)
            ping_id = pings[0]["ping_id"]
            ping_envelope = self.platform.store.decode_envelope(pings[0]["envelope_json"])
            return ping_id, ping_envelope["lcp"]["message"]["id"]

        # Reject bid without price — must be accepted
        ping_id, ping_msg_id = _create_ping_and_get_id()
        reject_bid = _envelope(
            "bid", buyer_id, "platform_001",
            {"ping_id": ping_id, "decision": "reject", "reject_reason": "price_too_low"},
            correlation_id=ping_msg_id,
            test=True,
        )
        ack = self.platform.submit_bid(
            reject_bid,
            headers={"X-LCP-Test": "true"},
            raw_body=json.dumps(reject_bid, separators=(",", ":")).encode(),
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        # Pass bid without price — must be accepted
        ping_id, ping_msg_id = _create_ping_and_get_id()
        pass_bid = _envelope(
            "bid", buyer_id, "platform_001",
            {"ping_id": ping_id, "decision": "pass"},
            correlation_id=ping_msg_id,
            test=True,
        )
        ack = self.platform.submit_bid(
            pass_bid,
            headers={"X-LCP-Test": "true"},
            raw_body=json.dumps(pass_bid, separators=(",", ":")).encode(),
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        # Accept bid without price — must be rejected by schema validation
        ping_id, ping_msg_id = _create_ping_and_get_id()
        accept_bid = _envelope(
            "bid", buyer_id, "platform_001",
            {"ping_id": ping_id, "decision": "accept"},
            correlation_id=ping_msg_id,
            test=True,
        )
        with self.assertRaises(RequestError) as context:
            self.platform.submit_bid(
                accept_bid,
                headers={"X-LCP-Test": "true"},
                raw_body=json.dumps(accept_bid, separators=(",", ":")).encode(),
            )
        self.assertEqual(context.exception.code, "LCP-100")


if __name__ == "__main__":
    unittest.main()

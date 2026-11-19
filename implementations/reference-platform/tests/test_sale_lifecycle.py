"""LEP-0012 sale lifecycle and LEP-0013 fail-closed regulated identifiers.

Contract pinned here:

* ``ack.disposition`` appears only when the acknowledged lead is 1.3.0 or
  later and a feature is on, and a DUPLICATE ack repeats it byte for byte;
* the platform reports a lead's sale result to its publisher as signed
  payout deltas with strictly increasing revisions (ACCEPTED / REFUNDED),
  or one REJECTED when nothing sold;
* ``lcp.test.outcome`` forces the answer on sandbox test traffic, never
  routes the lead, and is refused on live traffic with LCP-013;
* URL-template subscriptions refuse macros outside the query and outside
  the closed set, fire a signed GET only for events the platform addresses
  to the subscriber, and expand ``{payout}`` at the currency exponent;
* a regulated identifier is refused by field name (LCP-100), never echoed,
  and the lead is not stored.
"""

from __future__ import annotations

import base64
import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from lcp_platform import router as router_module
from lcp_platform import validation as validation_module
from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform, RedactedEnvelopeError, _format_payout
from lcp_platform.service import SUBSCRIBE_ROUTE, PlatformService
from lcp_platform.storage import now_iso
from lcp_platform.validation import regulated_value_errors

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_LEAD = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))
TEMPLATE_QUERY = (
    "cid={click_id}&s1={sub1}&event={event}&payout={payout}&cents={payout_cents}"
    "&cur={currency}&rev={revision}&test={test}&ext={external_id}"
)


class PostbackServer:
    """A tracker stand-in that records GET paths and headers."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append((self.path, dict(self.headers.items())))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}/pb"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class SaleLifecycleTests(unittest.TestCase):
    def _platform(self, **flags: Any) -> Platform:
        config = {
            "database_path": Path(":memory:"),
            "schema_root": ROOT / "schemas",
            "platform_id": "platform_001",
            "require_auth": False,
            "allow_insecure_webhooks": True,
            "test_mode": True,
            **flags,
        }
        platform = Platform(PlatformConfig(**config))
        self.addCleanup(platform.close)
        return platform

    @staticmethod
    def _lead(
        *, version: str = "1.3.0", test: bool = True, extensions: dict | None = None
    ) -> dict[str, Any]:
        lead = copy.deepcopy(EXAMPLE_LEAD)
        lead["lcp"]["version"] = version
        lead["lcp"]["message"].update(
            {
                "id": str(uuid4()),
                "timestamp": now_iso(),
                "idempotency_key": f"idem-{uuid4().hex}",
                "test": test,
            }
        )
        payload = lead["lcp"]["payload"]
        payload["lead_id"] = f"lead_{uuid4().hex[:12]}"
        payload.pop("expiry", None)
        if extensions is not None:
            payload["extensions"] = extensions
        return lead

    @staticmethod
    def _post(platform: Platform, envelope: dict, route: str = "/v1/lcp/leads"):
        message = envelope["lcp"]["message"]
        headers = {
            "Content-Type": "application/json",
            "X-LCP-Idempotency-Key": message["idempotency_key"],
            "X-LCP-Sender-Id": message["sender_id"],
        }
        if message.get("test"):
            headers["X-LCP-Test"] = "true"
        body = json.dumps(envelope, separators=(",", ":")).encode()
        return PlatformService(platform).dispatch("POST", route, headers=headers, body=body)

    def _sale_events(self, platform: Platform, lead_id: str) -> list[dict[str, Any]]:
        return [
            event["lcp"]["payload"]
            for event in platform.store.list_events(lead_id)
            if event["lcp"]["message"]["sender_id"] == "platform_001"
        ]

    # ── §1 disposition and its version gate ──────────────────────────────

    def test_feature_off_keeps_the_v12_ack(self) -> None:
        platform = self._platform()
        status, _, ack = self._post(platform, self._lead())
        self.assertEqual(status, 200, ack)
        self.assertNotIn("disposition", ack["lcp"]["payload"])
        self.assertNotIn("sale_disposition", platform.capabilities()["features"])

    def test_disposition_is_version_gated(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        self.assertEqual(
            platform.capabilities()["features"]["sale_disposition"], "1.3.0"
        )
        _, _, ack = self._post(platform, self._lead(version="1.2.0"))
        self.assertNotIn("disposition", ack["lcp"]["payload"])
        _, _, ack = self._post(platform, self._lead())
        self.assertEqual(ack["lcp"]["version"], "1.3.0")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(ack["lcp"]["payload"]["disposition"], {"outcome": "pending"})
        platform.validator.require_valid_envelope(ack)

    def test_duplicate_ack_repeats_the_disposition(self) -> None:
        platform = self._platform(feature_test_outcomes=True)
        lead = self._lead(extensions={"lcp.test.outcome": {"outcome": "sold", "payout_cents": 4450}})
        _, _, first = self._post(platform, lead)
        _, _, again = self._post(platform, lead)
        self.assertEqual(again["lcp"]["payload"]["status"], "DUPLICATE")
        self.assertEqual(
            json.dumps(again["lcp"]["payload"]["disposition"]),
            json.dumps(first["lcp"]["payload"]["disposition"]),
        )

    # ── §3 sale events ───────────────────────────────────────────────────

    def test_unroutable_lead_is_reported_unsold_once(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead = self._lead()
        self._post(platform, lead)
        lead_id = lead["lcp"]["payload"]["lead_id"]
        events = self._sale_events(platform, lead_id)
        self.assertEqual([event["event"] for event in events], ["REJECTED"])
        self.assertEqual(
            events[0]["details"], {"rejection_reason": "no_buyer", "revision": 1}
        )
        platform._sync_sale_result(lead_id, final=True)
        self.assertEqual(len(self._sale_events(platform, lead_id)), 1)

    def test_payable_changes_become_signed_deltas_with_revisions(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead = self._lead()
        self._post(platform, lead)
        lead_id = lead["lcp"]["payload"]["lead_id"]

        def payable(offer_id: str, status: str) -> None:
            platform.store.record_payable(
                offer_id=offer_id,
                lead_id=lead_id,
                buyer_id="buyer_7",
                month_key="2026-11",
                channel="lead",
                status=status,
                price_cents=4450,
                currency="USD",
            )
            platform._sync_sale_result(lead_id)

        payable("offer_a", "payable")
        payable("offer_a", "payable")  # a repeat reports nothing new
        payable("offer_a", "disputed")  # a dispute is still owed
        payable("offer_a", "refunded")
        events = self._sale_events(platform, lead_id)
        self.assertEqual(
            [(e["event"], e["details"].get("payout_cents"), e["details"]["revision"]) for e in events],
            [("REJECTED", None, 1), ("ACCEPTED", 4450, 2), ("REFUNDED", -4450, 3)],
        )
        self.assertEqual(sum(e["details"].get("payout_cents", 0) for e in events), 0)

    # ── §6 forced test outcomes ──────────────────────────────────────────

    def test_forced_sale_answers_sold_and_never_routes(self) -> None:
        platform = self._platform(feature_test_outcomes=True)
        self.assertEqual(platform.capabilities()["features"]["test_outcomes"], "1.3.0")
        lead = self._lead(
            extensions={
                "lcp.test.outcome": {"outcome": "sold", "payout_cents": 4450},
                "lcp.affiliate.tracking": {"click_id": "cl_8f3a2b"},
            }
        )
        status, _, ack = self._post(platform, lead)
        self.assertEqual(status, 200, ack)
        self.assertEqual(
            ack["lcp"]["payload"]["disposition"],
            {"outcome": "sold", "payout_cents": 4450, "currency": "USD"},
        )
        lead_id = lead["lcp"]["payload"]["lead_id"]
        self.assertEqual(platform.store.get_lead(lead_id)["status"], "ACCEPTED")
        self.assertEqual(platform.store.payable_for_lead(lead_id), [])
        (event,) = self._sale_events(platform, lead_id)
        self.assertEqual(event["event"], "ACCEPTED")
        self.assertEqual(event["details"]["payout_cents"], 4450)
        self.assertEqual(event["details"]["revision"], 1)

    def test_forced_pending_and_rejections(self) -> None:
        platform = self._platform(feature_test_outcomes=True)
        for outcome, expected, event in (
            (
                "pending",
                {"outcome": "pending"},
                ("ACCEPTED", {"payout_cents": 1000, "currency": "USD", "revision": 1}),
            ),
            (
                "unsold",
                {"outcome": "unsold"},
                ("REJECTED", {"rejection_reason": "no_buyer", "revision": 1}),
            ),
            (
                "price_rejected",
                {"outcome": "price_rejected", "counter_price_cents": 1000, "currency": "USD"},
                ("REJECTED", {"rejection_reason": "price_too_high", "revision": 1}),
            ),
        ):
            lead = self._lead(extensions={"lcp.test.outcome": {"outcome": outcome}})
            _, _, ack = self._post(platform, lead)
            self.assertEqual(ack["lcp"]["payload"]["disposition"], expected)
            (sent,) = self._sale_events(platform, lead["lcp"]["payload"]["lead_id"])
            details = {key: value for key, value in sent["details"].items() if not key.endswith("_at")}
            self.assertEqual((sent["event"], details), event)

    def test_forced_test_lead_never_gets_a_routing_job(self) -> None:
        platform = self._platform(feature_test_outcomes=True)
        lead = self._lead(extensions={"lcp.test.outcome": {"outcome": "sold"}})
        self._post(platform, lead)
        lead_id = lead["lcp"]["payload"]["lead_id"]
        self.assertFalse(platform.store.routing_job_pending(lead_id))
        self.assertEqual(platform.store.claim_routing_jobs("worker_x"), [])

    def test_forced_outcome_needs_a_sandbox_even_with_the_flag(self) -> None:
        platform = self._platform(
            test_mode=False,
            require_auth=True,
            allow_insecure_webhooks=False,
            pii_encryption_key=base64.b64encode(b"k" * 32).decode(),
            feature_test_outcomes=True,
        )
        lead = self._lead(extensions={"lcp.test.outcome": {"outcome": "sold"}})
        self.assertIsNone(platform._forced_test_outcome(lead))
        self.assertIsNone(platform._lead_disposition(lead))

    def test_forced_outcome_on_live_traffic_is_lcp013(self) -> None:
        platform = self._platform(
            test_mode=False,
            require_auth=True,
            allow_insecure_webhooks=False,
            pii_encryption_key=base64.b64encode(b"k" * 32).decode(),
            feature_test_outcomes=True,
        )
        platform.upsert_credential(
            "pub_123", tenant_id="tenant_a", scopes=["lead:submit"], hmac_secret="s3cret"
        )
        self.assertNotIn("test_outcomes", platform.capabilities()["features"])
        lead = self._lead(test=False, extensions={"lcp.test.outcome": {"outcome": "sold"}})
        body = json.dumps(lead, separators=(",", ":")).encode()
        timestamp = now_iso()
        key = lead["lcp"]["message"]["idempotency_key"]
        status, _, body = PlatformService(platform).dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={
                "Content-Type": "application/json",
                "X-LCP-Sender-Id": "pub_123",
                "X-LCP-Idempotency-Key": key,
                "X-LCP-Timestamp": timestamp,
                "X-LCP-Signature": signature_for("s3cret", timestamp, key, body),
            },
            body=body,
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["errors"][0]["code"], "LCP-013")
        self.assertIsNone(platform.store.get_lead(lead["lcp"]["payload"]["lead_id"]))

    def test_registered_extension_shapes_are_enforced(self) -> None:
        platform = self._platform()
        for extensions in (
            {"lcp.affiliate.tracking": {"click": "x"}},
            {"lcp.affiliate.tracking": {"sub1": "jane@example.com"}},
            {"lcp.affiliate.tracking": {"click_id": "12025550143"}},
            {"lcp.test.outcome": {"outcome": "won"}},
            {"lcp.test.outcome": {"outcome": "unsold", "payout_cents": 100}},
        ):
            status, _, body = self._post(platform, self._lead(extensions=extensions))
            self.assertEqual(status, 400, extensions)
            self.assertEqual(body["errors"][0]["code"], "LCP-100")

    def test_pair_scoped_extensions_never_reach_a_buyer(self) -> None:
        platform = self._platform()
        payload = {
            "lead_id": "lead_x",
            "extensions": {
                "lcp.affiliate.tracking": {"click_id": "cl_1"},
                "lcp.test.outcome": {"outcome": "sold"},
            },
        }
        self.assertIsNone(platform._extensions_for_delivery(payload, "buyer_7"))

    # ── §4 URL-template postbacks ────────────────────────────────────────

    def _subscribe(self, platform: Platform, body: dict[str, Any]):
        envelope = {
            "lcp": {
                "version": "1.3.0",
                "message": {
                    "id": str(uuid4()),
                    "type": "subscription",
                    "timestamp": now_iso(),
                    "sender_id": "pub_123",
                    "receiver_id": "platform_001",
                    "correlation_id": None,
                    "idempotency_key": f"sub-{uuid4().hex}",
                    "test": True,
                },
                "payload": body,
            }
        }
        return self._post(platform, envelope, SUBSCRIBE_ROUTE)

    def test_url_template_registration_rules(self) -> None:
        off = self._platform(feature_events=True)
        good = {
            "event_types": ["ACCEPTED"],
            "delivery": "url_template",
            "callback_url": "https://track.example.com/pb?e={event}",
        }
        status, _, body = self._subscribe(off, good)
        self.assertEqual(status, 400, body)
        platform = self._platform(feature_events=True, feature_url_postbacks=True)
        self.assertEqual(platform.capabilities()["features"]["url_postbacks"], "1.3.0")
        for url in (
            "https://{sub1}.track.example.com/pb?e={event}",
            "https://track.example.com/pb?e={event}&m={email}",
            "https://track.example.com/pb?e={event",
            "https://track.example.com/pb?e={event}&pad=" + "a" * 2048,
        ):
            status, _, body = self._subscribe(platform, {**good, "callback_url": url})
            self.assertEqual(status, 400, url)
            self.assertEqual(body["errors"][0]["code"], "LCP-100")
        # One macro grammar: registration and expansion share the pattern.
        self.assertIs(
            router_module.URL_TEMPLATE_MACRO_RE, validation_module.URL_TEMPLATE_MACRO_RE
        )

    def test_payout_macro_uses_the_currency_exponent(self) -> None:
        self.assertEqual(_format_payout(4450, "USD"), "44.50")
        self.assertEqual(_format_payout(-4450, "USD"), "-44.50")
        self.assertEqual(_format_payout(4450, "JPY"), "4450")
        self.assertEqual(_format_payout(5, "USD"), "0.05")

    def test_postback_is_a_signed_get_only_for_platform_sale_events(self) -> None:
        tracker = PostbackServer()
        self.addCleanup(tracker.close)
        platform = self._platform(
            feature_events=True, feature_url_postbacks=True, feature_test_outcomes=True
        )
        status, _, view = self._subscribe(
            platform,
            {
                "event_types": ["ACCEPTED", "REJECTED"],
                "delivery": "url_template",
                "callback_url": f"{tracker.base}?{TEMPLATE_QUERY}",
            },
        )
        self.assertEqual(status, 201, view)
        self.assertEqual(view["delivery"], "url_template")
        lead = self._lead(
            extensions={
                "lcp.test.outcome": {"outcome": "sold", "payout_cents": 4450},
                "lcp.affiliate.tracking": {"click_id": "cl_8f3a2b", "sub1": "fb"},
            }
        )
        self._post(platform, lead)
        # The publisher's own event about its lead is not a sale result.
        own = {
            "lcp": {
                "version": "1.2.0",
                "message": {
                    "id": str(uuid4()),
                    "type": "event",
                    "timestamp": now_iso(),
                    "sender_id": "pub_123",
                    "receiver_id": "platform_001",
                    "correlation_id": None,
                    "idempotency_key": f"ev-{uuid4().hex}",
                    "test": True,
                },
                "payload": {
                    "lead_id": lead["lcp"]["payload"]["lead_id"],
                    "event": "REJECTED",
                    "timestamp": now_iso(),
                },
            }
        }
        self._post(platform, own, "/v1/lcp/events")
        platform.process_once()

        self.assertEqual(len(tracker.requests), 1, tracker.requests)
        path, headers = tracker.requests[0]
        query = {key: values[0] for key, values in parse_qs(urlsplit(path).query, keep_blank_values=True).items()}
        self.assertEqual(
            query,
            {
                "cid": "cl_8f3a2b",
                "s1": "fb",
                "event": "ACCEPTED",
                "payout": "44.50",
                "cents": "4450",
                "cur": "USD",
                "rev": "1",
                "test": "1",
                "ext": lead["lcp"]["payload"]["external_id"],
            },
        )
        secret = platform.store.event_subscription_secret(view["subscription_id"])
        expanded = f"{tracker.base}?{urlsplit(path).query}"
        self.assertEqual(
            headers["X-LCP-Signature"],
            signature_for(
                secret,
                headers["X-LCP-Timestamp"],
                headers["X-LCP-Event-Id"],
                expanded.encode(),
            ),
        )
        self.assertEqual(headers["X-LCP-Test"], "true")

    # ── LEP-0013 fail-closed gate ────────────────────────────────────────

    def test_regulated_identifier_is_refused_by_name_and_not_stored(self) -> None:
        platform = self._platform()
        self.assertNotIn("regulated_identifiers", platform.capabilities()["features"])
        lead = self._lead()
        lead["lcp"]["payload"]["attributes"] = {
            "vertical": "personal_loans",
            "schema_version": "2.0.0",
            "amount_currency": "USD",
            "loan_amount": 1000,
            "us_details": {"country_code": "US", "ssn": "987654321"},
        }
        status, _, body = self._post(platform, lead)
        self.assertEqual(status, 400, body)
        self.assertEqual(body["errors"][0]["code"], "LCP-100")
        self.assertIn("attributes.us_details.ssn", json.dumps(body))
        self.assertNotIn("987654321", json.dumps(body))
        self.assertIsNone(platform.store.get_lead(lead["lcp"]["payload"]["lead_id"]))

    # ── Review fixes: idempotent revisions, crash recovery, money rules ──

    def _stored_lead(self, platform: Platform, *, status: str = "NEW") -> str:
        """A v1.3 lead stored directly: not routed, no sale event yet."""
        lead = self._lead()
        platform.store.insert_lead(lead, status=status, routing_job=False)
        return lead["lcp"]["payload"]["lead_id"]

    @staticmethod
    def _payable(
        platform: Platform,
        lead_id: str,
        *,
        offer_id: str = "offer_a",
        status: str = "payable",
        price: int = 4450,
        currency: str = "USD",
    ) -> None:
        platform.store.record_payable(
            offer_id=offer_id,
            lead_id=lead_id,
            buyer_id="buyer_7",
            month_key="2026-11",
            channel="lead",
            status=status,
            price_cents=price,
            currency=currency,
        )

    def _summary(self, platform: Platform, lead_id: str) -> list[tuple[Any, ...]]:
        return [
            (e["event"], e["details"]["revision"], e["details"].get("payout_cents"))
            for e in self._sale_events(platform, lead_id)
        ]

    def test_concurrent_syncs_never_send_two_events_at_one_revision(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead_id = self._stored_lead(platform)
        self._payable(platform, lead_id)
        barrier = threading.Barrier(2)
        real_read = platform._sale_events
        waited: set[int] = set()

        def stale_read(lead: str) -> list[dict[str, Any]]:
            prior = real_read(lead)
            if threading.get_ident() not in waited:
                waited.add(threading.get_ident())
                barrier.wait(timeout=5)  # both workers now hold the same view
            return prior

        platform._sale_events = stale_read  # type: ignore[method-assign]
        failures: list[BaseException] = []

        def worker() -> None:
            try:
                platform._sync_sale_result(lead_id)
            except BaseException as exc:  # noqa: BLE001 - reported below
                failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(failures, [])
        self.assertEqual(self._summary(platform, lead_id), [("ACCEPTED", 1, 4450)])

    def test_a_lost_revision_race_recomputes_from_fresh_state(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead_id = self._stored_lead(platform)
        self._payable(platform, lead_id)
        platform._sync_sale_result(lead_id)
        self._payable(platform, lead_id, status="refunded")
        self._payable(platform, lead_id, offer_id="offer_b", price=3000)
        real_read = platform._sale_events
        stale = [True]

        def read(lead: str) -> list[dict[str, Any]]:
            if stale:  # the first read misses revision 1, as a racing worker's would
                stale.pop()
                return []
            return real_read(lead)

        platform._sale_events = read  # type: ignore[method-assign]
        platform._sync_sale_result(lead_id)
        self.assertEqual(
            self._summary(platform, lead_id),
            [("ACCEPTED", 1, 4450), ("REFUNDED", 2, -1450)],
        )

    def _assert_reconciled_once(self, platform: Platform, lead_id: str) -> None:
        self.assertEqual(self._sale_events(platform, lead_id), [])
        platform.process_once()  # inside the grace window, in-flight work is left alone
        self.assertEqual(self._sale_events(platform, lead_id), [])
        with mock.patch.object(router_module, "SALE_RESULT_GRACE_SECONDS", 0):
            platform.process_once()
            platform.process_once()  # settled: the mark is gone, nothing repeats
        self.assertEqual(
            [(e["event"], e["details"]) for e in self._sale_events(platform, lead_id)],
            [("REJECTED", {"rejection_reason": "no_buyer", "revision": 1})],
        )
        self.assertEqual(platform.store.list_sale_results_due("9999-12-31T23:59:59Z"), [])

    def test_crash_between_expire_and_event_is_reconciled(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead_id = self._stored_lead(platform)
        with mock.patch.object(platform, "_sync_sale_result", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                platform._expire_lead(lead_id)
        self.assertEqual(platform.store.get_lead(lead_id)["status"], "EXPIRED")
        self._assert_reconciled_once(platform, lead_id)

    def test_crash_between_auction_expiry_and_event_is_reconciled(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead_id = self._stored_lead(platform)  # no bids: the auction close expires it
        with mock.patch.object(platform, "_sync_sale_result", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                platform._route_lead(lead_id)
        self.assertEqual(platform.store.get_lead(lead_id)["status"], "EXPIRED")
        self._assert_reconciled_once(platform, lead_id)

    def test_crash_after_unroutable_intake_leaves_the_routing_job(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead = self._lead()
        lead_id = lead["lcp"]["payload"]["lead_id"]
        with mock.patch.object(platform, "_sync_sale_result", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                platform._ingest_lead(
                    lead, message=lead["lcp"]["message"], sender_id="pub_123"
                )
        self.assertTrue(platform.store.routing_job_pending(lead_id))
        self.assertEqual(self._sale_events(platform, lead_id), [])
        platform.process_once()  # the worker re-routes the lead and reports it
        self.assertFalse(platform.store.routing_job_pending(lead_id))
        self.assertEqual(self._summary(platform, lead_id), [("REJECTED", 1, None)])

    def test_crash_between_payable_and_event_is_reconciled(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead_id = self._stored_lead(platform, status="POSTED")
        platform._mark_sale_result_due(lead_id)
        self._payable(platform, lead_id)  # the crash: no sync follows
        with mock.patch.object(router_module, "SALE_RESULT_GRACE_SECONDS", 0):
            platform.process_once()
        self.assertEqual(self._summary(platform, lead_id), [("ACCEPTED", 1, 4450)])

    def test_mixed_currency_payables_are_refused_never_relabelled(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead_id = self._stored_lead(platform)
        self._payable(platform, lead_id, offer_id="offer_usd")
        self._payable(platform, lead_id, offer_id="offer_aud", price=3000, currency="AUD")
        platform._sync_sale_result(lead_id)
        self.assertEqual(self._sale_events(platform, lead_id), [])
        (refusal,) = [
            row
            for row in platform.store.list_audit_events("lead", lead_id)
            if row["action"] == "lead.sale_result_refused"
        ]
        self.assertEqual(
            refusal["metadata"],
            {"reason": "mixed_currency", "currencies": ["AUD", "USD"]},
        )

    def test_zero_price_payable_sends_no_money_event_and_no_rejection(self) -> None:
        platform = self._platform(feature_sale_disposition=True)
        lead_id = self._stored_lead(platform)
        self._payable(platform, lead_id, price=0, currency="")
        platform._sync_sale_result(lead_id, final=True)
        self.assertEqual(self._sale_events(platform, lead_id), [])

    def test_payable_lookup_by_lead_is_indexed(self) -> None:
        platform = self._platform()
        indexes = platform.store._connection.execute(
            "PRAGMA index_list(payable_records)"
        ).fetchall()
        self.assertIn("idx_payable_lead", {row[1] for row in indexes})

    def test_duplicate_after_an_insert_race_uses_the_stored_disposition(self) -> None:
        platform = self._platform(feature_sale_disposition=True, feature_test_outcomes=True)
        for extensions in ({"lcp.test.outcome": {"outcome": "sold", "payout_cents": 4450}}, None):
            lead = self._lead(extensions=extensions)
            _, _, first = self._post(platform, lead)
            real_lookup = platform.store.get_lead_by_idempotency
            calls: list[Any] = []

            def racing_lookup(*args: Any, real=real_lookup, calls=calls) -> Any:
                calls.append(args)
                return None if len(calls) == 1 else real(*args)  # the first read races the insert

            with mock.patch.object(
                platform.store, "get_lead_by_idempotency", side_effect=racing_lookup
            ):
                _, _, again = self._post(platform, lead)
            self.assertEqual(len(calls), 2, extensions)
            self.assertEqual(again["lcp"]["payload"]["status"], "DUPLICATE")
            self.assertEqual(
                json.dumps(again["lcp"]["payload"]["disposition"]),
                json.dumps(first["lcp"]["payload"]["disposition"]),
            )

    def test_postback_refuses_a_redacted_event_or_lead(self) -> None:
        tracker = PostbackServer()
        self.addCleanup(tracker.close)
        platform = self._platform(
            feature_events=True, feature_url_postbacks=True, feature_test_outcomes=True
        )
        _, _, view = self._subscribe(
            platform,
            {
                "event_types": ["ACCEPTED"],
                "delivery": "url_template",
                "callback_url": f"{tracker.base}?{TEMPLATE_QUERY}",
            },
        )
        lead = self._lead(
            extensions={
                "lcp.test.outcome": {"outcome": "sold"},
                "lcp.affiliate.tracking": {"click_id": "cl_1"},
            }
        )
        self._post(platform, lead)
        (event,) = platform.store.list_events(lead["lcp"]["payload"]["lead_id"])
        platform.store.redact_expired_envelopes("9999-12-31T23:59:59Z")
        with self.assertRaises(RedactedEnvelopeError):  # the lead is redacted
            platform._expand_postback(f"{tracker.base}?{TEMPLATE_QUERY}", event)
        platform.process_once()  # the stored event is redacted too
        self.assertEqual(tracker.requests, [])
        (delivery,) = platform.store.list_event_deliveries(view["subscription_id"])
        self.assertEqual(delivery["last_error"], "RedactedEnvelopeError")

    def test_platform_postbacks_match_the_golden_vectors(self) -> None:
        """SALE-024..029: the platform expands and signs exactly as the vectors pin."""
        suite = json.loads((ROOT / "test-vectors" / "sale-lifecycle.json").read_text("utf-8"))
        golden = [v for v in suite["vectors"] if "expanded_url" in v.get("postback", {})]
        self.assertEqual(len(golden), 6)
        for vector in golden:
            with self.subTest(vector["id"]):
                platform = self._platform()
                postback = vector["postback"]
                event = vector["payload"]
                lead = self._lead()
                lead["lcp"]["payload"].update(
                    lead_id=event["lcp"]["payload"]["lead_id"], **postback["lead"]
                )
                platform.store.insert_lead(lead, status="SOLD", routing_job=False)
                url = platform._expand_postback(postback["template"], event)
                self.assertEqual(url, postback["expanded_url"])
                if "signature_hex" in postback:
                    self.assertEqual(
                        signature_for(
                            postback["secret"],
                            postback["timestamp"],
                            event["lcp"]["message"]["id"],
                            url.encode(),
                        ),
                        postback["signature_hex"],
                    )

    def test_json_delivery_failure_records_only_the_exception_type(self) -> None:
        platform = self._platform(feature_events=True, feature_test_outcomes=True)
        _, _, view = self._subscribe(
            platform,
            {
                "event_types": ["ACCEPTED"],
                "callback_url": "http://127.0.0.1:9/hook?token=s3cr3t",
            },
        )
        self._post(platform, self._lead(extensions={"lcp.test.outcome": {"outcome": "sold"}}))
        platform.process_once()
        (delivery,) = platform.store.list_event_deliveries(view["subscription_id"])
        self.assertEqual(delivery["last_error"], "ConnectError")

    def test_regulated_gate_follows_refs_composition_and_items(self) -> None:
        schema = {
            "$defs": {
                "ssn": {"type": "string", "x-lcp-regulated": "government_id"},
                "us": {"type": "object", "properties": {"ssn": {"$ref": "#/$defs/ssn"}}},
            },
            "type": "object",
            "properties": {
                "us_details": {"allOf": [{"$ref": "#/$defs/us"}]},
                "applicants": {"type": "array", "items": {"oneOf": [{"$ref": "#/$defs/us"}]}},
                "co": {
                    "anyOf": [
                        {"properties": {"bank": {"properties": {"account_number": {"x-lcp-regulated": "bank_account"}}}}}
                    ]
                },
                "nested": {"$ref": "#"},
            },
        }
        value = {
            "us_details": {"ssn": "987654321"},
            "applicants": [{"ssn": "987654322"}, {"ssn": None}],
            "co": {"bank": {"account_number": "000123"}},
            "nested": {"us_details": {"ssn": "987654323"}},
        }
        errors = regulated_value_errors(value, schema, "attributes")
        self.assertEqual(
            [error.rsplit("(field ", 1)[1].rstrip(")") for error in errors],
            [
                "attributes.applicants[0].ssn",
                "attributes.co.bank.account_number",
                "attributes.nested.us_details.ssn",
                "attributes.us_details.ssn",
            ],
        )
        self.assertFalse({"987654321", "987654322", "000123"} & set(json.dumps(errors).split()))


if __name__ == "__main__":
    unittest.main()

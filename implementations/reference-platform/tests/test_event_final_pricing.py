"""LEP-0006 Track A — FINAL_PRICING ingest, advertisement, neutrality.

The report rides the open `event` enumeration with no schema change
(`schemas/event.json` is untouched), so the whole contract is ingest
behavior: a publisher-scoped credential may submit it, `capabilities()`
advertises it, the recommended `details` bounds reject malformed reports,
and the report is settlement-neutral — no payable row, no lifecycle
transition.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lcp_platform.config import PlatformConfig
from lcp_platform.messages import _envelope
from lcp_platform.router import _EVENT_STATUS_TARGETS, Platform, RequestError
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
PLATFORM_ID = "platform_001"
PUBLISHER_ID = "pub_final_pricing"
LEAD_ID = "lead_final_pricing_001"
PHONE = "+61412345678"


def _compact(envelope: dict) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


def _valid_details() -> dict:
    # LEP-0006 §Proposed wire shape, Track A (A-1's shape minus the PII).
    return {
        "ping_id": "ping_550e8400-1",
        "sale_scope": "external",
        "winning_price_cents": 2450,
        "currency": "AUD",
        "duration_seconds": 0,
        "sold_at": "2026-11-02T23:40:00Z",
    }


class EventFinalPricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id=PLATFORM_ID,
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.platform.upsert_credential(
            PUBLISHER_ID, hmac_secret="final-pricing-secret", scopes=[]
        )
        lead = _envelope(
            "lead",
            PUBLISHER_ID,
            PLATFORM_ID,
            {
                "lead_id": LEAD_ID,
                "channel": "form",
                "consumer": {"phone": PHONE},
                "attributes": {"vertical": "mortgage"},
            },
            test=True,
        )
        self.assertTrue(self.platform.store.insert_lead(lead, status="POSTED"))

    def tearDown(self) -> None:
        self.platform.close()

    # ─── helpers ───────────────────────────────────────────────────────────

    def _event(self, details: dict | None, **overrides: object) -> dict:
        payload: dict = {
            "lead_id": LEAD_ID,
            "event": "FINAL_PRICING",
            "timestamp": now_iso(),
        }
        if details is not None:
            payload["details"] = details
        envelope = _envelope("event", PUBLISHER_ID, PLATFORM_ID, payload, test=True)
        envelope["lcp"]["version"] = "1.1.0"
        envelope["lcp"]["payload"].update(overrides)
        return envelope

    def _submit(self, envelope: dict) -> dict:
        return self.platform.submit_event(
            envelope, headers={"X-LCP-Test": "true"}, raw_body=_compact(envelope)
        )

    # ─── Track A contract ─────────────────────────────────────────────────

    def test_publisher_final_pricing_accepted_and_advertised(self) -> None:
        capabilities = self.platform.capabilities()
        self.assertIn("FINAL_PRICING", capabilities["events"])
        self.assertIn("1.1.0", capabilities["lcp_versions"])
        # A price report asserts no lifecycle transition (LEP-0006 Track A).
        self.assertNotIn("FINAL_PRICING", _EVENT_STATUS_TARGETS)

        status_before = self.platform.store.get_lead(LEAD_ID)["status"]
        self.assertEqual(self.platform.store.payable_for_lead(LEAD_ID), [])
        details = _valid_details()
        # Deployment-specific additions ride namespaced keys inside details.
        details["acme.marketplace.final_pricing"] = {"note": "external sale"}
        envelope = self._event(details)

        ack = self._submit(envelope)

        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(ack["lcp"]["payload"]["lead_id"], LEAD_ID)
        # Stored as a report...
        event_id = envelope["lcp"]["message"]["id"]
        self.assertIsNotNone(self.platform.store.get_event(event_id))
        # ...and inert: no payable row was created and the lead did not move.
        self.assertEqual(self.platform.store.payable_for_lead(LEAD_ID), [])
        self.assertEqual(self.platform.store.get_lead(LEAD_ID)["status"], status_before)

    def test_final_pricing_bounds_reject(self) -> None:
        cases = [
            {"winning_price_cents": 24.50},  # A-2: float major units
            {"winning_price_cents": 0},
            {"winning_price_cents": 100_000_001},
            {"winning_price_cents": True},
            {"currency": "aud"},
            {"duration_seconds": 86_401},
            {"duration_seconds": -1},
            {"sold_at": "yesterday"},
            {"ping_id": 42},
        ]
        for override in cases:
            with self.subTest(override=override):
                details = _valid_details()
                details.update(override)
                envelope = self._event(details)
                with self.assertRaises(RequestError) as caught:
                    self._submit(envelope)
                self.assertEqual(caught.exception.code, "LCP-100")
                self.assertEqual(caught.exception.status_code, 400)
                self.assertEqual(self.platform.store.list_events(LEAD_ID), [])

    def test_final_pricing_rejects_pii_report(self) -> None:
        # LEP-0006 invalid example A-1: the shape validates, ingest refuses.
        details = _valid_details()
        details["consumer_phone"] = PHONE
        envelope = self._event(details)
        with self.assertRaises(RequestError) as caught:
            self._submit(envelope)
        self.assertEqual(caught.exception.code, "LCP-100")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("details.consumer_phone", str(caught.exception))
        self.assertEqual(self.platform.store.list_events(LEAD_ID), [])

    def test_final_pricing_reports_are_keyed_and_replay_collapses(self) -> None:
        """Keying and replay (LEP-0006 Track A).

        Two reports for the same `(lead_id, ping_id)` are distinct facts the
        receiver keeps in payload-`timestamp` order, so a consumer's
        latest-wins (superseding) view is derivable from the history; an
        exact re-send collapses on the event `message.id`. This platform
        stores report history — it has no single-row analytics view to
        collapse into yet, so it MUST NOT pretend to (an honest boundary,
        not a silent gap: the ordering is what the supersedes rule needs).
        """
        first = self._event(_valid_details(), timestamp="2026-11-02T23:40:00Z")
        self.assertEqual(self._submit(first)["lcp"]["payload"]["status"], "RECEIVED")
        correction = _valid_details()
        correction["winning_price_cents"] = 3100
        second = self._event(correction, timestamp="2026-11-03T01:00:00Z")
        self.assertEqual(self._submit(second)["lcp"]["payload"]["status"], "RECEIVED")

        prices = [
            event["lcp"]["payload"]["details"]["winning_price_cents"]
            for event in self.platform.store.list_events(LEAD_ID)
        ]
        self.assertEqual(prices, [2450, 3100])
        self.assertEqual(self.platform.store.payable_for_lead(LEAD_ID), [])

        # Same id, same bytes → DUPLICATE; same id, different bytes → 409.
        replay = self._submit(second)
        self.assertEqual(replay["lcp"]["payload"]["status"], "DUPLICATE")
        second["lcp"]["payload"]["details"]["winning_price_cents"] = 4100
        with self.assertRaises(RequestError) as caught:
            self._submit(second)
        self.assertEqual(caught.exception.code, "LCP-005")
        self.assertEqual(caught.exception.status_code, 409)

    def test_final_pricing_report_is_publisher_scoped(self) -> None:
        # Only the publisher of the lead may report on it; a stray
        # publisher with a valid credential is LCP-002, not a store.
        self.platform.upsert_credential(
            "pub_other", hmac_secret="other-secret", scopes=[]
        )
        envelope = _envelope(
            "event",
            "pub_other",
            PLATFORM_ID,
            {
                "lead_id": LEAD_ID,
                "event": "FINAL_PRICING",
                "timestamp": now_iso(),
                "details": _valid_details(),
            },
            test=True,
        )
        envelope["lcp"]["version"] = "1.1.0"
        with self.assertRaises(RequestError) as caught:
            self._submit(envelope)
        self.assertEqual(caught.exception.code, "LCP-002")
        self.assertEqual(self.platform.store.list_events(LEAD_ID), [])


if __name__ == "__main__":
    unittest.main()

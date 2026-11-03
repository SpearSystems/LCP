"""SPEC §4 class rule — event `details` carries no consumer PII (#198).

Pre-existing v1.0 gap, not a LEP-0006 introduction: before this fix only
`CONTACTED` ran an ingest validator (`_validate_contact_report`), so a
publisher-submitted `CONSENT_WITHDRAWN` with a plaintext phone in `details`
was accepted and the plaintext persisted in the stored event envelope
(dossier claim C8). The class rule is now enforced for every event name and
both reporting directions, while the CONTACTED report rule keeps its own
published rejection semantics.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lcp_platform.config import PlatformConfig
from lcp_platform.messages import _envelope
from lcp_platform.router import (
    _PUBLISHER_EVENT_TYPES,
    Platform,
    RequestError,
)
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
PLATFORM_ID = "platform_001"
PUBLISHER_ID = "pub_pii_class"
BUYER_ID = "buyer_pii_class"
LEAD_ID = "lead_pii_class_001"
PHONE = "+61412345678"
DIGEST = "a" * 64
SECOND_DIGEST = "b" * 64


def _compact(envelope: dict) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


class EventDetailsPiiTests(unittest.TestCase):
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
            PUBLISHER_ID, hmac_secret="pii-class-secret", scopes=[]
        )
        self.platform.upsert_credential(
            BUYER_ID, hmac_secret="pii-class-buyer-secret", scopes=[]
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
        self._deliver_post_to_buyer()

    def tearDown(self) -> None:
        self.platform.close()

    # ─── helpers ───────────────────────────────────────────────────────────

    def _deliver_post_to_buyer(self) -> None:
        post = _envelope(
            "post",
            PLATFORM_ID,
            BUYER_ID,
            {"lead_id": LEAD_ID, "price_cents": 1900, "currency": "AUD"},
            test=True,
        )
        self.platform.store.insert_delivery(
            lead_id=LEAD_ID,
            ping_id="ping_pii_class",
            offer_id="offer_pii_class",
            buyer_id=BUYER_ID,
            kind="post",
            envelope=post,
            webhook_url="http://127.0.0.1:9/buyer",
        )
        delivery = next(
            row
            for row in self.platform.store.deliveries_for_lead(LEAD_ID)
            if row["buyer_id"] == BUYER_ID and row["kind"] == "post"
        )
        self.platform.store.mark_delivery(
            delivery["delivery_id"],
            status="DELIVERED",
            attempts=1,
            next_attempt_at=now_iso(),
        )

    def _event(
        self, name: str, details: dict | None, *, sender: str = PUBLISHER_ID
    ) -> dict:
        payload: dict = {
            "lead_id": LEAD_ID,
            "event": name,
            "timestamp": now_iso(),
        }
        if details is not None:
            payload["details"] = details
        envelope = _envelope("event", sender, PLATFORM_ID, payload, test=True)
        envelope["lcp"]["version"] = "1.1.0"
        return envelope

    def _submit(self, envelope: dict) -> dict:
        return self.platform.submit_event(
            envelope, headers={"X-LCP-Test": "true"}, raw_body=_compact(envelope)
        )

    def _assert_rejected(
        self, envelope: dict, *, field: str | None = None
    ) -> RequestError:
        stored_before = len(self.platform.store.list_events(LEAD_ID))
        with self.assertRaises(RequestError) as caught:
            self._submit(envelope)
        error = caught.exception
        self.assertEqual(error.code, "LCP-100")
        self.assertEqual(error.status_code, 400)
        if field is not None:
            self.assertTrue(
                field in str(error),
                f"rejection should name the offending field {field!r}: {error}",
            )
        # A rejection is not a store: the event table did not grow.
        self.assertEqual(len(self.platform.store.list_events(LEAD_ID)), stored_before)
        return error

    # ─── the #198 repro ───────────────────────────────────────────────────

    def test_event_details_pii_reject(self) -> None:
        """LEP-0006 named obligation (ARB F2 / #198): plaintext phone/email
        shapes in `details` are ingest-rejected for EVERY publisher-reportable
        event name — not FINAL_PRICING-specifically. The name list is pinned
        to the router's own set so a new publisher event cannot slip past.
        """
        self.assertEqual(
            _PUBLISHER_EVENT_TYPES,
            {
                "REJECTED",
                "DISPUTED",
                "CONSENT_WITHDRAWN",
                "ERASURE_REQUEST",
                "FINAL_PRICING",
            },
        )
        for name in sorted(_PUBLISHER_EVENT_TYPES):
            for details in (
                {"phone": PHONE},
                {"note": "call the lead on 0412 345 678"},
                {"email": "jane.smith@example.com"},
                {"sold_at": "2026-11-02T23:40:00Z", "consumer_phone": PHONE},
            ):
                with self.subTest(event=name, details=details):
                    self._assert_rejected(self._event(name, details))

    def test_consent_withdrawn_plaintext_phone_is_rejected(self) -> None:
        # Dossier C8: accepted and persisted before the class fix.
        envelope = self._event(
            "CONSENT_WITHDRAWN", {"phone": PHONE, "withdrawn_at": now_iso()}
        )
        self._assert_rejected(envelope, field="details.phone")
        # Rejection happens before any side effect: the lead is not
        # suppressed by a report the platform refused.
        self.assertFalse(self.platform.store.is_lead_suppressed(LEAD_ID))

    def test_erasure_request_plaintext_email_is_rejected(self) -> None:
        envelope = self._event(
            "ERASURE_REQUEST",
            {"reason": "gdpr_erasure", "note": "contact jane.smith@example.com"},
        )
        self._assert_rejected(envelope, field="details.note")
        # No erasure ran on a rejected report.
        self.assertEqual(self.platform.store.get_lead(LEAD_ID)["status"], "POSTED")

    def test_nested_details_objects_are_covered(self) -> None:
        envelope = self._event(
            "DISPUTED",
            {"evidence": {"contact": {"phone": "0412 345 678"}}},
        )
        self._assert_rejected(envelope, field="details.evidence.contact.phone")

    def test_lists_of_objects_are_covered(self) -> None:
        envelope = self._event(
            "DISPUTED",
            {"calls": [{"disposition": "answered"}, {"callback": "61412345678"}]},
        )
        self._assert_rejected(envelope, field="details.calls[1].callback")

    def test_buyer_event_details_are_covered(self) -> None:
        envelope = self._event(
            "DELIVERED", {"note": "call +61 412 345 678"}, sender=BUYER_ID
        )
        self._assert_rejected(envelope, field="details.note")

    # ─── legal values stay legal ──────────────────────────────────────────

    def test_digests_and_timestamps_are_still_accepted(self) -> None:
        envelope = self._event(
            "CONSENT_WITHDRAWN",
            {
                "phone_hash": DIGEST,
                "email_hash": SECOND_DIGEST,
                "withdrawn_at": "2026-11-02T00:00:00Z",
                "withdrawn_on": "2026-11-02",
                "withdrawn_purposes": ["calls", "sms"],
            },
        )
        ack = self._submit(envelope)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        # Consent withdrawal keeps working for a compliant report.
        self.assertTrue(self.platform.store.is_lead_suppressed(LEAD_ID))

    def test_integers_and_short_ids_are_still_accepted(self) -> None:
        envelope = self._event(
            "FINAL_PRICING",
            {
                "ping_id": "ping_550e8400-1",
                "offer_id": "offer_pii_class",
                "attempts": 3,
                "note": "no contact value in this string",
                "acme.marketplace.final_pricing": {"channel": "external"},
            },
        )
        ack = self._submit(envelope)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

    # ─── CONTACTED keeps its published semantics ──────────────────────────

    def test_contacted_report_semantics_are_preserved(self) -> None:
        accepted = self._event(
            "CONTACTED",
            {"contacted_hashes": [DIGEST, SECOND_DIGEST], "via": "phone"},
            sender=BUYER_ID,
        )
        ack = self._submit(accepted)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        # [] asserts nothing (§4 report-direction rule): refused, with the
        # CONTACTED-specific rejection message, not the class-rule message.
        empty = self._event("CONTACTED", {"contacted_hashes": []}, sender=BUYER_ID)
        error = self._assert_rejected(empty)
        self.assertIn("contacted_hashes", str(error))
        self.assertIn("1 to 32", str(error))

        # Plaintext in the digest array is still the report-shape rejection.
        plaintext = self._event(
            "CONTACTED", {"contacted_hashes": [PHONE]}, sender=BUYER_ID
        )
        error = self._assert_rejected(plaintext)
        self.assertIn("per-pair digests", str(error))

        # Oversized report: 33 entries.
        oversized = self._event(
            "CONTACTED",
            {"contacted_hashes": [f"{index:064x}" for index in range(33)]},
            sender=BUYER_ID,
        )
        error = self._assert_rejected(oversized)
        self.assertIn("1 to 32", str(error))


if __name__ == "__main__":
    unittest.main()

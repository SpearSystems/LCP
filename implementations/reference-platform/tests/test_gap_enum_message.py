"""Gap-closure tests: open-enumeration passthrough and message-shape rows.

Covers SPEC-COVERAGE rows ENU-02, ENU-03, ENU-05, PRV-01, MSG-LEAD-04,
MSG-CALL-02, MSG-ACK-03 and the schema direction of MSG-POST-03.

Two lanes:

1. ``test-vectors/sdk/enum-passthrough-corpus.json`` — an SDK-agnostic
   schema corpus (validation-corpus.json style: boolean pass/fail per
   fixture). It is executed here, on the reference-platform side, because
   the shared ``test-vectors/conformance.py`` runner is frozen for this
   lane; per-language SDK harnesses can adopt the corpus by path without
   any shared-file edit.
2. End-to-end platform behavior: what SPEC §6 promises beyond the schema
   ("store and pass through", "do not treat as a validation failure")
   plus the call lifecycle event flow, the emitted ack rejection_reason,
   and the payable-ledger channel carry.

SPEC clause quotes are embedded per test (quoted text is verbatim from
SPEC.md at base commit 5d6000dbc0d70bccb0ed8ea62472c350d29755b0).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.messages import _envelope
from lcp_platform.router import Platform
from lcp_platform.validation import SchemaValidator

ROOT = Path(__file__).resolve().parents[3]
CORPUS_PATH = ROOT / "test-vectors" / "sdk" / "enum-passthrough-corpus.json"


class EnumPassthroughCorpusTests(unittest.TestCase):
    """Shared corpus: unknown open-enum values must validate; the closed
    sub-surfaces (contact_window.days, additionalProperties:false) must not.

    SPEC §6 (Open vs. closed enumerations) quote — the policy these
    fixtures pin at schema level:

        | `status` | **Open** | Store and pass through. Do not treat as a
          validation failure. |
        | `channel` | **Open** | Store and pass through. |
        | `call.status` | **Open** | Store and pass through. |
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = SchemaValidator(ROOT / "schemas")
        with CORPUS_PATH.open(encoding="utf-8") as handle:
            cls.fixtures = json.load(handle)["fixtures"]

    def test_corpus_covers_expected_rows(self) -> None:
        rules = {fixture["rule"] for fixture in self.fixtures}
        for required in (
            "enum-status",
            "enum-channel",
            "enum-call-status",
            "provenance-open-enums",
            "contact-window",
            "call-lifecycle-details",
            "ack-rejection-reason",
            "post-duplicate-resub",
        ):
            self.assertIn(required, rules)

    def test_every_fixture_matches_declared_outcome(self) -> None:
        for fixture in self.fixtures:
            with self.subTest(fixture["id"]):
                errors = self.validator.validate_envelope(fixture["envelope"])
                if fixture["expect"] == "pass":
                    self.assertEqual(errors, [], f"{fixture['id']}: {errors}")
                else:
                    self.assertTrue(
                        errors,
                        f"{fixture['id']} was expected to fail schema validation "
                        "but validated clean",
                    )


class GapEnumPlatformTests(unittest.TestCase):
    """Platform-side passthrough for the open-enum axes of SPEC §6 and the
    provenance open enums of SPEC §3."""

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                pii_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
                attachment_directory=Path(self.tempdir.name) / "attachments",
            )
        )
        self.platform.upsert_credential("buyer_001", hmac_secret="buyer-secret")
        self.platform.upsert_credential("buyer_mva", hmac_secret="buyer-mva-secret")

    def tearDown(self) -> None:
        self.platform.close()
        self.tempdir.cleanup()

    # ─── helpers ────────────────────────────────────────────────────────────

    def load_lead(self, lead_id: str) -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["id"] = str(uuid4())
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["message"]["idempotency_key"] = f"gap-enum-{lead_id}"
        lead["lcp"]["payload"]["lead_id"] = lead_id
        lead["lcp"]["payload"].pop("expiry", None)
        return lead

    def direct_offer(self, offer_id: str, **overrides: object) -> dict:
        offer = {
            "offer_id": offer_id,
            "buyer_id": "buyer_001",
            "active": True,
            "routing_mode": "direct",
            "vertical": "mortgage",
            "countries": ["AU"],
            "channels": ["form"],
            "floor_price_cents": 1500,
            "currency": "AUD",
            "require_consent_evidence": True,
            "webhook_url": "http://127.0.0.1:9/buyer",
        }
        offer.update(overrides)
        return offer

    def post_for(self, lead_id: str) -> dict:
        posts = [
            row
            for row in self.platform.store.deliveries_for_lead(lead_id)
            if row["kind"] == "post"
        ]
        self.assertEqual(len(posts), 1, f"expected exactly one post for {lead_id}")
        return self.platform.store.decode_envelope(posts[0]["envelope_json"])

    def load_call(self, lead_id: str) -> dict:
        with (ROOT / "examples" / "mva-call.json").open(encoding="utf-8") as handle:
            call = json.load(handle)
        call["lcp"]["message"]["id"] = str(uuid4())
        call["lcp"]["message"]["idempotency_key"] = f"gap-enum-{lead_id}"
        call["lcp"]["payload"]["lead_id"] = lead_id
        # The checked-in example references an uploaded attachment; the
        # passthrough lanes assert enum handling, not the attachment flow.
        call["lcp"]["payload"].pop("attachments", None)
        return call

    # ─── ENU-02: unknown lead status ────────────────────────────────────────

    def test_unknown_lead_status_accepted_and_stored_verbatim(self) -> None:
        """SPEC §6: ``status`` is **Open** — "Store and pass through. Do not
        treat as a validation failure." The SPEC's own future-value example
        is ``PARTIALLY_REFUNDED``: "This ensures that adding a new `status`
        (e.g. a future `PARTIALLY_REFUNDED`) ... is an additive MINOR
        change, not a breaking MAJOR change."

        The platform must ingest a lead carrying an unknown status without a
        validation rejection, must store the wire value unnormalized in the
        envelope of record, and must keep driving its own lifecycle column
        from the §3 transition graph (unknown wire values never enter it —
        the transition guard rejects transitions, not ingestion).
        """
        lead = self.load_lead("gap-status-001")
        lead["lcp"]["payload"]["status"] = "PARTIALLY_REFUNDED"
        ack = self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        row = self.platform.store.get_lead("gap-status-001")
        self.assertIsNotNone(row)
        stored = self.platform.store.decode_envelope(row["envelope_json"])
        self.assertEqual(stored["lcp"]["payload"]["status"], "PARTIALLY_REFUNDED")
        self.assertIn(row["status"], {"NEW", "PINGED", "POSTED", "EXPIRED"})

    # ─── ENU-03: unknown channel ────────────────────────────────────────────

    def test_unknown_channel_stored_and_carried_into_payable_ledger(self) -> None:
        """SPEC §6: ``channel`` is **Open** — "Store and pass through.";
        the example future values are ``video`` and ``iot``. SPEC §4 lead:
        "`channel` (form|chat|click|api|agent|referral)" is illustrative of
        known values, not a closed set.

        Evidence: ingestion with channel "video" succeeds, the envelope of
        record keeps the value verbatim, and the channel is carried into the
        per-pair payable ledger row untouched (no mapping onto a known
        channel).
        """
        self.platform.upsert_offer(
            self.direct_offer("gap-channel-offer", channels=["video"])
        )
        lead = self.load_lead("gap-channel-001")
        lead["lcp"]["payload"]["channel"] = "video"
        ack = self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        row = self.platform.store.get_lead("gap-channel-001")
        stored = self.platform.store.decode_envelope(row["envelope_json"])
        self.assertEqual(stored["lcp"]["payload"]["channel"], "video")
        payables = self.platform.store.payable_for_lead("gap-channel-001")
        self.assertEqual(len(payables), 1)
        self.assertEqual(payables[0]["channel"], "video")

    # ─── PRV-01: unknown provenance enums ───────────────────────────────────

    def test_unknown_provenance_values_pass_through_to_delivered_post(self) -> None:
        """SPEC §3 provenance (non-PII): `source_type` (publisher|direct|
        agent|api|referral|organic|marketplace|outbound|scraped — open
        enum), `acquisition_method` (paid_ad|organic_search|marketplace|gmb|
        directory|cold_outbound|warm_transfer|scraped|unknown — open enum),
        `incentive_type` (free_consultation|gift_card|discount_coupon|
        free_quote|cash_offer — open enum), `platform_source` (google_ads|
        facebook|tiktok|bing_ads|organic|direct, open enum).

        Evidence: a lead whose provenance carries a new value on all four
        open axes is ingested, preserved verbatim, and passed through
        byte-for-byte into the post delivered to the buyer.
        """
        self.platform.upsert_offer(self.direct_offer("gap-prov-offer"))
        lead = self.load_lead("gap-prov-001")
        unknown = {
            "received_at": "2026-08-15T10:20:01Z",
            "source_type": "connected_device",
            "acquisition_method": "podcast_sponsorship",
            "incentive_type": "crypto_airdrop",
            "platform_source": "mastodon",
            "source_id": "pub_123",
        }
        lead["lcp"]["payload"]["provenance"] = unknown
        ack = self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        post = self.post_for("gap-prov-001")
        self.assertEqual(post["lcp"]["payload"]["provenance"], unknown)

    # ─── ENU-05: unknown call.status ────────────────────────────────────────

    def test_unknown_call_status_passes_through_to_delivered_post(self) -> None:
        """SPEC §6: ``call.status`` is **Open** — "Store and pass through.";
        schemas/call.json pins the same rule: "OPEN ENUMERATION — known
        values: answered, no_answer, busy, failed, voicemail. Unknown values
        MUST be stored and passed through."

        Evidence: a call message whose call block status is a future value
        ingests, and the delivered post carries the call block verbatim.
        """
        self.platform.upsert_offer(
            {
                "offer_id": "gap-call-offer",
                "buyer_id": "buyer_mva",
                "active": True,
                "routing_mode": "direct",
                "vertical": "mva",
                "countries": ["AU"],
                "channels": ["call"],
                "floor_price_cents": 2500,
                "currency": "AUD",
                "webhook_url": "http://127.0.0.1:9/buyer",
            }
        )
        call = self.load_call("gap-callstatus-001")
        call["lcp"]["payload"]["call"]["status"] = "amd_detected"
        ack = self.platform.ingest(call, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        post = self.post_for("gap-callstatus-001")
        self.assertEqual(post["lcp"]["payload"]["call"]["status"], "amd_detected")
        row = self.platform.store.get_lead("gap-callstatus-001")
        stored = self.platform.store.decode_envelope(row["envelope_json"])
        self.assertEqual(stored["lcp"]["payload"]["call"]["status"], "amd_detected")

    # ─── MSG-CALL-02: CALL_* lifecycle events end to end ────────────────────

    def test_call_lifecycle_events_flow_end_to_end_into_payable_record(self) -> None:
        """SPEC §4 event: "For call lifecycle events such as `CALL_OFFERED`,
        `CALL_CONNECTED`, `CALL_ENDED`, and `CALL_OUTCOME`, `details` SHOULD
        include `offer_id`, `call_status`, `total_seconds`, `disposition`,
        and `transfer_status` where available. A platform uses the buyer
        offer's `payable_rules` to classify the outcome and update the
        monthly payable quota."

        Evidence: full auction flow (call intake → ping → bid → awarded
        post), then the four lifecycle events, each carrying the full
        recommended details block (with an open-valued transfer_status).
        Every event acks RECEIVED, the stored envelopes preserve the details
        verbatim (details is open by design), the payable_rules evaluation
        consumes total_seconds/call_status/disposition, and the single
        per-(offer, lead) payable record converges to payable with the
        final duration — updating the monthly quota the spec names.
        """
        self.platform.upsert_offer(
            {
                "offer_id": "gap-call-lifecycle-offer",
                "buyer_id": "buyer_mva",
                "active": True,
                "routing_mode": "auction",
                "vertical": "mva",
                "countries": ["AU"],
                "channels": ["call"],
                "floor_price_cents": 2500,
                "currency": "AUD",
                "webhook_url": "http://127.0.0.1:9/buyer",
                "ping_timeout_seconds": 1,
                "payable_rules": {
                    "mode": "call_outcome",
                    "require_call_answered": True,
                    "minimum_call_seconds": 30,
                },
                "monthly_minimum_payable": 300,
                "monthly_quota_policy": "pace",
            }
        )
        call = self.load_call("gap-lifecycle-001")
        self.platform.ingest(call, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        ping = self.platform.store.list_pings("gap-lifecycle-001")[0]
        ping_envelope = self.platform.store.decode_envelope(ping["envelope_json"])
        bid = _envelope(
            "bid",
            "buyer_mva",
            "platform_001",
            {
                "ping_id": ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": 3200,
                "currency": "AUD",
                "estimated_contact_seconds": 3,
            },
            correlation_id=ping_envelope["lcp"]["message"]["id"],
            test=True,
        )
        self.platform.submit_bid(bid, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        time.sleep(1.1)
        self.platform.process_once()

        sequences = [
            ("CALL_OFFERED", "2026-08-15T10:01:00Z", "ringing", None, None, "pending"),
            (
                "CALL_CONNECTED",
                "2026-08-15T10:02:00Z",
                "answered",
                45,
                "in_treatment",
                "transferred_to_agent",
            ),
            (
                "CALL_ENDED",
                "2026-08-15T10:03:00Z",
                "answered",
                212,
                "qualified_lead",
                "blind_transfer",
            ),
            (
                "CALL_OUTCOME",
                "2026-08-15T10:04:00Z",
                "answered",
                212,
                "qualified_lead",
                "screened_and_bridged",
            ),
        ]
        for event_name, when, call_status, seconds, disposition, transfer in sequences:
            details = {
                "offer_id": "gap-call-lifecycle-offer",
                "call_status": call_status,
                "transfer_status": transfer,
            }
            if seconds is not None:
                details["total_seconds"] = seconds
            if disposition is not None:
                details["disposition"] = disposition
            event = _envelope(
                "event",
                "buyer_mva",
                "platform_001",
                {
                    "lead_id": "gap-lifecycle-001",
                    "event": event_name,
                    "timestamp": when,
                    "details": details,
                },
                test=True,
            )
            ack = self.platform.submit_event(
                event, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
            )
            self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED", event_name)

        stored_events = self.platform.store.list_events("gap-lifecycle-001")
        self.assertEqual(len(stored_events), 4)
        last_details = stored_events[-1]["lcp"]["payload"]["details"]
        self.assertEqual(
            last_details,
            {
                "offer_id": "gap-call-lifecycle-offer",
                "call_status": "answered",
                "total_seconds": 212,
                "disposition": "qualified_lead",
                "transfer_status": "screened_and_bridged",
            },
        )
        payables = self.platform.store.payable_for_lead("gap-lifecycle-001")
        self.assertEqual(len(payables), 1)
        self.assertEqual(payables[0]["status"], "payable")
        self.assertEqual(payables[0]["call_seconds"], 212)
        quota = self.platform.quota_status("gap-call-lifecycle-offer")
        self.assertEqual(quota["summary"]["payable"], 1)

    # ─── MSG-ACK-03: emitted rejection_reason ───────────────────────────────

    def test_expired_ping_bid_rejection_emits_structured_rejection_reason(self) -> None:
        """SPEC §4 ack: "`rejection_reason` | string | Structured rejection
        reason (open enum: `invalid_phone`, `duplicate`, `out_of_geography`,
        `credit_too_low`, `already_customer`, `compliance_fail`,
        `capacity_exceeded`, etc.). Enables publisher automation."

        Evidence: a bid arriving after the ping timed out gets an ack with
        status REJECTED and an emitted ``rejection_reason`` on the wire (the
        field the spec says publishers automate on), and the emitted ack
        validates against schemas/ack.json."""
        self.platform.upsert_offer(
            self.direct_offer(
                "gap-ack-offer", routing_mode="auction", ping_timeout_seconds=1
            )
        )
        lead = self.load_lead("gap-ack-001")
        self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        ping = self.platform.store.list_pings("gap-ack-001")[0]
        ping_envelope = self.platform.store.decode_envelope(ping["envelope_json"])
        time.sleep(1.1)
        self.platform.process_once()
        bid = _envelope(
            "bid",
            "buyer_001",
            "platform_001",
            {
                "ping_id": ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": 1600,
                "currency": "AUD",
                "estimated_contact_seconds": 3,
            },
            correlation_id=ping_envelope["lcp"]["message"]["id"],
            test=True,
        )
        ack = self.platform.submit_bid(
            bid, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )
        payload = ack["lcp"]["payload"]
        self.assertEqual(payload["status"], "REJECTED")
        self.assertEqual(payload["rejection_reason"], "expired")
        errors = SchemaValidator(ROOT / "schemas").validate_envelope(ack)
        self.assertEqual(errors, [], errors)


if __name__ == "__main__":
    unittest.main()

"""LEP-0006 Track B: `extensions` on `lead`/`post`.

Wire surface: the schema change (schemas/lead.json, schemas/post.json) adds
one optional namespaced object to lead and post only — ping/bid/ack keep
their strict allowlists, so "never ping_safe" is structural. Receiver rules
the core cannot express live in ``lcp_platform/extensions.py``:

* registered ``{org}.form.questions`` shape (§B.2) — reject, never a silent
  store of a non-conforming array;
* the deployment's extension-size policy bound (32 KiB default, §B.2);
* the §B.3.4 PII class rule, sharing the #198 shape detector: reject at lead
  ingest, and redact + log at post emission so a buyer never receives
  plaintext contacts that hash-keyed suppression/erasure cannot reach.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from typing import Any
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform, RequestError
from lcp_platform.service import PlatformService
from lcp_platform.validation import ValidationError

ROOT = Path(__file__).resolve().parents[3]
PLATFORM_ID = "platform_001"
PUBLISHER_ID = "pub_123"
BUYER_ID = "buyer_001"
PHONE = "+61412345678"

FORM_NS = "lcp.form.questions"
QUESTIONS = [
    {
        "question_id": "own_or_rent",
        "question_text": "Do you own the property or are you renting?",
        "answer_text": "Own outright",
    },
    {
        "question_id": "settle_timeline",
        "question_text": "How soon would you like to settle?",
        "answer_text": "Within 60 days",
    },
]


def _compact(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


class LeadExtensionsTests(unittest.TestCase):
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
            BUYER_ID, hmac_secret="buyer-ext-secret", scopes=[]
        )

    def tearDown(self) -> None:
        self.platform.close()

    # ─── helpers ───────────────────────────────────────────────────────────

    def lead(self, *, extensions: dict[str, Any] | None = None) -> dict[str, Any]:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        message = lead["lcp"]["message"]
        message["test"] = True
        message["id"] = str(uuid4())
        message["sender_id"] = PUBLISHER_ID
        message["idempotency_key"] = f"pub-lead-ext-{uuid4().hex}"
        message["correlation_id"] = None
        lead["lcp"]["payload"]["lead_id"] = f"lead_{uuid4().hex[:12]}"
        lead["lcp"]["payload"].pop("expiry", None)
        if extensions is not None:
            lead["lcp"]["version"] = "1.1.0"
            lead["lcp"]["payload"]["extensions"] = extensions
        return lead

    def offer(self, **overrides: Any) -> dict[str, Any]:
        offer = {
            "offer_id": f"offer_{uuid4().hex[:10]}",
            "buyer_id": BUYER_ID,
            "active": True,
            "vertical": "mortgage",
            "countries": ["AU"],
            "floor_price_cents": 1500,
            "currency": "AUD",
            "webhook_url": "http://127.0.0.1:9/buyer",
            "ping_timeout_seconds": 30,
            "routing_mode": "direct",
        }
        offer.update(overrides)
        return offer

    def ingest(self, lead: dict[str, Any]) -> dict[str, Any]:
        return self.platform.ingest(
            lead, headers={"X-LCP-Test": "true"}, raw_body=_compact(lead)
        )

    def assert_rejected(self, lead: dict, *, field: str | None = None) -> RequestError:
        with self.assertRaises(RequestError) as caught:
            self.ingest(lead)
        error = caught.exception
        self.assertEqual(error.code, "LCP-100")
        self.assertEqual(error.status_code, 400)
        if field is not None:
            self.assertIn(field, str(error) + json.dumps(error.details))
        # A rejection is not a store: no lead row was written.
        self.assertIsNone(
            self.platform.store.get_lead(lead["lcp"]["payload"]["lead_id"])
        )
        return error

    def delivery_envelope(self, lead_id: str) -> dict[str, Any]:
        deliveries = self.platform.store.deliveries_for_lead(lead_id)
        self.assertEqual(len(deliveries), 1, deliveries)
        return self.platform.store.decode_envelope(deliveries[0]["envelope_json"])

    # ─── B.1/B.2: accept, persist, carry to the delivery ───────────────────

    def test_lead_extensions_persisted_and_delivered_to_v11_buyer(self) -> None:
        self.platform.upsert_credential(
            BUYER_ID,
            hmac_secret="buyer-ext-secret",
            scopes=[],
            lcp_versions=["1.1.0"],
        )
        self.platform.upsert_offer(self.offer())
        extensions = {
            FORM_NS: QUESTIONS,
            "lcp.platform.claim_language": "none",
        }
        lead = self.lead(extensions=extensions)
        ack = self.ingest(lead)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        lead_id = lead["lcp"]["payload"]["lead_id"]
        lead_row = self.platform.store.get_lead(lead_id)
        assert lead_row is not None
        stored = self.platform.store.decode_envelope(lead_row["envelope_json"])
        self.assertEqual(stored["lcp"]["payload"]["extensions"], extensions)

        post = self.delivery_envelope(lead_id)
        self.assertEqual(post["lcp"]["version"], "1.1.0")
        self.assertEqual(post["lcp"]["payload"]["extensions"], extensions)

    def test_extensions_capability_gated_off_v10_buyer(self) -> None:
        # LEP-0006 §B.1: post is a closed object, so a v1.1 field must not be
        # sent to a receiver advertising only v1.0 — gate, never rely on the
        # receiver ignoring it.
        self.platform.upsert_offer(self.offer())
        lead = self.lead(extensions={FORM_NS: QUESTIONS})
        self.ingest(lead)
        post = self.delivery_envelope(lead["lcp"]["payload"]["lead_id"])
        self.assertNotIn("extensions", post["lcp"]["payload"])
        self.assertEqual(post["lcp"]["version"], "1.0.0")

    def test_extensions_never_reach_the_ping(self) -> None:
        # §B.3.1/B.3.2: the auction path gains nothing to leak.
        self.platform.upsert_offer(self.offer(routing_mode="auction"))
        lead = self.lead(
            extensions={
                FORM_NS: [
                    {
                        "question_text": "What is the roof pitch?",
                        "answer_text": "Steep",
                    }
                ]
            }
        )
        self.ingest(lead)
        pings = self.platform.store.list_pings(lead["lcp"]["payload"]["lead_id"])
        self.assertTrue(pings, "auction offer should have produced a ping")
        for row in pings:
            envelope = self.platform.store.decode_envelope(row["envelope_json"])
            self.assertNotIn("extensions", envelope["lcp"]["payload"])
            self.assertNotIn("roof pitch", json.dumps(envelope))

    # ─── B.1: namespace format (schema-enforced, LCP-100) ──────────────────

    def test_unnamespaced_extension_key_rejected(self) -> None:
        self.assert_rejected(
            self.lead(extensions={"questions": QUESTIONS}),
            field="does not match",
        )

    def test_lcp_core_namespace_is_reserved(self) -> None:
        self.assert_rejected(
            self.lead(extensions={"lcp.core.form_questions": QUESTIONS}),
            field="lcp.core",
        )

    def test_two_segment_namespace_rejected(self) -> None:
        self.assert_rejected(self.lead(extensions={"acme.questions": QUESTIONS}))

    def test_ping_with_extensions_rejected(self) -> None:
        # The published ping allowlist code is LCP-008 PII_IN_PING (SPEC §5;
        # conformance vector L2-019 asserts it). The platform rejects the same
        # ping at the schema gate, which is the LCP-100 class for shape.
        with (ROOT / "examples" / "ping.json").open(encoding="utf-8") as handle:
            ping = json.load(handle)
        ping["lcp"]["message"]["test"] = True
        ping["lcp"]["payload"]["extensions"] = {FORM_NS: QUESTIONS}
        with self.assertRaises(ValidationError) as caught:
            self.platform.validator.require_valid_envelope(ping)
        self.assertIn("extensions", str(caught.exception))

    # ─── B.2: registered {org}.form.questions shape ────────────────────────

    def test_form_questions_entry_bound_edges(self) -> None:
        twenty_five = [
            {
                "question_id": f"q_{index}",
                "question_text": "Do you own the property?",
                "answer_text": "Own outright",
            }
            for index in range(25)
        ]
        accepted = self.lead(extensions={FORM_NS: twenty_five})
        self.assertEqual(self.ingest(accepted)["lcp"]["payload"]["status"], "RECEIVED")

        twenty_six = twenty_five + [
            {"question_text": "one too many", "answer_text": "no"}
        ]
        self.assert_rejected(
            self.lead(extensions={FORM_NS: twenty_six}), field="at most 25"
        )

    def test_shape_ceiling_exceeds_default_policy_bound(self) -> None:
        # Three-tier hierarchy of LEP-0006 §B.2 measured end to end: the
        # worst-case shape-legal block (25 x 300/1000 + 64-char ids) is above
        # the 32 KiB soft target and under the 133,140 B shape ceiling, so a
        # deployment that wants shape-legal maxima must raise the policy
        # bound (issue #197).
        worst_case = [
            {
                "question_id": f"q{index:063d}",
                "question_text": "x" * 300,
                "answer_text": "y" * 1000,
            }
            for index in range(25)
        ]
        self.assert_rejected(
            self.lead(extensions={FORM_NS: worst_case}), field="policy bound"
        )
        raised = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id=PLATFORM_ID,
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                max_extension_bytes=133_140,
            )
        )
        try:
            lead = self.lead(extensions={FORM_NS: worst_case})
            ack = raised.ingest(
                lead, headers={"X-LCP-Test": "true"}, raw_body=_compact(lead)
            )
            self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        finally:
            raised.close()

    def test_form_questions_field_bounds(self) -> None:
        cases = {
            "question_text": [{"question_text": "x" * 301, "answer_text": "ok"}],
            "answer_text": [{"question_text": "ok", "answer_text": "y" * 1001}],
            "question_id": [
                {"question_id": "Bad Id", "question_text": "ok", "answer_text": "ok"}
            ],
            "outside the registered shape": [
                {"question_text": "ok", "answer_text": "ok", "nested": {"a": 1}}
            ],
        }
        for expected, questions in cases.items():
            with self.subTest(field=expected):
                self.assert_rejected(
                    self.lead(extensions={FORM_NS: questions}), field=expected
                )

    def test_form_questions_must_be_an_array(self) -> None:
        self.assert_rejected(
            self.lead(extensions={FORM_NS: {"question_text": "x"}}),
            field="must be an array",
        )

    def test_org_open_family_is_validated_too(self) -> None:
        # The registered family is {org}.form.questions; the reference
        # platform validates every member it can recognize, not just its own
        # lcp.form.questions registration.
        over_bound = [{"question_text": "ok", "answer_text": "ok"} for _ in range(26)]
        self.assert_rejected(
            self.lead(extensions={"acme.form.questions": over_bound}),
            field="acme.form.questions",
        )

    # ─── B.2: total-size policy bound ──────────────────────────────────────

    def test_extension_total_over_policy_bound_rejected(self) -> None:
        # Unrecognized namespaces are schema-unbounded by design; the
        # receiver's scan-cheap policy bound still applies (§B.2 32 KiB
        # default, configurable).
        self.assert_rejected(
            self.lead(extensions={"acme.blob.payload": "z" * 40_000}),
            field="policy bound",
        )

    def test_extension_total_under_policy_bound_accepted(self) -> None:
        lead = self.lead(extensions={"acme.blob.payload": "z" * 30_000})
        self.assertEqual(self.ingest(lead)["lcp"]["payload"]["status"], "RECEIVED")

    # ─── B.3.4: PII in extension text ──────────────────────────────────────

    def test_contact_identifiers_in_answer_text_rejected_at_ingest(self) -> None:
        for answer in (
            "Call Jane at 0412 345 678",
            "email jane.smith@example.com",
        ):
            with self.subTest(answer=answer):
                self.assert_rejected(
                    self.lead(
                        extensions={
                            "acme.form.questions": [
                                {
                                    "question_id": "callback",
                                    "question_text": "Best number for the crew?",
                                    "answer_text": answer,
                                }
                            ]
                        }
                    ),
                    field="extensions.acme.form.questions[0].answer_text",
                )

    def test_contact_identifiers_in_unrecognized_namespace_rejected(self) -> None:
        self.assert_rejected(
            self.lead(extensions={"acme.notes.free": "ring 0412 345 678"}),
            field="extensions.acme.notes.free",
        )

    def test_post_emission_redacts_as_delivery_backstop(self) -> None:
        # Ingest rejects these values, so this drives the emission guard the
        # way a pre-rule stored lead would arrive: stored directly, no ingest
        # validation, then delivered.
        lead = self.lead(
            extensions={
                "acme.form.questions": [
                    {
                        "question_id": "callback",
                        "question_text": "Best number for the crew?",
                        "answer_text": "Call 0412 345 678, gate code 4471",
                    }
                ]
            }
        )
        self.platform.upsert_credential(
            BUYER_ID,
            hmac_secret="buyer-ext-secret",
            scopes=[],
            lcp_versions=["1.1.0"],
        )
        self.platform.store.insert_lead(lead, status="NEW")
        offer = self.offer()
        self.platform.upsert_offer(offer)
        self.assertTrue(
            self.platform._create_direct_post(lead, offer),
            "direct delivery should be created",
        )
        post = self.delivery_envelope(lead["lcp"]["payload"]["lead_id"])
        answer = post["lcp"]["payload"]["extensions"]["acme.form.questions"][0][
            "answer_text"
        ]
        self.assertNotIn("0412 345 678", answer)
        self.assertIn("[REDACTED]", answer)
        audits = self.platform.store.list_audit_events(
            "lead", lead["lcp"]["payload"]["lead_id"]
        )
        actions = [row["action"] for row in audits]
        self.assertIn("lead.extensions_redacted", actions)

    # ─── §B.3.4 retention: erasure purges extension content ────────────────

    def test_erasure_purges_extension_content(self) -> None:
        lead = self.lead(extensions={FORM_NS: QUESTIONS})
        self.ingest(lead)
        lead_id = lead["lcp"]["payload"]["lead_id"]
        self.platform.erase_lead(lead_id, actor_id="privacy")
        lead_row = self.platform.store.get_lead(lead_id)
        assert lead_row is not None
        stored = self.platform.store.decode_envelope(lead_row["envelope_json"])
        self.assertNotIn("extensions", stored["lcp"]["payload"])
        self.assertNotIn("Own outright", json.dumps(stored))

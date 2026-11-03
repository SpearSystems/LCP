"""Gap-closure mop-up tests: CMP-06 (scrub results), STA-04 (dispute
resolutions), OFR-02 (offer restriction battery).

Verbatim matrix rows (docs/SPEC-COVERAGE.md at ceb89078c868cbef8cf9a9b542fa3f3dbe3671f6):

- ``| CMP-06 | §3 scrubs[] tri-state semantics (clean/flagged/blocked; blocked
  leads are rejected) | schema | - | gap(no vectors or platform behavior for
  scrub results) |``
- ``| STA-04 | §3 dispute resolution paths DISPUTED→ACCEPTED and
  DISPUTED→REFUNDED | platform | - | gap(no platform lifecycle test exercises
  either resolution path) |``
- ``| OFR-02 | §8 restriction field battery (excluded_source_types,
  excluded_acquisition_methods, reject_incentivized, excluded_incentive_types,
  require_verified_email, max_spam_risk_score, excluded_claim_language,
  require_consent_evidence, reject_dnc_flagged, reject_litigator_flagged,
  reject_blacklist_flagged, min_data_completeness, allowed_publisher_ids,
  allowed_brand_ids) | schema+platform | - | gap(only require_verified_phone +
  attribute_equals + profiles asserted today) |``

SPEC clause quotes are embedded per test (quoted text is verbatim from SPEC.md
at base commit ceb89078c868cbef8cf9a9b542fa3f3dbe3671f6). Every disposition
here exercises shipped behavior only; nothing is asserted against unshipped
aspiration.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import unittest
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.matching import match_offer
from lcp_platform.messages import _envelope
from lcp_platform.router import Platform, RequestError
from lcp_platform.storage import InvalidStatusTransition, now_iso


ROOT = Path(__file__).resolve().parents[3]


class GapTestsBase(unittest.TestCase):
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
        # Direct-award posts require a signer secret for the buyer
        # (router._create_direct_post → auth.secret_for).
        self.platform.upsert_credential("buyer_001", hmac_secret="buyer-secret")

    def tearDown(self) -> None:
        self.platform.close()

    def load_lead(self) -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["payload"].pop("expiry", None)
        return lead

    def ingest_lead(self, lead: dict) -> dict:
        return self.platform.ingest(
            lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )


# ─── CMP-06: §3 scrubs[] tri-state semantics ────────────────────────────────


class ScrubTriStateTests(GapTestsBase):
    """CMP-06. Shipped surfaces for scrub results: schema validation on
    intake, payload storage, buyer-facing post emission, and offer-side
    rejection of non-clean results.

    SPEC §3, scrubs[] table, verbatim::

        | `result` | string | `clean` (passed), `flagged` (on a list, buyer
        decides), `blocked` (should not be contacted), `not_checked`. |

    SPEC §3, verbatim::

        The `result` values are deliberately split into three actionable
        states:
        - `clean` — the consumer is not on any list; safe to contact.
        - `flagged` — the consumer appears on a list (e.g. known litigator, DNC
          registry) but the publisher did not block the lead. The buyer decides
          whether to accept. This is important for TCPA compliance: a
          `litigator` flag doesn't mean the lead is invalid, it means the buyer
          should be aware of elevated legal risk.
        - `blocked` — the consumer is on a list and the publisher blocked
          further processing. This lead should not have been delivered. If a
          buyer receives a `blocked` lead, they should reject it immediately.
    """

    def lead_with_scrub(self, lead_id: str, scrub: dict) -> dict:
        lead = self.load_lead()
        lead["lcp"]["message"]["id"] = str(uuid4())
        lead["lcp"]["message"]["idempotency_key"] = f"scrub-{lead_id}"
        lead["lcp"]["payload"]["lead_id"] = lead_id
        lead["lcp"]["payload"]["compliance"]["scrubs"] = [scrub]
        return lead

    def stored_scrubs(self, lead_id: str) -> list[dict]:
        row = self.platform.store.get_lead(lead_id)
        envelope = self.platform.store.decode_envelope(row["envelope_json"])
        return envelope["lcp"]["payload"]["compliance"]["scrubs"]

    def test_all_actionable_states_validate_and_store_verbatim(self) -> None:
        """All four schema-legal result values pass intake and are stored
        verbatim in the lead envelope (shipped storage of scrub results)."""
        for index, result in enumerate(["clean", "flagged", "blocked", "not_checked"]):
            lead_id = f"lead_scrub_state_{index}"
            scrub = {
                "type": "dnc_national",
                "provider": "au_dncregister",
                "result": result,
                "checked_at": "2026-08-15T10:19:48Z",
            }
            ack = self.ingest_lead(self.lead_with_scrub(lead_id, scrub))
            self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
            self.assertEqual(self.stored_scrubs(lead_id), [scrub])

    def test_unknown_scrub_result_rejected_by_closed_enum(self) -> None:
        """schemas/core.json pins the closed tri-state (+not_checked) result
        enumeration; intake rejects anything else with LCP-100."""
        with self.assertRaises(RequestError) as context:
            self.ingest_lead(
                self.lead_with_scrub(
                    "lead_scrub_bad_result",
                    {
                        "type": "dnc_national",
                        "provider": "au_dncregister",
                        "result": "passed",
                    },
                )
            )
        self.assertEqual(context.exception.code, "LCP-100")
        self.assertTrue(
            any(
                "not one of" in detail and "compliance/scrubs/0/result" in detail
                for detail in context.exception.details
            ),
            context.exception.details,
        )

    def test_scrub_entry_shape_required_and_closed(self) -> None:
        """`result` is required and entries allow no extra members
        (``required: ["type", "result"]``, ``additionalProperties: false``)."""
        with self.assertRaises(RequestError) as missing:
            self.ingest_lead(
                self.lead_with_scrub("lead_scrub_no_result", {"type": "dnc_national"})
            )
        self.assertEqual(missing.exception.code, "LCP-100")
        self.assertTrue(
            any(
                "'result' is a required property" in detail
                for detail in missing.exception.details
            ),
            missing.exception.details,
        )

        with self.assertRaises(RequestError) as extra:
            self.ingest_lead(
                self.lead_with_scrub(
                    "lead_scrub_extra_field",
                    {
                        "type": "dnc_national",
                        "result": "clean",
                        "provider_secret": "leak",
                    },
                )
            )
        self.assertEqual(extra.exception.code, "LCP-100")
        self.assertTrue(
            any(
                "Additional properties" in detail for detail in extra.exception.details
            ),
            extra.exception.details,
        )

    def test_scrub_type_is_open_enumeration_passthrough(self) -> None:
        """SPEC §3, scrubs[] table, verbatim::

            | `type` | string | Scrub type: `dnc_national`, `dnc_state`, ... Open enumeration. |

        An unknown scrub type stores and passes through untouched."""
        scrub = {
            "type": "litigator_alliance_v2",
            "provider": "la_v2",
            "result": "flagged",
            "checked_at": "2026-08-15T10:19:48Z",
        }
        ack = self.ingest_lead(self.lead_with_scrub("lead_scrub_open_type", scrub))
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self.stored_scrubs("lead_scrub_open_type"), [scrub])

    def test_flagged_scrub_result_emitted_to_buyer_in_post(self) -> None:
        """Shipped emission path: build_post forwards the full compliance
        block (scrubs included) so the buyer can exercise the §3 `flagged`
        decision — "The buyer decides whether to accept."""
        lead = self.lead_with_scrub(
            "lead_scrub_flagged_post",
            {
                "type": "dnc_national",
                "provider": "au_dncregister",
                "result": "flagged",
                "checked_at": "2026-08-15T10:19:48Z",
            },
        )
        self.platform.upsert_offer(
            {
                "offer_id": "mortgage-au",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "mortgage",
                "countries": ["AU"],
                "floor_price_cents": 1500,
                "currency": "AUD",
                "webhook_url": "http://127.0.0.1:9999/buyer",
                "routing_mode": "direct",
            }
        )
        ack = self.ingest_lead(lead)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(
            self.platform.store.get_lead("lead_scrub_flagged_post")["status"], "POSTED"
        )
        posts = [
            self.platform.store.decode_envelope(row["envelope_json"])
            for row in self.platform.store.deliveries_for_lead(
                "lead_scrub_flagged_post"
            )
            if row["kind"] == "post"
        ]
        self.assertEqual(len(posts), 1)
        emitted = posts[0]["lcp"]["payload"]["compliance"]["scrubs"]
        self.assertEqual(
            emitted,
            [
                {
                    "type": "dnc_national",
                    "provider": "au_dncregister",
                    "result": "flagged",
                    "checked_at": "2026-08-15T10:19:48Z",
                }
            ],
        )

    def test_restriction_flags_reject_every_non_clean_result(self) -> None:
        """The shipped offer-side reading of "blocked leads are rejected":
        reject_dnc/litigator/blacklist accept only ``result == "clean"``;
        `flagged`, `blocked`, `not_checked` and an absent entry all fail
        (fail-closed — SPEC §8: "If true, buyer will not accept leads with
        `dnc_status = flagged` or `blocked`" — the platform additionally
        treats `not_checked`/missing as non-clean, a conservative superset)."""
        battery = {
            "dnc_national": "dnc_not_clean",
            "litigator": "litigator_not_clean",
            "blacklist": "blacklist_not_clean",
        }
        all_flags = self_offer(
            reject_dnc_flagged=True,
            reject_litigator_flagged=True,
            reject_blacklist_flagged=True,
        )
        for scrub_type, reason in battery.items():
            for state in ["flagged", "blocked", "not_checked"]:
                payload = self_lead_payload()
                payload["compliance"]["scrubs"] = [
                    {"type": scrub_type, "result": state}
                ]
                result_obj = match_offer(all_flags, payload)
                self.assertIn(reason, result_obj.reasons, (scrub_type, state))
            # all three clean passes the full flag set
            matched = match_offer(all_flags, self_lead_payload())
            self.assertTrue(matched.matched, matched.reasons)
        # absent entry is treated as not clean too
        payload = self_lead_payload()
        payload["compliance"]["scrubs"] = []
        absent = match_offer(self_offer(reject_dnc_flagged=True), payload)
        self.assertIn("dnc_not_clean", absent.reasons)


def self_lead_payload() -> dict:
    """Fully compliant mortgage lead payload for matcher-unit assertions."""
    return {
        "channel": "form",
        "location": {
            "country_code": "AU",
            "state_region": "NSW",
            "postal_code": "2000",
        },
        "compliance": {
            "consent_purposes": ["calls", "sms"],
            "consent_evidence": [
                {
                    "type": "verified_consent",
                    "provider": "internal",
                    "token_or_url": "evd_abc123",
                }
            ],
            "scrubs": [
                {"type": "dnc_national", "result": "clean"},
                {"type": "litigator", "result": "clean"},
                {"type": "blacklist", "result": "clean"},
            ],
        },
        "provenance": {
            "source_type": "publisher",
            "acquisition_method": "paid_ad",
            "is_incentivized": False,
            "brand_id": "brand_x",
        },
        "lead_quality": {
            "verified_phone": True,
            "verified_email": True,
            "spam_risk_score": 10,
            "data_completeness": "rich",
        },
        "attributes": {
            "vertical": "mortgage",
            "loan_type": "refinance",
            "loan_amount_cents": 65000000,
        },
        "extensions": {"lcp.platform.claim_language": "no prohibited claims"},
    }


def self_offer(**overrides: object) -> dict:
    offer = {
        "offer_id": "battery-offer",
        "buyer_id": "buyer_001",
        "active": True,
        "vertical": "mortgage",
        "countries": ["AU"],
        "floor_price_cents": 1000,
        "currency": "AUD",
    }
    offer.update(overrides)
    return offer


# ─── STA-04: §3 dispute resolution paths ────────────────────────────────────


class DisputeResolutionLifecycleTests(GapTestsBase):
    """STA-04. Both §3 resolution paths driven through the platform
    lifecycle event harness (submit_event), asserting the shipped role gates,
    transition enforcement, audit trail and payable-ledger updates.

    SPEC §3, status transition table (dispute edges), verbatim::

         │                    │                   │      ├─► DISPUTED ──► REFUNDED §
         │                    │                   │      │      └────► ACCEPTED (resolved)

    SPEC §3, verbatim::

        **Notable resolution path:** `DISPUTED → ACCEPTED` (a dispute can
        resolve in the buyer's favor) and `DISPUTED → REFUNDED` (dispute
        resolves in the publisher's favor, lead is refunded).

    SPEC §3, verbatim::

        Terminal states are marked with §. Non-terminal states can transition
        per the legal edges below. All unspecified transitions are invalid
        (error `LCP-004 INVALID_STATUS_TRANSITION`).
    """

    def setUp(self) -> None:
        super().setUp()
        self.publisher_id = "publisher_dispute"
        self.buyer_id = "buyer_dispute"
        self.admin_id = "ops_admin_dispute"
        self.offer_id = "dispute-lifecycle-offer"
        for sender_id in (self.publisher_id, self.buyer_id, self.admin_id):
            scopes = ["platform:admin"] if sender_id == self.admin_id else []
            self.platform.upsert_credential(
                sender_id, hmac_secret=f"{sender_id}-secret", scopes=scopes
            )

    def delivered_lead(self, lead_id: str, message_id: str) -> None:
        lead = self.load_lead()
        lead["lcp"]["message"]["sender_id"] = self.publisher_id
        lead["lcp"]["message"]["id"] = message_id
        lead["lcp"]["message"]["idempotency_key"] = f"{lead_id}-key"
        lead["lcp"]["payload"]["lead_id"] = lead_id
        self.assertTrue(self.platform.store.insert_lead(lead, status="POSTED"))
        self.platform.upsert_offer(
            {
                "offer_id": self.offer_id,
                "buyer_id": self.buyer_id,
                "active": True,
                "vertical": "mortgage",
                "countries": ["AU"],
                "floor_price_cents": 1500,
                "currency": "AUD",
                "webhook_url": "http://127.0.0.1:9999/buyer",
            }
        )
        post = _envelope(
            "post",
            "platform_001",
            self.buyer_id,
            {
                "lead_id": lead_id,
                "offer_id": self.offer_id,
                "price_cents": 1500,
                "currency": "AUD",
            },
            test=True,
        )
        self.platform.store.insert_delivery(
            lead_id=lead_id,
            ping_id=None,
            offer_id=self.offer_id,
            buyer_id=self.buyer_id,
            kind="post",
            envelope=post,
            webhook_url="http://127.0.0.1:9/post",
        )
        self.platform.store._connection.execute(
            "UPDATE deliveries SET status = 'DELIVERED' WHERE message_id = ?",
            (post["lcp"]["message"]["id"],),
        )

    def submit(self, sender_id: str, lead_id: str, event_name: str) -> dict:
        envelope = _envelope(
            "event",
            sender_id,
            "platform_001",
            {
                "lead_id": lead_id,
                "event": event_name,
                "timestamp": now_iso(),
                **(
                    {"details": {"dispute_reason": "duplicate"}}
                    if event_name == "DISPUTED"
                    else {}
                ),
            },
            test=True,
        )
        return self.platform.submit_event(
            envelope, headers={"X-LCP-Test": "true"}, raw_body=b"{}"
        )

    def status(self, lead_id: str) -> str:
        return self.platform.store.get_lead(lead_id)["status"]

    def audit(self, lead_id: str, action: str) -> list[dict]:
        rows = self.platform.store._connection.execute(
            "SELECT action, actor_id, metadata_json FROM audit_events"
            " WHERE resource_id = ? AND action = ? ORDER BY created_at",
            (lead_id, action),
        ).fetchall()
        return [dict(row) for row in rows]

    def test_dispute_resolution_in_buyers_favor_returns_to_accepted(self) -> None:
        """DISPUTED → ACCEPTED ("a dispute can resolve in the buyer's
        favor"). Both hops run through submit_event; each acks RECEIVED, the
        projected status follows the graph, and the audit trail records
        previous_status on the resolution hop."""
        lead_id = "lead_dispute_resolve_accepted"
        self.delivered_lead(lead_id, "550e8400-e29b-41d4-a716-446655440301")
        self.assertEqual(
            self.submit(self.buyer_id, lead_id, "ACCEPTED")["lcp"]["payload"]["status"],
            "RECEIVED",
        )
        self.assertEqual(self.status(lead_id), "ACCEPTED")

        ack = self.submit(self.buyer_id, lead_id, "DISPUTED")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self.status(lead_id), "DISPUTED")

        ack = self.submit(self.buyer_id, lead_id, "ACCEPTED")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self.status(lead_id), "ACCEPTED")

        disputes = self.audit(lead_id, "lead.disputed")
        self.assertEqual(len(disputes), 1)
        self.assertEqual(
            json.loads(disputes[0]["metadata_json"])["previous_status"], "ACCEPTED"
        )
        accepteds = self.audit(lead_id, "lead.accepted")
        self.assertEqual(len(accepteds), 2)
        self.assertEqual(
            sorted(
                json.loads(row["metadata_json"])["previous_status"] for row in accepteds
            ),
            ["DISPUTED", "POSTED"],
        )

    def test_dispute_resolution_to_refund_is_admin_only_and_terminal(self) -> None:
        """DISPUTED → REFUNDED ("dispute resolves in the publisher's favor,
        lead is refunded"). Shipped role gate: `REFUNDED` is in neither
        _BUYER_EVENT_TYPES nor _PUBLISHER_EVENT_TYPES, so only a
        platform:admin sender may submit it (LCP-002 otherwise). REFUNDED is
        terminal (`REFUNDED §`): any onward transition is LCP-004."""
        lead_id = "lead_dispute_refunded"
        self.delivered_lead(lead_id, "550e8400-e29b-41d4-a716-446655440302")
        self.submit(self.buyer_id, lead_id, "ACCEPTED")
        self.submit(self.buyer_id, lead_id, "DISPUTED")
        self.assertEqual(self.status(lead_id), "DISPUTED")

        with self.assertRaises(RequestError) as role_error:
            self.submit(self.buyer_id, lead_id, "REFUNDED")
        self.assertEqual(role_error.exception.code, "LCP-002")
        self.assertEqual(self.status(lead_id), "DISPUTED")

        ack = self.submit(self.admin_id, lead_id, "REFUNDED")
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self.status(lead_id), "REFUNDED")

        # Terminal: "REFUNDED §" — no outgoing transitions.
        with self.assertRaises(RequestError) as terminal_error:
            self.submit(self.admin_id, lead_id, "ACCEPTED")
        self.assertEqual(terminal_error.exception.code, "LCP-004")
        self.assertEqual(self.status(lead_id), "REFUNDED")

        refunds = self.audit(lead_id, "lead.refunded")
        self.assertEqual(len(refunds), 1)
        self.assertEqual(refunds[0]["actor_id"], self.admin_id)
        self.assertEqual(
            json.loads(refunds[0]["metadata_json"])["previous_status"], "DISPUTED"
        )

    def test_dispute_resolutions_update_the_payable_ledger(self) -> None:
        """Shipped settlement behavior on each resolution path: DISPUTED
        flips the payable record to `disputed` and REFUNDED flips it to
        `refunded` (record_payable upsert on the same offer/lead row), while
        DISPUTED → ACCEPTED restores a `payable` row."""
        refunded_lead = "lead_dispute_payable_refund"
        self.delivered_lead(refunded_lead, "550e8400-e29b-41d4-a716-446655440303")
        self.submit(self.buyer_id, refunded_lead, "ACCEPTED")
        row = self.platform.store.payable_for_lead(refunded_lead)
        self.assertEqual(
            [(r["offer_id"], r["status"], r["reason"]) for r in row],
            [(self.offer_id, "payable", "buyer_acceptance")],
        )

        self.submit(self.buyer_id, refunded_lead, "DISPUTED")
        row = self.platform.store.payable_for_lead(refunded_lead)
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["status"], "disputed")
        self.assertEqual(row[0]["reason"], "lifecycle_disputed")

        self.submit(self.admin_id, refunded_lead, "REFUNDED")
        row = self.platform.store.payable_for_lead(refunded_lead)
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["status"], "refunded")
        self.assertEqual(row[0]["reason"], "lifecycle_refunded")

        reopened = "lead_dispute_payable_reopen"
        self.delivered_lead(reopened, "550e8400-e29b-41d4-a716-446655440304")
        self.submit(self.buyer_id, reopened, "ACCEPTED")
        self.submit(self.buyer_id, reopened, "DISPUTED")
        self.submit(self.buyer_id, reopened, "ACCEPTED")
        row = self.platform.store.payable_for_lead(reopened)
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["status"], "payable")

    def test_dispute_requires_accepted_precondition(self) -> None:
        """ "All unspecified transitions are invalid (error LCP-004
        INVALID_STATUS_TRANSITION)": POSTED → DISPUTED is not an edge in the
        §3 table, and REFUNDED → DISPUTED re-dispute is not either."""
        lead_id = "lead_dispute_illegal"
        self.delivered_lead(lead_id, "550e8400-e29b-41d4-a716-446655440305")
        with self.assertRaises(RequestError) as early:
            self.submit(self.buyer_id, lead_id, "DISPUTED")
        self.assertEqual(early.exception.code, "LCP-004")
        self.assertEqual(self.status(lead_id), "POSTED")

        self.submit(self.buyer_id, lead_id, "ACCEPTED")
        self.submit(self.buyer_id, lead_id, "DISPUTED")
        self.submit(self.admin_id, lead_id, "REFUNDED")
        self.assertEqual(self.status(lead_id), "REFUNDED")
        with self.assertRaises(RequestError) as redispute:
            self.submit(self.admin_id, lead_id, "DISPUTED")
        self.assertEqual(redispute.exception.code, "LCP-004")
        self.assertEqual(self.status(lead_id), "REFUNDED")

    def test_runtime_graph_pins_dispute_edges(self) -> None:
        """The shipped runtime graph carries exactly the two §3 resolution
        edges out of DISPUTED and nothing out of REFUNDED."""
        from lcp_platform.storage import LEGAL_LEAD_TRANSITIONS

        self.assertEqual(
            LEGAL_LEAD_TRANSITIONS["ACCEPTED"], {"CONVERTED", "DISPUTED", "ARCHIVED"}
        )
        self.assertEqual(LEGAL_LEAD_TRANSITIONS["DISPUTED"], {"REFUNDED", "ACCEPTED"})
        self.assertEqual(LEGAL_LEAD_TRANSITIONS["REFUNDED"], set())
        lead_id = "lead_graph_probe"
        self.delivered_lead(lead_id, "550e8400-e29b-41d4-a716-446655440306")
        with self.assertRaises(InvalidStatusTransition):
            self.platform.store.update_lead_status(lead_id, "DISPUTED")


# ─── OFR-02: §8 restriction field battery ───────────────────────────────────


class OfferRestrictionBatteryTests(GapTestsBase):
    """OFR-02. Branch-by-branch assertions for the restrictions shipped in
    lcp_platform/matching.py (match_offer, offer_is_available) plus the
    extension operators in offer_extensions.py. Branches asserted today
    before this file: require_verified_phone (reject side), attribute_equals,
    profiles (in/equals/between + service_area postal), delivery_windows
    (vertical/channel/hours). Everything else below is new coverage.

    SPEC §8, verbatim::

        The platform filters against offer restrictions before pinging:

    and, per-field, verbatim table rows (quoted in each test)."""

    def test_baseline_compliant_lead_matches_unrestricted_offer(self) -> None:
        result = match_offer(self_offer(), self_lead_payload())
        self.assertTrue(result.matched, result.reasons)
        self.assertEqual(result.reasons, ())

    def test_excluded_source_types(self) -> None:
        """§8: ``| excluded_source_types | array<string> | Source types the
        buyer rejects (e.g. `["scraped", "outbound", "marketplace"]`). |``"""
        payload = self_lead_payload()
        payload["provenance"]["source_type"] = "marketplace"
        result = match_offer(self_offer(excluded_source_types=["marketplace"]), payload)
        self.assertIn("source_type_excluded", result.reasons)

        accepted = match_offer(self_offer(excluded_source_types=["scraped"]), payload)
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_excluded_acquisition_methods(self) -> None:
        """§8: ``| excluded_acquisition_methods | array<string> | Acquisition
        methods the buyer rejects (e.g. `["cold_outbound", "scraped"]`). |``"""
        payload = self_lead_payload()
        payload["provenance"]["acquisition_method"] = "cold_outbound"
        result = match_offer(
            self_offer(excluded_acquisition_methods=["cold_outbound"]), payload
        )
        self.assertIn("acquisition_method_excluded", result.reasons)

        accepted = match_offer(
            self_offer(excluded_acquisition_methods=["scraped"]), payload
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_reject_incentivized(self) -> None:
        """§8: ``| reject_incentivized | boolean | If true, buyer will not
        accept incentivized leads (`is_incentivized = true`). |``

        Shipped as fail-closed: the lead must declare
        ``is_incentivized: False`` — ``not False`` (including absent) is
        rejected. That is a conservative superset of the spec row, asserted
        here as shipped."""
        flagged = self_lead_payload()
        flagged["provenance"]["is_incentivized"] = True
        result = match_offer(self_offer(reject_incentivized=True), flagged)
        self.assertIn("incentivized_lead", result.reasons)

        undeclared = self_lead_payload()
        del undeclared["provenance"]["is_incentivized"]
        undeclared_result = match_offer(
            self_offer(reject_incentivized=True), undeclared
        )
        self.assertIn("incentivized_lead", undeclared_result.reasons)

        clean = match_offer(self_offer(reject_incentivized=True), self_lead_payload())
        self.assertTrue(clean.matched, clean.reasons)

    def test_excluded_incentive_types(self) -> None:
        """§8: ``| excluded_incentive_types | array<string> | Specific
        incentive types the buyer rejects (e.g. `["free_consultation"]`). |``"""
        payload = self_lead_payload()
        payload["provenance"]["is_incentivized"] = True
        payload["provenance"]["incentive_type"] = "gift_card"
        result = match_offer(
            self_offer(excluded_incentive_types=["gift_card"]), payload
        )
        self.assertIn("incentive_type_excluded", result.reasons)

        accepted = match_offer(
            self_offer(excluded_incentive_types=["free_consultation"]), payload
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_require_verified_email_accept_and_reject(self) -> None:
        """§8: ``| require_verified_email | boolean | If true, buyer requires
        `verified_email = true`. |``

        Also pins the second shipped acceptance route —
        ``lead_quality.verification.email_verified_at`` — which mirrors the
        §3 lead_quality.verification schema (email_verified_at)."""
        payload = self_lead_payload()
        payload["lead_quality"]["verified_email"] = False
        result = match_offer(self_offer(require_verified_email=True), payload)
        self.assertIn("email_not_verified", result.reasons)

        via_timestamp = self_lead_payload()
        del via_timestamp["lead_quality"]["verified_email"]
        via_timestamp["lead_quality"]["verification"] = {
            "email_verified_at": "2026-08-15T10:19:00Z"
        }
        accepted = match_offer(self_offer(require_verified_email=True), via_timestamp)
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_require_verified_phone_accept_via_verification_timestamp(self) -> None:
        """§8: ``| require_verified_phone | boolean | If true, buyer requires
        `verified_phone = true`. |`` — the accept-side ``_verified`` branch
        via ``phone_verified_at`` had no prior assertion."""
        payload = self_lead_payload()
        del payload["lead_quality"]["verified_phone"]
        payload["lead_quality"]["verification"] = {
            "phone_verified_at": "2026-08-15T10:19:00Z"
        }
        accepted = match_offer(self_offer(require_verified_phone=True), payload)
        self.assertTrue(accepted.matched, accepted.reasons)

        missing = self_lead_payload()
        del missing["lead_quality"]["verified_phone"]
        rejected = match_offer(self_offer(require_verified_phone=True), missing)
        self.assertIn("phone_not_verified", rejected.reasons)

    def test_max_spam_risk_score_band_boundary_and_fail_closed(self) -> None:
        """§8: ``| max_spam_risk_score | integer | Buyer will not accept
        leads with `spam_risk_score` above this value. |``

        Boundary (score == max passes), above (fails), and the shipped
        fail-closed branch for a missing/non-integer score."""
        at_limit = match_offer(self_offer(max_spam_risk_score=10), self_lead_payload())
        self.assertTrue(at_limit.matched, at_limit.reasons)

        above = self_lead_payload()
        above["lead_quality"]["spam_risk_score"] = 11
        result = match_offer(self_offer(max_spam_risk_score=10), above)
        self.assertIn("spam_risk_too_high", result.reasons)

        undeclared = self_lead_payload()
        del undeclared["lead_quality"]["spam_risk_score"]
        missing = match_offer(self_offer(max_spam_risk_score=10), undeclared)
        self.assertIn("spam_risk_too_high", missing.reasons)

    def test_min_data_completeness_band_order(self) -> None:
        """§8: ``| min_data_completeness | string | Buyer requires at least
        this completeness level (`minimal`, `standard`, `rich`). |``

        Shipped ranking in matching.py: ``_COMPLETENESS = {"minimal": 1,
        "standard": 2, "rich": 3}``; below-band, unknown/absent (rank 0) all
        fail closed."""
        standard = self_lead_payload()
        standard["lead_quality"]["data_completeness"] = "standard"
        self.assertTrue(
            match_offer(self_offer(min_data_completeness="minimal"), standard).matched
        )
        self.assertTrue(
            match_offer(self_offer(min_data_completeness="standard"), standard).matched
        )
        result = match_offer(self_offer(min_data_completeness="rich"), standard)
        self.assertIn("data_completeness_too_low", result.reasons)

        rich = self_lead_payload()
        self.assertTrue(
            match_offer(self_offer(min_data_completeness="rich"), rich).matched
        )

        missing = self_lead_payload()
        del missing["lead_quality"]["data_completeness"]
        self.assertIn(
            "data_completeness_too_low",
            match_offer(self_offer(min_data_completeness="minimal"), missing).reasons,
        )

    def test_excluded_claim_language_is_fail_closed_evidence(self) -> None:
        """§8: ``| excluded_claim_language | array<string> | Landing page /
        ad copy language the buyer prohibits ... Publisher declares in
        `extensions` if their copy contains these. |``

        Shipped as presence-only fail-closed matching (matching.py comment:
        "Claim-language evidence is intentionally fail-closed. A deployment
        may provide a namespaced evidence block without putting copy/PII in a
        ping."): a lead without the evidence block cannot match; a lead that
        declares the block matches (the block is where the publisher
        declaration lives)."""
        payload = self_lead_payload()
        del payload["extensions"]
        result = match_offer(
            self_offer(excluded_claim_language=["guaranteed savings"]), payload
        )
        self.assertIn("claim_language_evidence_missing", result.reasons)

        declared = match_offer(
            self_offer(excluded_claim_language=["guaranteed savings"]),
            self_lead_payload(),
        )
        self.assertTrue(declared.matched, declared.reasons)

    def test_require_consent_evidence(self) -> None:
        """§8: ``| require_consent_evidence | boolean | If true, buyer
        requires `consent_evidence[]` to be non-empty. |``"""
        payload = self_lead_payload()
        payload["compliance"]["consent_evidence"] = []
        result = match_offer(self_offer(require_consent_evidence=True), payload)
        self.assertIn("consent_evidence_missing", result.reasons)

        accepted = match_offer(
            self_offer(require_consent_evidence=True), self_lead_payload()
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_reject_litigator_and_blacklist_flagged(self) -> None:
        """§8: ``| reject_litigator_flagged | boolean | If true, buyer will
        not accept leads with `litigator_status != clean`. |`` and
        ``| reject_blacklist_flagged | boolean | If true, buyer will not
        accept leads with `blacklist_status != clean`. |``

        Shipped source of the status: the §3 ``compliance.scrubs[]`` entry of
        the matching type (see CMP-06 tests for the tri-state semantics)."""
        litigator = self_lead_payload()
        litigator["compliance"]["scrubs"] = [{"type": "litigator", "result": "flagged"}]
        result = match_offer(self_offer(reject_litigator_flagged=True), litigator)
        self.assertIn("litigator_not_clean", result.reasons)

        blacklist = self_lead_payload()
        blacklist["compliance"]["scrubs"] = [{"type": "blacklist", "result": "blocked"}]
        result = match_offer(self_offer(reject_blacklist_flagged=True), blacklist)
        self.assertIn("blacklist_not_clean", result.reasons)

        accepted = match_offer(
            self_offer(reject_litigator_flagged=True, reject_blacklist_flagged=True),
            self_lead_payload(),
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_allowed_publisher_ids(self) -> None:
        """§8: ``| allowed_publisher_ids | array<string> | Optional publisher
        sender-ID allowlist. |``"""
        result = match_offer(
            self_offer(allowed_publisher_ids=["pub_a"]),
            self_lead_payload(),
            sender_id="pub_b",
        )
        self.assertIn("publisher_not_allowed", result.reasons)

        accepted = match_offer(
            self_offer(allowed_publisher_ids=["pub_a"]),
            self_lead_payload(),
            sender_id="pub_a",
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_allowed_brand_ids(self) -> None:
        """§8: ``| allowed_brand_ids | array<string> | Optional
        `provenance.brand_id` allowlist. |``"""
        wrong_brand = self_lead_payload()
        wrong_brand["provenance"]["brand_id"] = "brand_z"
        result = match_offer(self_offer(allowed_brand_ids=["brand_x"]), wrong_brand)
        self.assertIn("brand_not_allowed", result.reasons)

        missing_brand = self_lead_payload()
        del missing_brand["provenance"]["brand_id"]
        result = match_offer(self_offer(allowed_brand_ids=["brand_x"]), missing_brand)
        self.assertIn("brand_not_allowed", result.reasons)

        accepted = match_offer(
            self_offer(allowed_brand_ids=["brand_x"]), self_lead_payload()
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_attribute_in_allowlist(self) -> None:
        """§8: ``| attribute_equals / attribute_in | object | Safe
        declarative vertical criteria; values are data, never executable
        expressions. |`` — attribute_in is the unasserted half."""
        result = match_offer(
            self_offer(attribute_in={"loan_type": ["first_time_buyer"]}),
            self_lead_payload(),
        )
        self.assertIn("attribute_loan_type_not_allowed", result.reasons)

        accepted = match_offer(
            self_offer(attribute_in={"loan_type": ["refinance", "purchase"]}),
            self_lead_payload(),
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_core_predicates_vertical_country_region_postal_channel_active(
        self,
    ) -> None:
        """The non-battery core branches of match_offer/offer_is_available,
        asserted for completeness of the matcher's reason surface:
        vertical_mismatch, country_not_supported, state_region_not_supported,
        postal_code_not_supported, channel_not_supported, offer_inactive,
        consent_expired."""
        self.assertIn(
            "vertical_mismatch",
            match_offer(self_offer(vertical="solar"), self_lead_payload()).reasons,
        )
        self.assertIn(
            "country_not_supported",
            match_offer(self_offer(countries=["NZ"]), self_lead_payload()).reasons,
        )
        self.assertIn(
            "state_region_not_supported",
            match_offer(self_offer(state_regions=["VIC"]), self_lead_payload()).reasons,
        )
        self.assertIn(
            "postal_code_not_supported",
            match_offer(self_offer(postal_codes=["3000"]), self_lead_payload()).reasons,
        )
        self.assertIn(
            "channel_not_supported",
            match_offer(self_offer(channels=["call"]), self_lead_payload()).reasons,
        )
        self.assertIn(
            "offer_inactive",
            match_offer(self_offer(active=False), self_lead_payload()).reasons,
        )

        expiring = self_lead_payload()
        expiring["compliance"]["consent_expires_at"] = "2020-01-01T00:00:00Z"
        self.assertIn("consent_expired", match_offer(self_offer(), expiring).reasons)

    def test_consent_purpose_requirements(self) -> None:
        """Namespaced requirement ``lcp.platform.required_consent_purposes``
        plus ``contact_purpose`` auto-inclusion, and the invalid-policy
        fail-closed branch."""
        missing_purpose = self_lead_payload()
        missing_purpose["compliance"]["consent_purposes"] = ["calls"]
        result = match_offer(
            self_offer(
                extensions={"lcp.platform.required_consent_purposes": ["sms", "email"]}
            ),
            missing_purpose,
        )
        self.assertIn("consent_purpose_missing:email,sms", result.reasons)

        contact_only = match_offer(
            self_offer(extensions={"lcp.platform.contact_purpose": "email"}),
            missing_purpose,
        )
        self.assertIn("consent_purpose_missing:email", contact_only.reasons)

        invalid_policy = match_offer(
            self_offer(extensions={"lcp.platform.required_consent_purposes": "sms"}),
            self_lead_payload(),
        )
        self.assertIn("consent_purpose_policy_invalid", invalid_policy.reasons)

        accepted = match_offer(
            self_offer(
                extensions={"lcp.platform.required_consent_purposes": ["calls"]}
            ),
            self_lead_payload(),
        )
        self.assertTrue(accepted.matched, accepted.reasons)

    def test_extension_operators_exists_prefix_and_invalid_block(self) -> None:
        """offer_extensions branches beyond the asserted in/equals/between
        profile trio: ``exists``, ``prefix``, the invalid-block fail-closed
        reason, and the service-area country/state reasons."""
        requirements = {
            "lcp.platform.requirements": {
                "profile_id": "p1",
                "version": "v1",
                "predicates": [
                    {
                        "path": "provenance.brand_id",
                        "operator": "exists",
                        "value": True,
                    },
                    {
                        "path": "attributes.loan_type",
                        "operator": "prefix",
                        "value": "refi",
                    },
                ],
            }
        }
        self.assertTrue(
            match_offer(
                self_offer(extensions=requirements), self_lead_payload()
            ).matched
        )

        absent_target = self_lead_payload()
        del absent_target["provenance"]["brand_id"]
        result = match_offer(self_offer(extensions=requirements), absent_target)
        self.assertIn("requirements_predicate_mismatch", result.reasons)

        not_prefix = self_lead_payload()
        not_prefix["attributes"]["loan_type"] = "equity"
        result = match_offer(self_offer(extensions=requirements), not_prefix)
        self.assertIn("requirements_predicate_mismatch", result.reasons)

        result = match_offer(
            self_offer(extensions="not-a-mapping"), self_lead_payload()
        )
        self.assertIn("offer_extensions_invalid", result.reasons)

        result = match_offer(
            self_offer(
                extensions={
                    "lcp.platform.service_area": {
                        "profile_id": "s1",
                        "version": "v1",
                        "countries": ["NZ"],
                    }
                }
            ),
            self_lead_payload(),
        )
        self.assertIn("service_area_country_not_supported", result.reasons)

        result = match_offer(
            self_offer(
                extensions={
                    "lcp.platform.service_area": {
                        "profile_id": "s1",
                        "version": "v1",
                        "state_regions": ["VIC"],
                    }
                }
            ),
            self_lead_payload(),
        )
        self.assertIn("service_area_state_not_supported", result.reasons)

    def test_day_of_week_and_overnight_delivery_windows(self) -> None:
        """Window-style requirements beyond the asserted vertical/channel/
        hours filters: day mismatch and the overnight (start > end) branch of
        ``_in_window``."""
        monday_10am = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)  # Monday
        result = match_offer(
            self_offer(
                delivery_windows=[
                    {
                        "timezone": "UTC",
                        "available_from": "09:00",
                        "available_to": "17:00",
                        "days": ["tue"],
                    }
                ]
            ),
            self_lead_payload(),
            now=monday_10am,
        )
        self.assertIn("outside_delivery_window", result.reasons)

        overnight = {
            "timezone": "UTC",
            "available_from": "22:00",
            "available_to": "02:00",
            "days": ["mon", "tue"],
        }
        late_monday = datetime(2026, 8, 17, 23, 30, tzinfo=timezone.utc)
        early_tuesday = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc)
        outside_night = datetime(2026, 8, 18, 3, 0, tzinfo=timezone.utc)
        self.assertTrue(
            match_offer(
                self_offer(delivery_windows=[overnight]),
                self_lead_payload(),
                now=late_monday,
            ).matched
        )
        self.assertTrue(
            match_offer(
                self_offer(delivery_windows=[overnight]),
                self_lead_payload(),
                now=early_tuesday,
            ).matched
        )
        self.assertIn(
            "outside_delivery_window",
            match_offer(
                self_offer(delivery_windows=[overnight]),
                self_lead_payload(),
                now=outside_night,
            ).reasons,
        )

    def test_offer_schema_and_storage_accept_full_battery(self) -> None:
        """schema half of the row: schemas/offer.json (strict,
        ``additionalProperties: false``) declares every §8 battery field, and
        upsert_offer stores the restriction set verbatim.

        §8, verbatim::

            Offers (`GET /v1/lcp/offers`) may declare restrictions — lead
            characteristics the buyer will not accept. The standardized
            discovery shape is defined in [schemas/offer.json](schemas/offer.json).
        """
        battery = {
            "excluded_source_types": ["scraped"],
            "excluded_acquisition_methods": ["cold_outbound"],
            "reject_incentivized": True,
            "excluded_incentive_types": ["free_consultation"],
            "require_verified_phone": True,
            "require_verified_email": True,
            "max_spam_risk_score": 40,
            "excluded_claim_language": ["guaranteed savings"],
            "require_consent_evidence": True,
            "reject_dnc_flagged": True,
            "reject_litigator_flagged": True,
            "reject_blacklist_flagged": True,
            "min_data_completeness": "standard",
            "allowed_publisher_ids": ["pub_a"],
            "allowed_brand_ids": ["brand_x"],
            "attribute_equals": {"loan_type": "refinance"},
            "attribute_in": {"employment_status": ["employed"]},
        }
        offer = self_offer(offer_id="battery-full", **battery)
        self.platform.upsert_offer(offer)
        stored = self.platform.store.get_offer("battery-full")
        self.assertIsNotNone(stored)
        for field, value in battery.items():
            self.assertEqual(stored[field], value, field)


if __name__ == "__main__":
    unittest.main()

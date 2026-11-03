"""Adversarial end-to-end checks of the money-path invariants (ARB plan §3.5).

Each test drives the reference platform through its real HTTP service surface
with HMAC authentication enforced (``require_auth=True``) and asserts that an
attack-shaped input is refused *before* any lead, payable, or delivery side
effect happens. Deterministic and network-free: every webhook target is the
non-routable loopback port ``http://127.0.0.1:1/`` (connection refused
immediately, exercising the genuine failure path of the delivery worker).

Scenario -> SPEC anchor:
  replay defense ......... SPEC §5 Transport ("receivers MUST reject requests
                           outside the acceptable timestamp window before
                           processing PII"), §4 idempotency, LCP-012/LCP-005
  over-award ............. SPEC §3 exclusivity ("exclusive" = single buyer),
                           §4 bid/post winner selection
  tampered signature ..... SPEC §5 (canonical signing input), LCP-012
  unknown message type ... SPEC §5 taxonomy, §6 closed allowlist on
                           message.type (LCP-006), error shape
  dead-letter ............ delivery retry/DLQ machinery (worker contract)
  PII-in-ping boundary ... SPEC §4 ping allowlist + ping_safe tagging, LCP-008
  slot accounting ........ LEP-0003 §5 enforcement set (T2/T3/T4): one
                           write-time award decision, DB-enforced slot
                           uniqueness, distinct-buyer budget, leased expiry
                           sweep, award-eligible ping state
  contacted exchange ..... LEP-0003 §3/§4 (T6/T8): scoped-buyer CONTACTED
                           reporting, message.id dedupe, set-union
                           accumulation, per-pair re-keying on later posts
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import multiprocessing
import os
import sqlite3
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.messages import _envelope
from lcp_platform.router import Platform, RequestError
from lcp_platform.service import PlatformService
from lcp_platform.storage import now_iso
from lcp_platform.validation import ValidationError

ROOT = Path(__file__).resolve().parents[3]

PUBLISHER_SECRET = "publisher-secret"
BUYER_SECRETS = {"buyer_001": "buyer-secret-1", "buyer_002": "buyer-secret-2"}
REPLAY_WINDOW_SECONDS = 300  # SPEC §5 default window
# Non-routable target: the delivery worker gets a real ConnectError without
# touching any external network.
DEAD_TARGET = "http://127.0.0.1:1/buyer"


def _ts(offset_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _load_lead(
    lead_id: str,
    *,
    idempotency_key: str,
    exclusive: bool = False,
) -> dict:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        lead = json.load(handle)
    message = lead["lcp"]["message"]
    payload = lead["lcp"]["payload"]
    message["test"] = True
    message["id"] = str(uuid4())
    message["idempotency_key"] = idempotency_key
    payload["lead_id"] = lead_id
    # The checked-in example demonstrates a historical expiry; these flows
    # need a live intake (expiry is covered by the dedicated suite).
    payload.pop("expiry", None)
    if exclusive:
        payload["exclusivity"] = {"exclusivity": "exclusive"}
    return lead


def _compact(envelope: dict) -> bytes:
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _signed_headers(
    envelope: dict, secret: str, *, timestamp: str | None = None
) -> tuple[dict[str, str], bytes]:
    """Build transport headers exactly as SPEC §5 defines the canonical input."""
    body = _compact(envelope)
    message = envelope["lcp"]["message"]
    timestamp = timestamp or now_iso()
    idempotency_key = message["idempotency_key"]
    headers = {
        "Content-Type": "application/json",
        "X-LCP-Test": "true",
        "X-LCP-Sender-Id": message["sender_id"],
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Idempotency-Key": idempotency_key,
        "X-LCP-Signature": signature_for(secret, timestamp, idempotency_key, body),
    }
    return headers, body


def _offer(**overrides: object) -> dict:
    offer = {
        "offer_id": "mortgage-au",
        "buyer_id": "buyer_001",
        "active": True,
        "vertical": "mortgage",
        "countries": ["AU"],
        "floor_price_cents": 1500,
        "currency": "AUD",
        "require_consent_evidence": True,
        "webhook_url": DEAD_TARGET,
        "ping_timeout_seconds": 300,
    }
    offer.update(overrides)
    return offer


class AdversarialE2ETests(unittest.TestCase):
    def _platform(self, **config_overrides: object) -> Platform:
        config = PlatformConfig(
            database_path=Path(":memory:"),
            schema_root=ROOT / "schemas",
            platform_id="platform_001",
            require_auth=True,
            replay_window_seconds=REPLAY_WINDOW_SECONDS,
            allow_insecure_webhooks=True,
            test_mode=True,
        )
        if config_overrides:
            config = replace(config, **config_overrides)
        platform = Platform(config)
        platform.upsert_credential("pub_123", hmac_secret=PUBLISHER_SECRET)
        for buyer_id, secret in BUYER_SECRETS.items():
            platform.upsert_credential(buyer_id, hmac_secret=secret)
        self.addCleanup(platform.close)
        return platform

    # ─── Scenario 1: replay defense ─────────────────────────────────────────

    def test_replayed_signed_requests_window_and_idempotency_enforced(self) -> None:
        platform = self._platform()
        platform.upsert_offer(_offer())
        service = PlatformService(platform)

        lead = _load_lead("adv-replay-001", idempotency_key="adv-replay-key-a")
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        status, _, ack = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        deliveries = platform.store.deliveries_for_lead("adv-replay-001")
        self.assertEqual([d["kind"] for d in deliveries], ["ping"])

        # (1a) Byte-identical replay inside the window WITH the same
        # idempotency key: must dedupe, never deliver twice.
        status, _, replay = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(replay["lcp"]["payload"]["status"], "DUPLICATE")
        self.assertEqual(len(platform.store.deliveries_for_lead("adv-replay-001")), 1)
        self.assertEqual(len(platform.store.list_pings("adv-replay-001")), 1)

        # (1b) Same lead, valid signature, DIFFERENT idempotency key inside
        # the window: the idempotency layer cannot merge it with 1a, but the
        # lead primary key must still refuse a second intake (defense in
        # depth against key-rotation style replays of the same payload).
        resubmit = _load_lead("adv-replay-001", idempotency_key="adv-replay-key-b")
        headers_b, body_b = _signed_headers(resubmit, PUBLISHER_SECRET)
        status, _, dup = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers_b, body=body_b
        )
        self.assertEqual(status, 200)
        self.assertEqual(dup["lcp"]["payload"]["status"], "DUPLICATE")
        self.assertEqual(len(platform.store.deliveries_for_lead("adv-replay-001")), 1)

        # (1c) A genuinely new lead with a fresh key must be treated as new
        # (idempotency scoping is not over-deduplicating across leads).
        fresh = _load_lead("adv-replay-002", idempotency_key="adv-replay-key-c")
        headers_c, body_c = _signed_headers(fresh, PUBLISHER_SECRET)
        status, _, new_ack = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers_c, body=body_c
        )
        self.assertEqual(status, 200)
        self.assertEqual(new_ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertIsNotNone(platform.store.get_lead("adv-replay-002"))
        self.assertEqual(len(platform.store.deliveries_for_lead("adv-replay-002")), 1)

        # (1d) Outside the window: SPEC §5 requires rejection *before*
        # processing PII — with a properly signed (non-tampered) request so
        # this isolates the replay-window check, not the signature check.
        stale = _load_lead("adv-replay-003", idempotency_key="adv-replay-key-d")
        stale_timestamp = _ts(-(REPLAY_WINDOW_SECONDS + 60))
        headers_d, body_d = _signed_headers(
            stale, PUBLISHER_SECRET, timestamp=stale_timestamp
        )
        status, _, refused = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers_d, body=body_d
        )
        self.assertEqual(status, 401)
        error = refused["errors"][0]
        self.assertEqual(error["code"], "LCP-012")
        self.assertIn("replay window", error["message"].lower())
        self.assertIn("request_id", refused)
        self.assertIsNone(platform.store.get_lead("adv-replay-003"))
        self.assertEqual(platform.store.deliveries_for_lead("adv-replay-003"), [])

    # ─── Scenario 2: over-award of an exclusive lead ────────────────────────

    def test_exclusive_lead_overaward_refused_single_post_only(self) -> None:
        platform = self._platform()
        # ping_timeout_seconds=1 keeps auction resolution deterministic at
        # second granularity (the pattern the existing flow test uses) with
        # no network: both webhooks are the refused loopback port.
        platform.upsert_offer(
            _offer(
                offer_id="adv-auction-a", buyer_id="buyer_001", ping_timeout_seconds=1
            )
        )
        platform.upsert_offer(
            _offer(
                offer_id="adv-auction-b", buyer_id="buyer_002", ping_timeout_seconds=1
            )
        )
        service = PlatformService(platform)

        lead = _load_lead(
            "adv-exclusive-001", idempotency_key="adv-exclusive-key", exclusive=True
        )
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        status, _, ack = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        pings = platform.store.list_pings("adv-exclusive-001")
        self.assertEqual(
            len(pings), 2, "an exclusive lead may be pinged to both buyers"
        )
        ping_by_buyer = {ping["buyer_id"]: ping for ping in pings}

        # Both buyers bid accept; the second (higher) bid is the only legal win.
        bids = []
        for buyer_id, price in (
            ("buyer_001", 2200),
            ("buyer_002", 2400),
        ):
            ping = ping_by_buyer[buyer_id]
            bid = _envelope(
                "bid",
                buyer_id,
                "platform_001",
                {
                    "ping_id": ping["ping_id"],
                    "decision": "accept",
                    "bid_price_cents": price,
                    "currency": "AUD",
                    "estimated_contact_seconds": 30,
                },
                correlation_id=ping["message_id"],
                test=True,
            )
            headers, body = _signed_headers(bid, BUYER_SECRETS[buyer_id])
            bids.append(platform.submit_bid(bid, headers=headers, raw_body=body))
        self.assertEqual(
            [b["lcp"]["payload"]["status"] for b in bids], ["RECEIVED", "RECEIVED"]
        )

        time.sleep(1.1)  # past the second-granularity ping expiry boundary
        platform.process_once()

        posts = [
            d
            for d in platform.store.deliveries_for_lead("adv-exclusive-001")
            if d["kind"] == "post"
        ]
        self.assertEqual(len(posts), 1, "the second winning post must be refused")
        self.assertEqual(posts[0]["buyer_id"], "buyer_002")
        self.assertEqual(
            platform.store.get_lead("adv-exclusive-001")["status"], "POSTED"
        )
        payables = platform.store.payable_for_lead("adv-exclusive-001")
        self.assertEqual(len(payables), 1, "over-award would double the payable ledger")
        self.assertEqual(payables[0]["buyer_id"], "buyer_002")
        self.assertEqual(platform.store.count_offer_deliveries("adv-auction-a"), 0)
        ping_statuses = {
            ping["ping_id"]: ping["status"]
            for ping in platform.store.list_pings("adv-exclusive-001")
        }
        self.assertEqual(sorted(ping_statuses.values()), ["EXPIRED", "WON"])

        # The losing buyer cannot monetize: an ACCEPTED lifecycle event from a
        # buyer with no delivered post must be refused (LCP-002). The probe
        # credential carries bid/event scopes (NOT platform:admin, which a
        # wildcard grant would imply) so the refusal comes from the per-lead
        # delivery authorization, not the coarse scope check.
        platform.upsert_credential(
            "buyer_001",
            hmac_secret=BUYER_SECRETS["buyer_001"],
            scopes=["bid:submit", "event:submit"],
        )
        event = _envelope(
            "event",
            "buyer_001",
            "platform_001",
            {
                "lead_id": "adv-exclusive-001",
                "event": "ACCEPTED",
                "timestamp": now_iso(),
            },
            correlation_id=posts[0]["message_id"],
            test=True,
        )
        headers, body = _signed_headers(event, BUYER_SECRETS["buyer_001"])
        with self.assertRaises(RequestError) as context:
            platform.submit_event(event, headers=headers, raw_body=body)
        self.assertEqual(context.exception.code, "LCP-002")
        self.assertEqual(len(platform.store.payable_for_lead("adv-exclusive-001")), 1)

        # Adversarial re-route: invoking the auction resolver again for the
        # same lead must not mint a second post (per-offer delivery guard).
        platform._route_lead("adv-exclusive-001")
        platform.process_once()
        posts = [
            d
            for d in platform.store.deliveries_for_lead("adv-exclusive-001")
            if d["kind"] == "post"
        ]
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(platform.store.payable_for_lead("adv-exclusive-001")), 1)

    # ─── Scenario 3: tampered signature ─────────────────────────────────────

    def test_tampered_signature_and_body_rejected_no_side_effects(self) -> None:
        platform = self._platform()
        platform.upsert_offer(_offer())
        service = PlatformService(platform)

        lead = _load_lead("adv-tamper-001", idempotency_key="adv-tamper-key-a")
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        tampered_body = body.replace(b"+61412345678", b"+61412345679")
        self.assertNotEqual(tampered_body, body)
        status, _, refused = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=tampered_body
        )
        self.assertEqual(status, 401)
        self.assertEqual(refused["errors"][0]["code"], "LCP-012")
        self.assertEqual(refused["errors"][0]["message"], "Invalid HMAC signature")

        # A flipped hex digit in the signature is refused the same way.
        lead_b = _load_lead("adv-tamper-002", idempotency_key="adv-tamper-key-b")
        headers_b, body_b = _signed_headers(lead_b, PUBLISHER_SECRET)
        signature = headers_b["X-LCP-Signature"]
        flipped = signature[:-1] + ("0" if signature[-1] != "0" else "1")
        headers_b["X-LCP-Signature"] = flipped
        status, _, refused_b = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers_b, body=body_b
        )
        self.assertEqual(status, 401)
        self.assertEqual(refused_b["errors"][0]["code"], "LCP-012")

        # Nothing was persisted: refusal happened before any intake side effect.
        self.assertIsNone(platform.store.get_lead("adv-tamper-001"))
        self.assertIsNone(platform.store.get_lead("adv-tamper-002"))
        self.assertEqual(platform.store.deliveries_for_lead("adv-tamper-001"), [])

    # ─── Scenario 4: unknown message type → structured error shape ─────────

    def test_unknown_message_type_returns_structured_error(self) -> None:
        platform = self._platform()
        platform.upsert_offer(_offer())
        service = PlatformService(platform)

        # (4a) Type outside the closed enum. SPEC §6/§7: message.type is a
        # closed allowlist and an unknown type triggers LCP-006
        # UNKNOWN_MESSAGE_TYPE (issue #131): the platform's structural gate
        # re-codes that one violation ahead of generic schema validation;
        # every other validation failure keeps LCP-100.
        lead = _load_lead("adv-unknown-001", idempotency_key="adv-unknown-key-a")
        lead["lcp"]["message"]["type"] = "profit_share"
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        status, _, refused = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 400)
        self.assertIsInstance(refused["errors"], list)
        self.assertEqual(len(refused["errors"]), 1)
        error = refused["errors"][0]
        self.assertEqual(set(error) >= {"code", "message"}, True)
        self.assertEqual(error["code"], "LCP-006")
        self.assertTrue(error["details"], "structured error must explain the failure")
        self.assertTrue(
            any("not one of" in str(detail) for detail in error["details"]),
            "details must name the closed message.type enum violation",
        )
        self.assertIn("request_id", refused)
        self.assertIsNone(platform.store.get_lead("adv-unknown-001"))

        # (4b) A protocol-known type that cannot enter intake (valid event
        # schema posted to /leads) gets the SPEC-listed LCP-006.
        event = _envelope(
            "event",
            "pub_123",
            "platform_001",
            {"lead_id": "adv-unknown-002", "event": "REJECTED", "timestamp": now_iso()},
            test=True,
        )
        headers, body = _signed_headers(event, PUBLISHER_SECRET)
        status, _, refused_b = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 400)
        self.assertEqual(refused_b["errors"][0]["code"], "LCP-006")
        self.assertIn("request_id", refused_b)

    # ─── Scenario 5: dead-letter path ───────────────────────────────────────

    def test_dead_letter_durable_when_webhook_unreachable(self) -> None:
        # max_delivery_attempts=1 keeps the test fast while exercising the
        # real retry accounting (attempts increment across replays).
        platform = self._platform(max_delivery_attempts=1)
        platform.upsert_offer(_offer(offer_id="adv-direct-dead", routing_mode="direct"))
        service = PlatformService(platform)

        lead = _load_lead("adv-deadletter-001", idempotency_key="adv-deadletter-key")
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        status, _, ack = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        posts = [
            d
            for d in platform.store.deliveries_for_lead("adv-deadletter-001")
            if d["kind"] == "post"
        ]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["status"], "PENDING")

        platform.process_once()

        row = platform.store.deliveries_for_lead("adv-deadletter-001")[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("ConnectError", row["last_error"])
        dead_letters = platform.store.list_dead_letters()
        self.assertEqual(len(dead_letters), 1)
        job = dead_letters[0]
        self.assertEqual(job["queue_type"], "delivery")
        self.assertEqual(job["resource_id"], row["delivery_id"])
        self.assertEqual(job["lead_id"], "adv-deadletter-001")
        self.assertEqual(job["status"], "OPEN")
        self.assertEqual(job["attempts"], 1)
        metrics = platform.store.metrics()
        self.assertEqual(metrics["dead_letters"]["open"], 1)

        # The retry helper re-arms the failed delivery through the existing
        # machinery, and a repeated failure re-opens the dead-letter row with
        # cumulative attempt accounting.
        self.assertTrue(platform.store.replay_dead_letter(job["job_id"]))
        self.assertEqual(
            platform.store.deliveries_for_lead("adv-deadletter-001")[0]["status"],
            "RETRY",
        )
        self.assertEqual(platform.store.list_dead_letters()[0]["status"], "REPLAYED")

        platform.process_once()
        row = platform.store.deliveries_for_lead("adv-deadletter-001")[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["attempts"], 2)
        job = platform.store.list_dead_letters()[0]
        self.assertEqual(job["status"], "OPEN")
        self.assertEqual(job["attempts"], 2)

        # Quarantine parks it out of the open backlog; quarantined jobs remain
        # operator-replayable, and a consumed (REPLAYED) job refuses replay.
        self.assertTrue(platform.store.quarantine_dead_letter(job["job_id"]))
        metrics = platform.store.metrics()
        self.assertEqual(metrics["dead_letters"]["open"], 0)
        self.assertEqual(metrics["dead_letters"]["quarantined"], 1)
        self.assertTrue(platform.store.replay_dead_letter(job["job_id"]))
        self.assertEqual(platform.store.list_dead_letters()[0]["status"], "REPLAYED")
        self.assertFalse(platform.store.replay_dead_letter(job["job_id"]))

    # ─── Scenario 6: PII boundary at the ping ───────────────────────────────

    def test_pii_in_ping_rejected_by_platform_validation(self) -> None:
        platform = self._platform()
        platform.upsert_offer(_offer(offer_id="adv-ping-pii"))
        service = PlatformService(platform)

        lead = _load_lead("adv-ping-pii-001", idempotency_key="adv-ping-pii-key")
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        status, _, ack = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        ping_rows = platform.store.list_pings("adv-ping-pii-001")
        self.assertEqual(len(ping_rows), 1)
        ping = platform.store.decode_envelope(ping_rows[0]["envelope_json"])
        serialized = json.dumps(ping)
        for secret_value in (
            "+61412345678",
            "jane.smith@example.com",
            "loan_amount_cents",
            "65000000",
        ):
            self.assertNotIn(secret_value, serialized, "ping leaked source PII")
        self.assertIn("loan_amount_band", ping["lcp"]["payload"]["attributes"])

        # The platform's own ingest gate (_validate_envelope, the same call
        # every inbound message and every platform-built ping passes through)
        # must reject a ping carrying a raw phone number.
        control = copy.deepcopy(ping)
        platform.validator.require_valid_envelope(
            control
        )  # unmodified ping stays valid

        with_phone = copy.deepcopy(ping)
        with_phone["lcp"]["payload"]["phone"] = "+61412345678"
        with self.assertRaises(RequestError) as context:
            platform._validate_envelope(with_phone)
        self.assertEqual(context.exception.code, "LCP-100")
        self.assertTrue(
            any("phone" in str(detail) for detail in context.exception.details),
            "structured rejection must identify the offending field",
        )

        with_consumer = copy.deepcopy(ping)
        with_consumer["lcp"]["payload"]["consumer"] = lead["lcp"]["payload"]["consumer"]
        with self.assertRaises(RequestError):
            platform._validate_envelope(with_consumer)

        # Non-ping_safe vertical attributes are rejected even though the ping
        # schema itself allows them (the §12 ping_safe tagging rule).
        with_unsafe_attr = copy.deepcopy(ping)
        with_unsafe_attr["lcp"]["payload"]["attributes"]["loan_amount_cents"] = 65000000
        with self.assertRaises(ValidationError) as unsafe:
            platform.validator.require_valid_envelope(with_unsafe_attr)
        self.assertIn("not tagged ping_safe", str(unsafe.exception))


POSTGRES_URL = os.environ.get("LCP_TEST_POSTGRES_URL")
PG_RACE_TEST_KEY = "bGNwLXNsb3RzLXJhY2UtaW50ZWdyYXRpb24ta2V5MzI="


def _make_platform(
    database_url: str | None = None, **config_overrides: object
) -> Platform:
    config = PlatformConfig(
        database_path=Path(":memory:"),
        database_url=database_url,
        schema_root=ROOT / "schemas",
        platform_id="platform_001",
        require_auth=True,
        replay_window_seconds=REPLAY_WINDOW_SECONDS,
        allow_insecure_webhooks=True,
        test_mode=True,
    )
    if config_overrides:
        config = replace(config, **config_overrides)
    return Platform(config)


def _slot_offer(offer_id: str, buyer_id: str, **overrides: object) -> dict:
    offer = _offer(
        offer_id=offer_id,
        buyer_id=buyer_id,
        webhook_url=DEAD_TARGET,
        ping_timeout_seconds=300,
    )
    offer.update(overrides)
    return offer


def _place_bids(platform: Platform, lead_id: str, bids: list[tuple[str, int]]) -> None:
    ping_by_buyer = {
        ping["buyer_id"]: ping for ping in platform.store.list_pings(lead_id)
    }
    for buyer_id, price in bids:
        ping = ping_by_buyer[buyer_id]
        bid = _envelope(
            "bid",
            buyer_id,
            "platform_001",
            {
                "ping_id": ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": price,
                "currency": "AUD",
                "estimated_contact_seconds": 30,
            },
            correlation_id=ping["message_id"],
            test=True,
        )
        headers, body = _signed_headers(bid, BUYER_SECRETS[buyer_id])
        platform.submit_bid(bid, headers=headers, raw_body=body)


def _posts_for(platform: Platform, lead_id: str) -> list:
    return [
        delivery
        for delivery in platform.store.deliveries_for_lead(lead_id)
        if delivery["kind"] == "post"
    ]


class _AuctionFixtureMixin:
    """Shared fixture plumbing for the LEP-0003 slot/contacted suites."""

    def _platform(self) -> Platform:
        platform = _make_platform()
        platform.upsert_credential("pub_123", hmac_secret=PUBLISHER_SECRET)
        for buyer_id, secret in BUYER_SECRETS.items():
            platform.upsert_credential(buyer_id, hmac_secret=secret)
        self.addCleanup(platform.close)  # type: ignore[attr-defined]
        return platform

    def _ingest_shared(
        self, platform: Platform, lead_id: str, *, max_buyers: int
    ) -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        message = lead["lcp"]["message"]
        payload = lead["lcp"]["payload"]
        message["test"] = True
        message["id"] = str(uuid4())
        message["idempotency_key"] = f"slot-{lead_id}"
        payload["lead_id"] = lead_id
        payload.pop("expiry", None)
        payload["exclusivity"] = {"exclusivity": "shared", "max_buyers": max_buyers}
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        service = PlatformService(platform)
        status, _, ack = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)  # type: ignore[attr-defined]
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")  # type: ignore[attr-defined]
        return lead

    def _expire_pings(
        self, platform: Platform, lead_id: str, buyer_ids: set[str] | None = None
    ) -> None:
        for ping in platform.store.list_pings(lead_id):
            if buyer_ids is None or ping["buyer_id"] in buyer_ids:
                platform.store.expire_ping(ping["ping_id"])


class SlotBudgetTests(_AuctionFixtureMixin, unittest.TestCase):
    """LEP-0003 §5 slot-accounting enforcement gates (named tests T2/T3/T4).

    The dossier evidence and reproduction harnesses are recorded on
    https://github.com/SpearSystems/LCP/issues/124 (comment 6579188344 rows
    11-17; re-verified byte-exactly by the ARB re-dossier, comment
    6591860350 rows 5-6) with the published race harness at
    https://github.com/SpearSystems/LCP/issues/153#issuecomment-6579178681
    (``arb-lep-pg-race.py``, second fence).
    """

    # ─── T2a: in-process concurrent award (dossier rows 14/16) ─────────────

    def test_slot_budget_never_oversells_under_concurrent_award(self) -> None:
        """T2 (``plat: test_slot_budget_never_oversells_under_concurrent_award``).

        Forced interleaving reproduces the dossier's in-process oversell
        (ARB L-Platform dossier #124 comment 6579188344, row 14; the
        serialized control run row 16 "did not break", localising the defect
        to the read->write window plus per-pass budgeting):

            | 14 | Two award passes whose slot-state reads overlap deliver
            more posts than the budget (2 slots -> 4 posts) | harness D2:
            barrier on ``has_delivery_for_offer``, two threads calling the
            real ``Platform._route_lead`` | ``D2 forced-interleave two award
            passes, max_buyers=2 -> posts=4 distinct_buyers=2
            OVERSELL=True`` (rows: ``offer=d-offer-0-1 buyer=buyer_001`` x2,
            ``offer=d-offer-1-2 buyer=buyer_002`` x2, distinct
            ``message_id``s) | yes |

        The barrier only forces scheduling of the real slot-state read; it
        patches no platform logic. After the §5 fix the award decision is a
        single write-time transaction, so overlap is harmless (or the
        advisory read is gone entirely and the barrier never fires) - the
        invariant asserted here must hold under any interleaving.
        """
        platform = self._platform()
        platform.upsert_offer(_slot_offer("slot-race-a", "buyer_001"))
        platform.upsert_offer(_slot_offer("slot-race-b", "buyer_002"))
        self._ingest_shared(platform, "adv-slotrace-001", max_buyers=2)
        _place_bids(
            platform, "adv-slotrace-001", [("buyer_001", 1600), ("buyer_002", 1700)]
        )
        for ping in platform.store.list_pings("adv-slotrace-001"):
            platform.store.expire_ping(ping["ping_id"])

        gate = threading.Barrier(2, timeout=20)
        store = platform.store

        # After the §5 fix the advisory read is gone (the award decision is
        # the single write-time transaction), so the forced-interleave hook
        # degrades to "both passes simply run concurrently" - the invariant
        # asserted below must hold under any interleaving either way.
        real_check = getattr(store, "has_delivery_for_offer", None)
        if real_check is not None:

            def synchronised(*args: object, **kwargs: object) -> bool:
                # Both award passes complete the slot-state read before
                # either writes - exactly the interleaving the unguarded
                # window produces.
                result = real_check(*args, **kwargs)
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
                return result

            store.has_delivery_for_offer = synchronised  # type: ignore[method-assign]

        errors: list[BaseException] = []

        def pass_worker() -> None:
            try:
                platform._route_lead("adv-slotrace-001")
            except BaseException as exc:  # pragma: no cover - failure surfacing
                errors.append(exc)

        threads = [threading.Thread(target=pass_worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])

        posts = _posts_for(platform, "adv-slotrace-001")
        awarded_offers = [post["offer_id"] for post in posts]
        awarded_buyers = {post["buyer_id"] for post in posts}
        self.assertLessEqual(
            len(awarded_buyers), 2, "distinct awarded buyers exceed max_buyers"
        )
        self.assertEqual(
            len(awarded_offers),
            len(set(awarded_offers)),
            "the same (lead, offer) was awarded twice",
        )
        self.assertLessEqual(
            len(posts), 2, "delivered post rows exceed the two-buyer budget"
        )
        self.assertLessEqual(
            len(platform.store.payable_for_lead("adv-slotrace-001")),
            2,
            "over-award would double the payable ledger",
        )

    # ─── T4: budget unit = distinct awarded buyers (dossier row 19) ────────

    def test_max_buyers_bounds_distinct_buyers(self) -> None:
        """T4 (``plat: test_max_buyers_bounds_distinct_buyers``).

        LEP-0003 §1 "Units (normative)": one awarded buyer consumes exactly
        one slot regardless of matching-offer count; delivery stays per
        ``(lead, offer)`` and copies are never the budget. The dossier
        measured the pre-fix conflation (#124 comment 6579188344, row 19):

            | 19 | The budget's unit contradicts SPEC §3: two slots can be
            consumed by one buyer | harness D4: ``buyer_001`` holds two
            matching offers, both bid highest | ``D4 budget unit,
            max_buyers=2 -> posts=2 distinct_buyers=1 offers_used=
            ['d-offer-0-1','d-offer-1-1'] SPEC3 'max buyers'
            satisfied=False`` | yes |
        """
        platform = self._platform()
        platform.upsert_offer(_slot_offer("slot-unit-a1", "buyer_001"))
        platform.upsert_offer(_slot_offer("slot-unit-a2", "buyer_001"))
        platform.upsert_offer(_slot_offer("slot-unit-b1", "buyer_002"))
        self._ingest_shared(platform, "adv-slotunit-001", max_buyers=2)
        pings = {
            (ping["offer_id"]): ping
            for ping in platform.store.list_pings("adv-slotunit-001")
        }
        for offer_id, buyer_id, price in (
            ("slot-unit-a1", "buyer_001", 3000),
            ("slot-unit-a2", "buyer_001", 2900),
            ("slot-unit-b1", "buyer_002", 1600),
        ):
            ping = pings[offer_id]
            bid = _envelope(
                "bid",
                buyer_id,
                "platform_001",
                {
                    "ping_id": ping["ping_id"],
                    "decision": "accept",
                    "bid_price_cents": price,
                    "currency": "AUD",
                    "estimated_contact_seconds": 30,
                },
                correlation_id=ping["message_id"],
                test=True,
            )
            headers, body = _signed_headers(bid, BUYER_SECRETS[buyer_id])
            platform.submit_bid(bid, headers=headers, raw_body=body)
        for ping in platform.store.list_pings("adv-slotunit-001"):
            platform.store.expire_ping(ping["ping_id"])

        platform._route_lead("adv-slotunit-001")

        posts = _posts_for(platform, "adv-slotunit-001")
        awarded_buyers = {post["buyer_id"] for post in posts}
        awarded_offers = [post["offer_id"] for post in posts]
        self.assertLessEqual(
            len(awarded_buyers), 2, "distinct awarded buyers exceed max_buyers"
        )
        self.assertEqual(
            len(awarded_offers),
            len(set(awarded_offers)),
            "the same (lead, offer) was awarded twice",
        )
        self.assertIn(
            "buyer_002",
            awarded_buyers,
            "a second distinct buyer inside the budget must not be starved by "
            "one buyer's extra copies (SPEC §312 counts buyers, not posts)",
        )
        self.assertEqual(
            sorted(awarded_offers),
            ["slot-unit-a1", "slot-unit-a2", "slot-unit-b1"],
            "buyer_001 keeps both copies inside its single slot; buyer_002 fills slot two",
        )

    # ─── T3: durable uniqueness of the award (dossier row 17) ──────────────

    def test_second_post_for_same_lead_offer_is_refused(self) -> None:
        """T3 (``plat: test_second_post_for_same_lead_offer_is_refused``).

        LEP-0003 §5 item 2: the deliveries table must not depend on the
        advisory read. The dossier measured (row 17): "a second
        ``insert_delivery(kind='post')`` for the same ``(lead_id,
        offer_id)`` was measured ``accepted=2 rows=2``" - the only
        constraint was on ``message_id`` (``storage.py:613-614``). The
        invariant must survive application bugs, so the award carries a
        partial unique index on ``(lead_id, offer_id) WHERE kind = 'post'``.
        """
        platform = self._platform()
        platform.upsert_offer(_slot_offer("slot-unique-a", "buyer_001"))
        self._ingest_shared(platform, "adv-slotunique-001", max_buyers=2)
        _place_bids(platform, "adv-slotunique-001", [("buyer_001", 1600)])
        for ping in platform.store.list_pings("adv-slotunique-001"):
            platform.store.expire_ping(ping["ping_id"])
        platform._route_lead("adv-slotunique-001")
        self.assertEqual(len(_posts_for(platform, "adv-slotunique-001")), 1)

        ping_row = platform.store.list_pings("adv-slotunique-001")[0]
        duplicate = _envelope(
            "post",
            "platform_001",
            "buyer_001",
            {
                "lead_id": "adv-slotunique-001",
                "delivered_at": now_iso(),
                "price_cents": 1600,
                "currency": "AUD",
                "buyer_id": "buyer_001",
                "consumer": {"full_name": "Jane Smith", "phone": "+61412345678"},
                "location": {"country_code": "AU"},
                "attributes": {"vertical": "mortgage", "schema_version": "1.0.0"},
            },
            correlation_id=ping_row["message_id"],
            test=True,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            platform.store.insert_delivery(
                lead_id="adv-slotunique-001",
                ping_id=ping_row["ping_id"],
                offer_id="slot-unique-a",
                buyer_id="buyer_001",
                kind="post",
                envelope=duplicate,
                webhook_url=DEAD_TARGET,
            )
        # The index is restricted to awarded posts: ping-kind rows for the
        # same (lead, offer) remain legal bookkeeping.
        probe = _envelope(
            "ping",
            "platform_001",
            "buyer_001",
            {
                "ping_id": f"probe_{uuid4().hex}",
                "lead_reference": "ref",
                "phone_hash": "a" * 64,
                "country_code": "AU",
                "vertical": "mortgage",
                "attributes": {},
                "floor_price_cents": 1500,
                "currency": "AUD",
                "expires_at": _ts(300),
            },
            test=True,
        )
        platform.store.insert_delivery(
            lead_id="adv-slotunique-001",
            ping_id="no-such-ping",
            offer_id="slot-unique-a",
            buyer_id="buyer_001",
            kind="ping",
            envelope=probe,
            webhook_url=DEAD_TARGET,
        )
        kinds = [
            delivery["kind"]
            for delivery in platform.store.deliveries_for_lead("adv-slotunique-001")
        ]
        self.assertEqual(kinds.count("post"), 1)
        self.assertGreaterEqual(kinds.count("ping"), 2)

        # Router-level consequence: re-entry for the awarded lead never
        # mints another post (invalid C: "re-entry for a POSTED lead changes
        # nothing").
        platform._route_lead("adv-slotunique-001")
        platform.process_once()
        self.assertEqual(len(_posts_for(platform, "adv-slotunique-001")), 1)

    # ─── §5 item 3: expiry sweep claims under a lock/lease ─────────────────

    def test_expiry_sweep_claims_each_ping_once(self) -> None:
        """LEP-0003 §5 item 3 (dossier row 13; routed observation O1).

        ``list_expired_open_pings`` + ``expire_ping`` re-route is the
        platform's only multi-worker path back into ``_route_lead`` and took
        neither a lease nor a row lock, unlike ``claim_due_deliveries`` and
        the Postgres ``FOR UPDATE SKIP LOCKED`` paths. The sweep must claim
        each expired ping exactly once per worker.
        """
        platform = self._platform()
        platform.upsert_offer(
            _slot_offer("slot-sweep-a", "buyer_001", ping_timeout_seconds=1)
        )
        self._ingest_shared(platform, "adv-slot-sweep-001", max_buyers=2)
        time.sleep(1.2)  # second-granularity expiry boundary, like scenario 2

        first = platform.store.claim_expired_open_pings("worker-a")
        second = platform.store.claim_expired_open_pings("worker-b")
        self.assertEqual([ping["lead_id"] for ping in first], ["adv-slot-sweep-001"])
        self.assertEqual(second, [])
        ping_row = platform.store.list_pings("adv-slot-sweep-001")[0]
        self.assertEqual(ping_row["status"], "EXPIRED")

    # ─── §5 item 4: award-eligible ping state ──────────────────────────────

    def test_post_expiry_bid_is_not_award_eligible(self) -> None:
        """LEP-0003 §5 item 4 (dossier cross-surface note, recorded at #153).

        "``_route_lead`` builds candidates from every accepted bid without
        checking ``ping.status``; the OPEN-ping requirement lives only in
        ``submit_bid``, so a post-expiry bid that reached the store by
        another path stays award-eligible. The decision point filters
        non-OPEN/expired pings." Here the bid is written straight to the
        store after the ping's expiry boundary - the exact bypass shape.
        """
        platform = self._platform()
        platform.upsert_offer(
            _slot_offer("slot-late-a", "buyer_001", ping_timeout_seconds=1)
        )
        self._ingest_shared(platform, "adv-slot-late-001", max_buyers=2)
        time.sleep(1.2)
        ping = platform.store.list_pings("adv-slot-late-001")[0]
        platform.store.expire_ping(ping["ping_id"])
        late_bid = _envelope(
            "bid",
            "buyer_001",
            "platform_001",
            {
                "ping_id": ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": 4000,
                "currency": "AUD",
            },
            correlation_id=ping["message_id"],
            test=True,
        )
        self.assertTrue(
            platform.store.insert_bid(late_bid, ping)
        )  # bypass of submit_bid, as the dossier describes

        platform._route_lead("adv-slot-late-001")

        posts = _posts_for(platform, "adv-slot-late-001")
        self.assertEqual(
            posts, [], "a bid that arrived after the ping expired must not be awarded"
        )

    # ─── T2b: cross-process Postgres race (dossier row 15) ─────────────────

    @unittest.skipUnless(
        POSTGRES_URL,
        "Set LCP_TEST_POSTGRES_URL to run the two-process Postgres race",
    )
    def test_slot_budget_two_process_postgres_race(self) -> None:
        """T2 (``plat: test_slot_budget_two_process_postgres_race``).

        Quoted from the published ARB harness ``arb-lep-pg-race.py``
        (#153 comment 6579178681, second fence; re-run byte-identical by the
        re-dossier #124 comment 6591860350, row 6 - "Cross-process oversell
        on the shipped Postgres backend reproduces at this SHA"):

            Topology is the one the repository documents for production: the
            Kubernetes example runs 3 API replicas and 2 worker replicas
            against one Postgres
            (implementations/reference-platform/kubernetes/deployment.yaml:6,
            :92) ... Two separate OS processes each run one award pass. A
            cross-process barrier forces both to complete the
            ``has_delivery_for_offer`` read before either writes; that is
            the interleaving the unguarded window produces, not a modified
            code path. No platform logic is patched - only the scheduling of
            the real read.

        Recorded outcome (original dossier row 15):

            result slot budget (max_buyers): 2
            result delivered post rows: 3
            result OVERSELL: True
            sqlite3.IntegrityError: duplicate key value violates unique
            constraint "payable_records_pkey" ... raised inside the losing
            replica's award loop

        CI runs this against the ``Test`` job's ``postgres:16`` service
        (``LCP_TEST_POSTGRES_URL``); locally it needs a disposable database.
        """
        suffix = uuid4().hex[:10]
        lead_id = f"adv-pgrace-{suffix}"
        buyers = [f"pgr-buyer-{i}-{suffix}" for i in (1, 2, 3)]
        offers = [f"pgr-offer-{i}-{suffix}" for i in (1, 2, 3)]
        prices = {buyers[0]: 1600, buyers[1]: 1700, buyers[2]: 1500}

        platform = _make_platform(
            database_url=POSTGRES_URL,
            require_auth=False,
            pii_encryption_key=PG_RACE_TEST_KEY,
        )
        try:
            for buyer_id in buyers:
                platform.upsert_credential(buyer_id, hmac_secret=f"{buyer_id}-secret")
            for buyer_id, offer_id in zip(buyers, offers):
                platform.upsert_offer(
                    {
                        "offer_id": offer_id,
                        "buyer_id": buyer_id,
                        "active": True,
                        "vertical": "mortgage",
                        "countries": ["AU"],
                        "floor_price_cents": 1000,
                        "currency": "AUD",
                        "require_consent_evidence": True,
                        "webhook_url": DEAD_TARGET,
                        "ping_timeout_seconds": 300,
                    }
                )
            with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
                lead = json.load(handle)
            lead["lcp"]["message"]["test"] = True
            lead["lcp"]["message"]["id"] = str(uuid4())
            lead["lcp"]["message"]["idempotency_key"] = f"pgr-{suffix}"
            lead["lcp"]["payload"]["lead_id"] = lead_id
            lead["lcp"]["payload"].pop("expiry", None)
            lead["lcp"]["payload"]["exclusivity"] = {
                "exclusivity": "shared",
                "max_buyers": 2,
            }
            platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
            pings = {
                ping["buyer_id"]: ping for ping in platform.store.list_pings(lead_id)
            }
            self.assertEqual(len(pings), 3)
            for buyer_id in buyers:
                ping = pings[buyer_id]
                ping_env = platform.store.decode_envelope(ping["envelope_json"])
                bid = _envelope(
                    "bid",
                    buyer_id,
                    "platform_001",
                    {
                        "ping_id": ping["ping_id"],
                        "decision": "accept",
                        "bid_price_cents": prices[buyer_id],
                        "currency": "AUD",
                    },
                    correlation_id=ping_env["lcp"]["message"]["id"],
                    test=True,
                )
                platform.submit_bid(
                    bid,
                    headers={"X-LCP-Test": "true"},
                    raw_body=json.dumps(bid, separators=(",", ":")).encode(),
                )
            for ping in pings.values():
                platform.store.expire_ping(ping["ping_id"])
            self.assertTrue(platform.store.all_pings_terminal(lead_id))
        finally:
            platform.close()

        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        procs = [
            ctx.Process(
                target=_pg_award_pass_child,
                args=(
                    POSTGRES_URL,
                    lead_id,
                    buyer_ids_with_offers(buyers, offers),
                    str(ROOT),
                    barrier,
                    f"replica-{index}",
                ),
                name=f"replica-{index}",
            )
            for index in range(2)
        ]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(90)

        counter = _make_platform(
            database_url=POSTGRES_URL,
            require_auth=False,
            pii_encryption_key=PG_RACE_TEST_KEY,
        )
        try:
            posts = [
                delivery
                for delivery in counter.store.deliveries_for_lead(lead_id)
                if delivery["kind"] == "post"
            ]
        finally:
            counter.close()

        awarded_offers = [post["offer_id"] for post in posts]
        awarded_buyers = {post["buyer_id"] for post in posts}
        self.assertLessEqual(
            len(awarded_buyers), 2, "distinct awarded buyers exceed the budget of 2"
        )
        self.assertEqual(
            len(awarded_offers),
            len(set(awarded_offers)),
            "the same (lead, offer) was awarded by two replicas",
        )
        self.assertLessEqual(
            len(posts),
            2,
            f"cross-process over-award: {len(posts)} delivered post rows vs budget 2 "
            "(dossier row 15 outcome)",
        )


def buyer_ids_with_offers(buyers: list[str], offers: list[str]) -> dict[str, str]:
    return dict(zip(buyers, offers))


def _pg_award_pass_child(
    dsn: str,
    lead_id: str,
    buyer_offer_map: dict[str, str],
    root: str,
    barrier: object,
    label: str,
) -> None:
    """One replica's award pass; mirrors ``arb-lep-pg-race.py`` pass_worker."""
    import sys

    root_path = Path(root)
    if str(root_path / "implementations" / "reference-platform") not in sys.path:
        sys.path.insert(0, str(root_path / "implementations" / "reference-platform"))
    from lcp_platform.config import PlatformConfig as _Config
    from lcp_platform.router import Platform as _Platform

    platform = _Platform(
        _Config(
            database_path=Path(":memory:"),
            database_url=dsn,
            schema_root=root_path / "schemas",
            platform_id="platform_001",
            require_auth=False,
            pii_encryption_key=PG_RACE_TEST_KEY,
            test_mode=True,
            allow_insecure_webhooks=True,
        )
    )
    store = platform.store
    real_check = getattr(store, "has_delivery_for_offer", None)
    if real_check is not None:

        def synchronised(*args: object, **kwargs: object) -> bool:
            result = real_check(*args, **kwargs)
            try:
                barrier.wait(timeout=30)  # type: ignore[attr-defined]
            except Exception as exc:
                print(f"race {label} barrier: {type(exc).__name__}: {exc}", flush=True)
            return result

        store.has_delivery_for_offer = synchronised  # type: ignore[method-assign]
    else:
        # Post-fix: the advisory read no longer exists; both replicas simply
        # run the real award pass concurrently. Release peers so no barrier
        # wait can strand a sibling.
        try:
            barrier.wait(timeout=5)  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        platform._route_lead(lead_id)
    except BaseException as exc:  # the dossier's loser raised inside the loop
        print(f"race {label} award pass: {type(exc).__name__}: {exc}", flush=True)
    finally:
        platform.close()


class ContactedExchangeTests(_AuctionFixtureMixin, unittest.TestCase):
    """LEP-0003 §3/§4 contacted-hash exchange gates (T6/T8 + ingest hygiene).

    Trust model, stated in the record and reproduced here: reported entries
    are authenticated-sender, per-lead attestations keyed with the
    reporting pair's secret. The platform validates **format and count
    sanity only** - "the platform **cannot verify** that a reported digest
    matches a real contact (verification would require every pair's secret
    and normalization, §3)" - and re-keys **only from the plaintext it
    delivered** ("never from ``CONTACTED`` report digests"). Replay is
    deduped by the event ``message.id`` (``lifecycle_events`` primary key);
    retried reports with fresh ids merge by digest set (at-least-once,
    §4). The reference platform hashes phone only (verbatim wire-bytes
    preimage rule, ``messages.pair_phone_hash``); the email hashing path
    ships with the dedup-hash wave (SPEC §9 pins the canonical,
    contact-hash.json publishes the pairs), so this build never emits an
    email-form digest.
    """

    LEAD_PHONE = "+61412345678"

    def _digest(self, secret: str, value: str) -> str:
        return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()

    def _bid(
        self,
        platform: Platform,
        *,
        buyer_id: str,
        secret: str,
        lead_id: str,
        price: int,
        extra: dict[str, object] | None = None,
        version: str = "1.0.0",
    ) -> dict:
        ping = next(
            ping
            for ping in platform.store.list_pings(lead_id)
            if ping["buyer_id"] == buyer_id
        )
        payload: dict[str, object] = {
            "ping_id": ping["ping_id"],
            "decision": "accept",
            "bid_price_cents": price,
            "currency": "AUD",
            "estimated_contact_seconds": 30,
        }
        if extra:
            payload.update(extra)
        bid = _envelope(
            "bid",
            buyer_id,
            "platform_001",
            payload,
            correlation_id=ping["message_id"],
            test=True,
        )
        bid["lcp"]["version"] = version
        headers, body = _signed_headers(bid, secret)
        return platform.submit_bid(bid, headers=headers, raw_body=body)

    def _mark_delivered(self, platform: Platform, lead_id: str, offer_id: str) -> None:
        for post in _posts_for(platform, lead_id):
            if post["offer_id"] == offer_id:
                platform.store.mark_delivery(
                    post["delivery_id"],
                    status="DELIVERED",
                    attempts=1,
                    next_attempt_at=now_iso(),
                )

    def _report_contacted(
        self,
        platform: Platform,
        *,
        buyer_id: str,
        secret: str,
        lead_id: str,
        hashes: object,
        message_id: str | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
        include_details: bool = True,
        resend: dict | None = None,
    ) -> tuple[dict, dict]:
        if resend is not None:
            event = resend
        else:
            details: dict[str, object] = {
                "contacted_at": now_iso(),
                "via": "phone",
            }
            if hashes is not None:
                details["contacted_hashes"] = hashes
            payload: dict[str, object] = {
                "lead_id": lead_id,
                "event": "CONTACTED",
                "timestamp": now_iso(),
            }
            if include_details:
                payload["details"] = details
            event = _envelope(
                "event",
                buyer_id,
                "platform_001",
                payload,
                correlation_id=correlation_id,
                test=True,
            )
            event["lcp"]["version"] = "1.1.0"
            if message_id:
                event["lcp"]["message"]["id"] = message_id
            if idempotency_key:
                event["lcp"]["message"]["idempotency_key"] = idempotency_key
        headers, body = _signed_headers(event, secret)
        return platform.submit_event(event, headers=headers, raw_body=body), event

    def _delivered_reporter(
        self, platform: Platform, lead_id: str, offer_id: str, buyer_id: str
    ) -> None:
        """Award ``buyer_id`` and mark its post delivered (report eligibility)."""
        self._expire_pings(platform, lead_id, {buyer_id})
        platform._route_lead(lead_id)
        self._mark_delivered(platform, lead_id, offer_id)

    # ─── T6: scoped-buyer CONTACTED acceptance + advertisement ─────────────

    def test_buyer_scoped_contacted_event_accepted_and_advertised(self) -> None:
        """T6 (``plat: test_buyer_scoped_contacted_event_accepted_and_advertised``).

        LEP-0003 §4 (F5): "measured, the proposal's own §4 example from a
        delivered buyer with ``scopes=["event:submit","bid:submit"]``
        returns ``LCP-002 Sender is not authorized for this lead event``,
        while ``DELIVERED`` on the same transport and scopes returns
        ``RECEIVED`` (dossier F5, row 20)." Acceptance today rode the
        wildcard default scope - "not a conformance-grade path" - so this
        test uses properly scoped credentials.
        """
        platform = self._platform()
        for buyer_id in ("buyer_001", "buyer_002"):
            platform.upsert_credential(
                buyer_id,
                hmac_secret=BUYER_SECRETS[buyer_id],
                scopes=["bid:submit", "event:submit"],
            )
        platform.upsert_offer(_slot_offer("slot-t6-a", "buyer_001"))
        platform.upsert_offer(_slot_offer("slot-t6-b", "buyer_002"))
        self._ingest_shared(platform, "adv-t6-001", max_buyers=1)
        self._bid(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-t6-001",
            price=2200,
        )
        self._bid(
            platform,
            buyer_id="buyer_002",
            secret=BUYER_SECRETS["buyer_002"],
            lead_id="adv-t6-001",
            price=2400,
        )
        self._expire_pings(platform, "adv-t6-001")
        platform._route_lead("adv-t6-001")
        posts = _posts_for(platform, "adv-t6-001")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["buyer_id"], "buyer_002")
        self._mark_delivered(platform, "adv-t6-001", "slot-t6-b")

        digest = self._digest(BUYER_SECRETS["buyer_002"], self.LEAD_PHONE)
        ack, _event = self._report_contacted(
            platform,
            buyer_id="buyer_002",
            secret=BUYER_SECRETS["buyer_002"],
            lead_id="adv-t6-001",
            hashes=[digest],
            correlation_id=posts[0]["message_id"],
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        advertised = platform.capabilities()
        self.assertIn("CONTACTED", advertised["events"])
        self.assertTrue(
            any(
                element == "1.1.0"
                or (
                    element.count(".") == 2
                    and element.split(".")[0] == "1"
                    and int(element.split(".")[1]) >= 1
                )
                for element in advertised["lcp_versions"]
            ),
            "the platform must advertise a full-semver v1.1 element (rollout §2)",
        )

        # Authorization rails stay: the losing (delivered-less) scoped buyer
        # is still refused.
        with self.assertRaises(RequestError) as refused:
            self._report_contacted(
                platform,
                buyer_id="buyer_001",
                secret=BUYER_SECRETS["buyer_001"],
                lead_id="adv-t6-001",
                hashes=[digest],
            )
        self.assertEqual(refused.exception.code, "LCP-002")

    # ─── T8: replay idempotency + set-union accumulation ───────────────────

    def test_contacted_report_replay_is_idempotent(self) -> None:
        """T8 (``plat: test_contacted_report_replay_is_idempotent``).

        LEP-0003 §4 corrected semantics (F9, dossier row 21): the event
        store dedupes by ``message.id`` - "two reports sharing one
        ``idempotency_key`` with fresh message ids both persist" - so
        reports are at-least-once with receiver-side collapse: exact
        re-sends of the same ``message.id` collapse to a ``DUPLICATE``
        ack, retried reports with new ids merge by digest **set**.
        #153 T8: "two reports sharing one ``idempotency_key``, different
        ``message.id`` -> contacted set unchanged".
        """
        platform = self._platform()
        platform.upsert_offer(_slot_offer("slot-t8-a", "buyer_001"))
        self._ingest_shared(platform, "adv-t8-001", max_buyers=2)
        self._bid(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-t8-001",
            price=1900,
        )
        self._delivered_reporter(platform, "adv-t8-001", "slot-t8-a", "buyer_001")

        first = self._digest(BUYER_SECRETS["buyer_001"], self.LEAD_PHONE)
        shared_key = "buyer-001-event-adv-t8-001-contacted-1"
        ack, first_event = self._report_contacted(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-t8-001",
            hashes=[first],
            message_id="ee0e8400-e840-4000-8000-000000000a01",
            idempotency_key=shared_key,
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        # (a) Exact re-send of the same message.id collapses receiver-side.
        replay, _ = self._report_contacted(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-t8-001",
            hashes=None,
            resend=first_event,
        )
        self.assertEqual(replay["lcp"]["payload"]["status"], "DUPLICATE")
        stored = {
            row["digest"] for row in platform.store.list_contacted_digests("adv-t8-001")
        }
        self.assertEqual(stored, {first})

        # (b) Same idempotency_key, fresh message.id, same digests: the
        # report persists (message.id dedupe only) but the contacted SET is
        # unchanged - set-union merge, not a second entry.
        retried, _ = self._report_contacted(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-t8-001",
            hashes=[first],
            message_id="ee0e8400-e840-4000-8000-000000000b02",
            idempotency_key=shared_key,
        )
        self.assertEqual(retried["lcp"]["payload"]["status"], "RECEIVED")
        stored_after_retry = {
            row["digest"] for row in platform.store.list_contacted_digests("adv-t8-001")
        }
        self.assertEqual(
            stored_after_retry, {first}, "the contacted set must be unchanged"
        )

        # (c) A fresh digest under a fresh message.id merges into the set.
        second = self._digest(BUYER_SECRETS["buyer_001"], "+15125550147")
        newer, _ = self._report_contacted(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-t8-001",
            hashes=[second],
            message_id="ee0e8400-e840-4000-8000-000000000c03",
            idempotency_key="buyer-001-event-adv-t8-001-contacted-2",
        )
        self.assertEqual(newer["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(
            {
                row["digest"]
                for row in platform.store.list_contacted_digests("adv-t8-001")
            },
            {first, second},
        )

    # ─── §4 ingest hygiene ──────────────────────────────────────────────────

    def test_contacted_report_ingest_hygiene(self) -> None:
        """LEP-0003 §4 ingest MUST (F5 conversion).

        "Receivers MUST reject ``CONTACTED`` reports whose
        ``details.contacted_hashes`` contains an entry failing
        ``^[a-f0-9]{64}$`` or exceeds **32 entries per report**, and MUST
        treat oversized or malformed reports as a validation rejection, not
        a store." And §4's report-direction absent-vs-``[]`` rule: "``[]`` =
        MUST NOT be sent as an event detail (it asserts nothing)."
        Beyond shape and count the trust model is explicit: entries are
        authenticated-sender attestations; there is no verification without
        the plaintext.
        """
        platform = self._platform()
        platform.upsert_offer(_slot_offer("slot-hyg-a", "buyer_001"))
        self._ingest_shared(platform, "adv-hyg-001", max_buyers=2)
        self._bid(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-hyg-001",
            price=1900,
        )
        self._delivered_reporter(platform, "adv-hyg-001", "slot-hyg-a", "buyer_001")

        rejected_values = [
            ["a94a8fe5ccb19ba61c4c0873d391e987982fbbd3"],  # SHA-1 shape (invalid A)
            ["A" * 64],  # uppercase is not the lowercase family
            ["a" * 63],  # the RF-1 shape: 63 hex fails the ingest MUST
            ["z" * 64],  # non-hex
            [12345],  # non-string element
            ["c" * 64] * 33,  # over the 32-entry report bound
            [],  # asserts nothing; MUST NOT be sent as event detail
            "not-a-list",  # wrong container type
        ]
        for bad in rejected_values:
            with self.assertRaises(RequestError) as refused:
                self._report_contacted(
                    platform,
                    buyer_id="buyer_001",
                    secret=BUYER_SECRETS["buyer_001"],
                    lead_id="adv-hyg-001",
                    hashes=bad,
                )
            self.assertEqual(refused.exception.code, "LCP-100")
        self.assertEqual(platform.store.list_contacted_digests("adv-hyg-001"), [])
        self.assertEqual(len(platform.store.list_events("adv-hyg-001")), 0)

        # Boundary: exactly 32 well-formed entries are a valid report.
        boundary = [f"{i:064x}" for i in range(32)]
        ack, _ = self._report_contacted(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-hyg-001",
            hashes=boundary,
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(len(platform.store.list_contacted_digests("adv-hyg-001")), 32)

    # ─── §3 re-keying onto later posts (phone-only, plaintext-held) ────────

    def test_contacted_digests_rekeyed_from_plaintext_onto_later_posts(self) -> None:
        """LEP-0003 §3: re-keying is "only from plaintext it holds (the lead
        values it delivered; §3 on re-keying) - never from ``CONTACTED``
        report digests", and the slot fields reach capability-gated
        receivers only. The reference platform hashes the delivered
        ``consumer.phone`` verbatim (``pair_phone_hash``) and never an
        email (no email hashing exists in this build; that path ships with
        the dedup-hash wave), so a later v1.1 buyer's post carries
        exactly the phone-derived pair-keyed digests it can verify against
        its own delivered value; unmatchable attestations are stored but
        never propagated.
        """
        platform = self._platform()
        third_secret = "buyer-secret-3"
        platform.upsert_credential(
            "buyer_001", hmac_secret=BUYER_SECRETS["buyer_001"], lcp_versions=["1.1.0"]
        )
        platform.upsert_credential(
            "buyer_002", hmac_secret=BUYER_SECRETS["buyer_002"], lcp_versions=["1.1.0"]
        )
        platform.upsert_credential("buyer_003", hmac_secret=third_secret)
        platform.upsert_offer(_slot_offer("slot-rk-1", "buyer_001"))
        platform.upsert_offer(_slot_offer("slot-rk-2", "buyer_002"))
        platform.upsert_offer(_slot_offer("slot-rk-3", "buyer_003"))
        self._ingest_shared(platform, "adv-rk-001", max_buyers=3)
        self._bid(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-rk-001",
            price=1900,
        )
        self._delivered_reporter(platform, "adv-rk-001", "slot-rk-1", "buyer_001")

        phone_digest = self._digest(BUYER_SECRETS["buyer_001"], self.LEAD_PHONE)
        stray = "f" * 64  # an attested contact the platform holds no plaintext for
        ack, _ = self._report_contacted(
            platform,
            buyer_id="buyer_001",
            secret=BUYER_SECRETS["buyer_001"],
            lead_id="adv-rk-001",
            hashes=[phone_digest, stray],
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        # Buyer_002 (advertises 1.1.0) wins next: its post re-keys the
        # delivered phone under buyer_002's pair secret.
        self._bid(
            platform,
            buyer_id="buyer_002",
            secret=BUYER_SECRETS["buyer_002"],
            lead_id="adv-rk-001",
            price=1800,
        )
        self._expire_pings(platform, "adv-rk-001", {"buyer_002"})
        platform._route_lead("adv-rk-001")
        post2 = next(
            post
            for post in _posts_for(platform, "adv-rk-001")
            if post["offer_id"] == "slot-rk-2"
        )
        env2 = platform.store.decode_envelope(post2["envelope_json"])
        expected2 = self._digest(BUYER_SECRETS["buyer_002"], self.LEAD_PHONE)
        self.assertEqual(env2["lcp"]["payload"].get("contacted_hashes"), [expected2])
        self.assertEqual(env2["lcp"]["version"], "1.1.0")

        # Buyer_003 never advertised v1.1: bit-for-bit v1.0 bytes, no slot
        # or contacted fields (invalid D's protection).
        self._bid(
            platform,
            buyer_id="buyer_003",
            secret=third_secret,
            lead_id="adv-rk-001",
            price=1700,
        )
        self._expire_pings(platform, "adv-rk-001", {"buyer_003"})
        platform._route_lead("adv-rk-001")
        post3 = next(
            post
            for post in _posts_for(platform, "adv-rk-001")
            if post["offer_id"] == "slot-rk-3"
        )
        env3 = platform.store.decode_envelope(post3["envelope_json"])
        self.assertNotIn("contacted_hashes", env3["lcp"]["payload"])
        self.assertEqual(env3["lcp"]["version"], "1.0.0")

        # The stray attestation was stored for its own pair... and never
        # emitted to another buyer (no plaintext to re-key from).
        stored = {
            row["digest"] for row in platform.store.list_contacted_digests("adv-rk-001")
        }
        self.assertEqual(stored, {phone_digest, stray})


class SlotAdvertisementTests(_AuctionFixtureMixin, unittest.TestCase):
    """LEP-0003 §1/§2 advertisement + claim gates (capability-gated fields).

    These exercise the wire surface added by the v1.1 schemas, so they bind
    the platform behaviour to the merged field shapes exactly as drafted in
    the record (``slots_available`` min 1 + the exclusive conditional on
    ping; ``slots_claimed`` 1..1 on bid).
    """

    def _advertising_platform(self) -> Platform:
        platform = _make_platform()
        platform.upsert_credential("pub_123", hmac_secret=PUBLISHER_SECRET)
        platform.upsert_credential(
            "buyer_001", hmac_secret=BUYER_SECRETS["buyer_001"], lcp_versions=["1.1.0"]
        )
        platform.upsert_credential("buyer_002", hmac_secret=BUYER_SECRETS["buyer_002"])
        self.addCleanup(platform.close)
        return platform

    def _ping_payloads(self, platform: Platform, lead_id: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for ping in platform.store.list_pings(lead_id):
            env = platform.store.decode_envelope(ping["envelope_json"])
            out[ping["buyer_id"]] = env
        return out

    def test_slots_available_only_advertised_to_v11_receivers_and_never_zero(
        self,
    ) -> None:
        """LEP-0003 §1 + Compatibility (F6/invalid D): "A sender MUST include
        ``slots_available`` only in pings to receivers whose §8 capability
        advertisement supports v1.1 - compared as parsed semver (an
        advertised element with ``major == 1`` and ``minor >= 1``), never as
        the string literal ``1.1``"; "The platform MUST NOT send a ping with
        ``slots_available: 0`` - zero remaining means stop pinging, not ping
        with zero."
        """
        platform = self._advertising_platform()
        platform.upsert_offer(_slot_offer("slot-g-1", "buyer_001"))
        platform.upsert_offer(_slot_offer("slot-g-2", "buyer_002"))
        self._ingest_shared(platform, "adv-gate-001", max_buyers=2)

        envelopes = self._ping_payloads(platform, "adv-gate-001")
        v11 = envelopes["buyer_001"]
        self.assertEqual(v11["lcp"]["payload"].get("slots_available"), 2)
        self.assertEqual(v11["lcp"]["version"], "1.1.0")
        plain = envelopes["buyer_002"]
        self.assertNotIn("slots_available", plain["lcp"]["payload"])
        self.assertEqual(plain["lcp"]["version"], "1.0.0")

        # Spend both buyer slots, then an additional offer must be refused
        # a ping rather than be shown zero remaining.
        ack, _ = self._bid_once(
            platform, "adv-gate-001", "buyer_001", BUYER_SECRETS["buyer_001"], 1700
        )
        self._bid_once(
            platform, "adv-gate-001", "buyer_002", BUYER_SECRETS["buyer_002"], 1600
        )
        self._expire_pings(platform, "adv-gate-001")
        platform._route_lead("adv-gate-001")
        awarded_buyers = {
            post["buyer_id"] for post in _posts_for(platform, "adv-gate-001")
        }
        self.assertEqual(awarded_buyers, {"buyer_001", "buyer_002"})

        platform.upsert_credential(
            "buyer_009", hmac_secret="buyer-secret-9", lcp_versions=["1.1.0"]
        )
        late_offer = _slot_offer("slot-g-9", "buyer_009")
        platform.upsert_offer(late_offer)
        lead_row = platform.store.get_lead("adv-gate-001")
        assert lead_row is not None
        lead_envelope = platform.store.decode_envelope(lead_row["envelope_json"])
        created = platform._create_ping(lead_envelope, late_offer)
        self.assertFalse(created, "a zero-remaining lead must stop pinging")
        self.assertFalse(platform.store.has_ping_for_offer("adv-gate-001", "slot-g-9"))

    def _bid_once(
        self, platform: Platform, lead_id: str, buyer_id: str, secret: str, price: int
    ) -> tuple[dict, dict]:
        ping = next(
            row
            for row in platform.store.list_pings(lead_id)
            if row["buyer_id"] == buyer_id
        )
        bid = _envelope(
            "bid",
            buyer_id,
            "platform_001",
            {
                "ping_id": ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": price,
                "currency": "AUD",
            },
            correlation_id=ping["message_id"],
            test=True,
        )
        headers, body = _signed_headers(bid, secret)
        return platform.submit_bid(bid, headers=headers, raw_body=body), bid

    def test_exclusive_advertises_exactly_one_slot(self) -> None:
        """§1: "``exclusivity: "exclusive"`` is exactly the one-slot special
        case: when present, ``slots_available`` MUST be ``1``" (and the
        drafted schema conditional rejects any other value)."""
        platform = self._advertising_platform()
        platform.upsert_offer(_slot_offer("slot-g-x", "buyer_001"))
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["message"]["id"] = str(uuid4())
        lead["lcp"]["message"]["idempotency_key"] = "gate-exclusive"
        lead["lcp"]["payload"]["lead_id"] = "adv-gate-002"
        lead["lcp"]["payload"].pop("expiry", None)
        lead["lcp"]["payload"]["exclusivity"] = {"exclusivity": "exclusive"}
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        service = PlatformService(platform)
        status, _, ack = service.dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        envelopes = self._ping_payloads(platform, "adv-gate-002")
        self.assertEqual(envelopes["buyer_001"]["lcp"]["payload"]["slots_available"], 1)

    def test_slots_claimed_one_honored_and_greater_rejected(self) -> None:
        """§2: "``minimum: 1, maximum: 1`` at v1.1 - single-slot awards
        only"; "The platform MUST NOT award more buyer slots than remain.
        Because the v1.1 range is exactly 1, a claim can never itself exceed
        the remaining count - the oversell surface is the award path."
        """
        platform = self._advertising_platform()
        platform.upsert_offer(_slot_offer("slot-c-1", "buyer_001"))
        platform.upsert_offer(_slot_offer("slot-c-2", "buyer_002"))
        self._ingest_shared(platform, "adv-claim-001", max_buyers=2)

        ping1 = next(
            row
            for row in platform.store.list_pings("adv-claim-001")
            if row["buyer_id"] == "buyer_001"
        )
        claim_bid = _envelope(
            "bid",
            "buyer_001",
            "platform_001",
            {
                "ping_id": ping1["ping_id"],
                "decision": "accept",
                "bid_price_cents": 1700,
                "currency": "AUD",
                "slots_claimed": 1,
            },
            correlation_id=ping1["message_id"],
            test=True,
        )
        claim_bid["lcp"]["version"] = "1.1.0"
        headers, body = _signed_headers(claim_bid, BUYER_SECRETS["buyer_001"])
        ack = platform.submit_bid(claim_bid, headers=headers, raw_body=body)
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        over = _envelope(
            "bid",
            "buyer_002",
            "platform_001",
            {
                "ping_id": next(
                    row
                    for row in platform.store.list_pings("adv-claim-001")
                    if row["buyer_id"] == "buyer_002"
                )["ping_id"],
                "decision": "accept",
                "bid_price_cents": 1600,
                "currency": "AUD",
                "slots_claimed": 2,
            },
            correlation_id=ping1["message_id"],
            test=True,
        )
        over["lcp"]["version"] = "1.1.0"
        headers2, body2 = _signed_headers(over, BUYER_SECRETS["buyer_002"])
        with self.assertRaises(RequestError) as refused:
            platform.submit_bid(over, headers=headers2, raw_body=body2)
        self.assertEqual(refused.exception.code, "LCP-100")

        # The honoured claim is one buyer slot; award accounting is
        # unchanged by the claim value.
        self._expire_pings(platform, "adv-claim-001")
        platform._route_lead("adv-claim-001")
        awarded = {post["buyer_id"] for post in _posts_for(platform, "adv-claim-001")}
        self.assertEqual(awarded, {"buyer_001"}, "only the accepted claim was awarded")


if __name__ == "__main__":
    unittest.main()

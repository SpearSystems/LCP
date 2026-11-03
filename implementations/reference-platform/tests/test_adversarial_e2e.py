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
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
import unittest
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


if __name__ == "__main__":
    unittest.main()

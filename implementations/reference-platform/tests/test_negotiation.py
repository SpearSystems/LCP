"""LEP-0009 blocking tests: bounded negotiation between agents.

The feature flag is ON for every test here (CI exercises the surface that
ships off by default). Each test names the LEP `## Tests` row it proves.
"""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.messages import (
    _envelope,
    hash_negotiation_token,
    new_negotiation_token,
)
from lcp_platform.router import Platform, RequestError
from lcp_platform.service import PlatformService
from lcp_platform.storage import now_iso, parse_iso_datetime, format_iso_datetime


ROOT = Path(__file__).resolve().parents[3]
PING_EXPIRY_GAP_SECONDS = 120


class NegotiationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.platform = self._platform(feature_negotiation=True)
        self.platform.upsert_credential(
            "buyer_001",
            hmac_secret="buyer-001-secret",
            lcp_versions=["1.0.0", "1.1.0", "1.2.0"],
            tenant_id="default",
        )
        self.platform.upsert_credential(
            "buyer_002",
            hmac_secret="buyer-002-secret",
            lcp_versions=["1.2.0"],
            tenant_id="default",
        )
        self.platform.upsert_credential(
            "buyer_other",
            hmac_secret="buyer-other-secret",
            lcp_versions=["1.2.0"],
            tenant_id="tenant_other",
        )
        self.platform.upsert_credential(
            "buyer_legacy",
            hmac_secret="buyer-legacy-secret",
            lcp_versions=["1.0.0", "1.1.0"],
            tenant_id="default",
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
                "require_consent_evidence": True,
                "ping_timeout_seconds": PING_EXPIRY_GAP_SECONDS,
            }
        )
        self.ping, self.ping_envelope = self._create_ping()

    def tearDown(self) -> None:
        self.platform.close()
        self.tempdir.cleanup()

    def _platform(self, *, feature_negotiation: bool) -> Platform:
        return Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                feature_negotiation=feature_negotiation,
                pii_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
                attachment_directory=Path(self.tempdir.name) / "attachments",
            )
        )

    def _create_ping(self):
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440099"
        lead["lcp"]["message"]["idempotency_key"] = "negotiation-lead-001"
        lead["lcp"]["payload"]["lead_id"] = "lead_negotiation_001"
        lead["lcp"]["payload"].pop("expiry", None)
        self.platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
        self.platform.process_once()
        pings = self.platform.store.list_pings("lead_negotiation_001")
        self.assertTrue(pings)
        envelope = self.platform.store.decode_envelope(pings[0]["envelope_json"])
        return pings[0], envelope

    # ─── helpers ──────────────────────────────────────────────────────────

    @property
    def ping_payload(self) -> dict:
        return self.ping_envelope["lcp"]["payload"]

    def _quote(
        self,
        negotiation_id: str | None = None,
        *,
        price: int = 2000,
        max_rounds: int | None = 3,
        rounds_remaining: int | None = None,
        expires_at: str | None = None,
        receiver_id: str = "buyer_001",
        ping=None,
    ):
        negotiation_id = negotiation_id or f"neg_{uuid4().hex}"
        ping = ping or self.ping
        ping_envelope = (
            self.ping_envelope
            if ping is self.ping
            else self.platform.store.decode_envelope(ping["envelope_json"])
        )
        payload = {
            "negotiation_id": negotiation_id,
            "ping_id": ping["ping_id"],
            "offer_id": "mortgage-au",
            "price_cents": price,
            "currency": "AUD",
            "floor_price_cents": ping_envelope["lcp"]["payload"]["floor_price_cents"],
            "rounds_remaining": (
                rounds_remaining if rounds_remaining is not None else (max_rounds or 3)
            ),
            "expires_at": expires_at or ping_envelope["lcp"]["payload"]["expires_at"],
        }
        if max_rounds is not None:
            payload["max_rounds"] = max_rounds
        envelope = _envelope(
            "quote",
            "platform_001",
            receiver_id,
            payload,
            correlation_id=ping_envelope["lcp"]["message"]["id"],
            version="1.2.0",
            test=True,
        )
        ack = self.platform.submit_quote(
            envelope,
            headers={"X-LCP-Test": "true"},
            raw_body=json.dumps(envelope).encode(),
        )
        return ack, envelope

    def _counter(
        self,
        negotiation_id: str,
        correlation_id: str,
        *,
        price: int | None = 1800,
        rounds_remaining: int = 3,
        decision: str = "counter",
        sender_id: str = "buyer_001",
        receiver_id: str | None = None,
        expires_at: str | None = None,
        timestamp: str | None = None,
        rejection_reason: str | None = None,
        ping=None,
    ):
        ping = ping or self.ping
        payload = {
            "negotiation_id": negotiation_id,
            "ping_id": ping["ping_id"],
            "offer_id": "mortgage-au",
            "decision": decision,
            "currency": "AUD",
            "floor_price_cents": 1500,
            "rounds_remaining": rounds_remaining,
            "expires_at": expires_at or self.ping_payload["expires_at"],
        }
        if price is not None:
            payload["price_cents"] = price
        if rejection_reason is not None:
            payload["rejection_reason"] = rejection_reason
        envelope = _envelope(
            "counter",
            sender_id,
            receiver_id
            or ("buyer_001" if sender_id == "platform_001" else "platform_001"),
            payload,
            correlation_id=correlation_id,
            version="1.2.0",
            test=True,
        )
        if timestamp is not None:
            envelope["lcp"]["message"]["timestamp"] = timestamp
        ack = self.platform.submit_counter(
            envelope,
            headers={"X-LCP-Test": "true"},
            raw_body=json.dumps(envelope).encode(),
        )
        return ack, envelope

    def _bid(
        self,
        token: str,
        *,
        sender_id: str = "buyer_001",
        idempotency_key: str | None = None,
        bid_price_cents: int | None = None,
    ):
        payload: dict = {
            "ping_id": self.ping["ping_id"],
            "decision": "accept",
            "negotiation_binding_token": token,
        }
        if bid_price_cents is not None:
            payload["bid_price_cents"] = bid_price_cents
        envelope = _envelope(
            "bid",
            sender_id,
            "platform_001",
            payload,
            correlation_id=self.ping_envelope["lcp"]["message"]["id"],
            idempotency_key=idempotency_key,
            version="1.2.0",
            test=True,
        )
        ack = self.platform.submit_bid(
            envelope,
            headers={"X-LCP-Test": "true"},
            raw_body=json.dumps(envelope).encode(),
        )
        return ack, envelope

    def _ack_negotiation(self, ack: dict) -> dict:
        return ack["lcp"]["payload"]["negotiation"]

    def _negotiation_row(self, negotiation_id: str):
        return self.platform.store.get_negotiation(negotiation_id)

    def _count(self, table: str, where: str = "", values: tuple = ()) -> int:
        query = f"SELECT COUNT(*) AS count FROM {table}"
        if where:
            query += f" WHERE {where}"
        row = self.platform.store._connection.execute(query, values).fetchone()
        return int(row["count"])

    # ─── T1: bounded rounds ───────────────────────────────────────────────

    def test_negotiation_round_cap(self) -> None:
        # max_rounds absent => 3 (D8 default).
        negotiation_id = f"neg_{uuid4().hex}"
        ack, quote = self._quote(negotiation_id, price=3000, max_rounds=None)
        self.assertEqual(self._ack_negotiation(ack)["rounds_remaining"], 3)

        previous_rounds = 3
        for index, price in enumerate((1600, 1700, 1800)):
            ack, _ = self._counter(
                negotiation_id,
                quote["lcp"]["message"]["id"],
                price=price,
                rounds_remaining=previous_rounds,
            )
            self.assertEqual(self._ack_negotiation(ack)["outcome"], "countered")
            previous_rounds -= 1
            self.assertEqual(
                self._ack_negotiation(ack)["rounds_remaining"], previous_rounds
            )
        self.assertEqual(self._negotiation_row(negotiation_id)["rounds_remaining"], 0)

        # Fourth buyer counter is refused by the state cap, not by the echo.
        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=1900,
            rounds_remaining=0,
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "REJECTED")
        self.assertEqual(
            ack["lcp"]["payload"]["rejection_reason"], "round_limit_exceeded"
        )
        self.assertEqual(self._negotiation_row(negotiation_id)["status"], "REFUSED")

        # max_rounds: 5 allows exactly five buyer counters.
        five_id = f"neg_{uuid4().hex}"
        ack, quote5 = self._quote(five_id, price=3000, max_rounds=5)
        self.assertEqual(self._ack_negotiation(ack)["rounds_remaining"], 5)
        rounds = 5
        for price in (1600, 1700, 1800, 1900, 2000):
            ack, _ = self._counter(
                five_id,
                quote5["lcp"]["message"]["id"],
                price=price,
                rounds_remaining=rounds,
            )
            rounds -= 1
        self.assertEqual(self._negotiation_row(five_id)["rounds_remaining"], 0)

        # max_rounds: 6 is LCP-100 at quote intake, before any row exists.
        with self.assertRaises(RequestError) as context:
            self._quote(f"neg_{uuid4().hex}", max_rounds=6, rounds_remaining=6)
        self.assertEqual(context.exception.code, "LCP-100")

        # A stale echo is refused; the stored count is what the platform
        # counts with (the counter consumes one from state, not from the echo).
        stale_id = f"neg_{uuid4().hex}"
        ack, stale_quote = self._quote(stale_id, price=3000, max_rounds=3)
        first, _ = self._counter(
            stale_id,
            stale_quote["lcp"]["message"]["id"],
            price=1600,
            rounds_remaining=3,
        )
        self.assertEqual(first["lcp"]["payload"]["negotiation"]["rounds_remaining"], 2)
        with self.assertRaises(RequestError) as context:
            self._counter(
                stale_id,
                stale_quote["lcp"]["message"]["id"],
                price=1700,
                rounds_remaining=3,
            )
        self.assertEqual(context.exception.code, "LCP-100")
        self.assertEqual(self._negotiation_row(stale_id)["rounds_remaining"], 2)
        second, _ = self._counter(
            stale_id,
            stale_quote["lcp"]["message"]["id"],
            price=1700,
            rounds_remaining=2,
        )
        self.assertEqual(self._negotiation_row(stale_id)["rounds_remaining"], 1)

    # ─── T2: monotone ─────────────────────────────────────────────────────

    def test_negotiation_monotone_enforced(self) -> None:
        negotiation_id = f"neg_{uuid4().hex}"
        _, quote = self._quote(negotiation_id, price=3000)

        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=1800,
            rounds_remaining=3,
        )
        self.assertEqual(self._ack_negotiation(ack)["outcome"], "countered")
        row = self._negotiation_row(negotiation_id)
        self.assertEqual(row["status"], "COUNTERED")
        self.assertEqual(row["rounds_remaining"], 2)
        self.assertEqual(row["buyer_price_cents"], 1800)

        # Buyer walks the price backwards: refused, no round burned, the
        # stored standing price unchanged.
        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=1700,
            rounds_remaining=2,
        )
        self.assertEqual(
            ack["lcp"]["payload"]["rejection_reason"], "non_monotone_counter"
        )
        row = self._negotiation_row(negotiation_id)
        self.assertEqual(row["status"], "REFUSED")
        self.assertEqual(row["rounds_remaining"], 2)
        self.assertEqual(row["buyer_price_cents"], 1800)

        # A legitimate climb in a fresh negotiation moves COUNTERED.
        climb_id = f"neg_{uuid4().hex}"
        _, climb_quote = self._quote(climb_id, price=3000)
        self._counter(
            climb_id,
            climb_quote["lcp"]["message"]["id"],
            price=1800,
            rounds_remaining=3,
        )
        ack, _ = self._counter(
            climb_id,
            climb_quote["lcp"]["message"]["id"],
            price=1900,
            rounds_remaining=2,
        )
        self.assertEqual(self._ack_negotiation(ack)["outcome"], "countered")
        self.assertEqual(self._negotiation_row(climb_id)["status"], "COUNTERED")

        # Platform mirror: a platform counter below its own standing ask is
        # the same refusal (a concession is an accept, never a lower counter).
        platform_id = f"neg_{uuid4().hex}"
        _, platform_quote = self._quote(platform_id, price=3000)
        buyer_counter, buyer_envelope = self._counter(
            platform_id,
            platform_quote["lcp"]["message"]["id"],
            price=1800,
            rounds_remaining=3,
        )
        ack, _ = self._counter(
            platform_id,
            buyer_envelope["lcp"]["message"]["id"],
            price=1900,
            rounds_remaining=2,
            decision="counter",
            sender_id="platform_001",
        )
        self.assertEqual(
            ack["lcp"]["payload"]["rejection_reason"], "non_monotone_counter"
        )

        # Platform restating/raising its ask is legal and keeps the state.
        raise_id = f"neg_{uuid4().hex}"
        _, raise_quote = self._quote(raise_id, price=2000)
        _, buyer_envelope = self._counter(
            raise_id,
            raise_quote["lcp"]["message"]["id"],
            price=1800,
            rounds_remaining=3,
        )
        ack, _ = self._counter(
            raise_id,
            buyer_envelope["lcp"]["message"]["id"],
            price=2000,
            rounds_remaining=2,
            sender_id="platform_001",
        )
        self.assertEqual(self._ack_negotiation(ack)["outcome"], "countered")
        self.assertEqual(self._negotiation_row(raise_id)["platform_price_cents"], 2000)

    # ─── T3: floor refusal never clamps ───────────────────────────────────

    def test_negotiation_floor_refusal_never_clamps(self) -> None:
        negotiation_id = f"neg_{uuid4().hex}"
        _, quote = self._quote(negotiation_id, price=2000)
        bids_before = self._count("bids")
        tokens_before = self._count("negotiation_token")

        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=1000,
            rounds_remaining=3,
        )
        payload = ack["lcp"]["payload"]
        self.assertEqual(payload["status"], "REJECTED")
        self.assertEqual(payload["rejection_reason"], "below_floor_price")
        self.assertEqual(
            [error["field"] for error in payload["errors"]], ["payload.price_cents"]
        )
        self.assertEqual(
            self._ack_negotiation(ack)["price_cents"], 1000, "echo the stated price"
        )
        row = self._negotiation_row(negotiation_id)
        self.assertEqual(row["status"], "REFUSED")
        self.assertEqual(row["platform_price_cents"], 2000)
        self.assertIsNone(row["buyer_price_cents"])
        self.assertEqual(self._count("bids"), bids_before)
        self.assertEqual(self._count("negotiation_token"), tokens_before)
        self.assertEqual(self._count("deliveries", "kind = 'post'"), 0)

        # A quote below the restated floor is refused before any row exists.
        refused_id = f"neg_{uuid4().hex}"
        ack, _ = self._quote(refused_id, price=1000)
        self.assertEqual(ack["lcp"]["payload"]["rejection_reason"], "below_floor_price")
        self.assertIsNone(self._negotiation_row(refused_id))

    # ─── T4: absolute expiry ──────────────────────────────────────────────

    def test_negotiation_expiry_absolute(self) -> None:
        # A quote whose expires_at exceeds the ping's expiry is LCP-100 and
        # writes no row.
        ping_expiry = parse_iso_datetime(self.ping_payload["expires_at"])
        beyond = format_iso_datetime(ping_expiry + timedelta(seconds=60))
        with self.assertRaises(RequestError) as context:
            self._quote(f"neg_{uuid4().hex}", expires_at=beyond)
        self.assertEqual(context.exception.code, "LCP-100")

        negotiation_id = f"neg_{uuid4().hex}"
        _, quote = self._quote(negotiation_id, price=2000)
        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=1800,
            rounds_remaining=3,
            timestamp=self.ping_payload["expires_at"],
        )
        self.assertEqual(
            ack["lcp"]["payload"]["rejection_reason"], "negotiation_expired"
        )
        self.assertEqual(self._negotiation_row(negotiation_id)["status"], "EXPIRED")

        # The sweep is idempotent: a second pass audits nothing new.
        self.platform._expire_negotiations()
        self.platform._expire_negotiations()
        expired_audits = [
            event
            for event in self.platform.store.list_audit_events(
                "negotiation", negotiation_id
            )
            if event["action"] == "negotiation.expired"
        ]
        self.assertEqual(len(expired_audits), 1)

        # A "fresh" timestamp inside the replay window does not extend or
        # reopen anything: the terminal id answers negotiation_closed.
        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=1900,
            rounds_remaining=3,
            timestamp=now_iso(),
        )
        self.assertEqual(
            ack["lcp"]["payload"]["rejection_reason"], "negotiation_closed"
        )
        self.assertEqual(self._negotiation_row(negotiation_id)["status"], "EXPIRED")

    # ─── T5: single-use, idempotent token ─────────────────────────────────

    def test_negotiation_token_single_use_idempotent(self) -> None:
        negotiation_id = f"neg_{uuid4().hex}"
        _, quote = self._quote(negotiation_id, price=2000)
        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=2000,
            decision="accept",
            rounds_remaining=3,
        )
        negotiation = self._ack_negotiation(ack)
        token = negotiation["binding_token"]
        self.assertTrue(token.startswith("tok_"))
        self.assertEqual(negotiation["outcome"], "accepted")

        # The state table's illegal transitions: ACCEPTED -> COUNTERED and a
        # second accept are LCP-004, not silent state changes.
        with self.assertRaises(RequestError) as context:
            self._counter(
                negotiation_id,
                quote["lcp"]["message"]["id"],
                price=2100,
                rounds_remaining=3,
            )
        self.assertEqual(context.exception.code, "LCP-004")
        with self.assertRaises(RequestError) as context:
            self._counter(
                negotiation_id,
                quote["lcp"]["message"]["id"],
                price=2000,
                decision="accept",
                rounds_remaining=3,
            )
        self.assertEqual(context.exception.code, "LCP-004")

        bids_before = self._count("bids")
        first_ack, envelope = self._bid(token, idempotency_key="redeem-key-1")
        self.assertEqual(first_ack["lcp"]["payload"]["status"], "RECEIVED")
        self.assertEqual(self._ack_negotiation(first_ack)["outcome"], "redeemed")
        self.assertEqual(self._count("bids"), bids_before + 1)
        self.assertEqual(self._negotiation_row(negotiation_id)["status"], "REDEEMED")
        bid_row = self.platform.store.list_bids_for_lead("lead_negotiation_001")[0]
        self.assertEqual(int(bid_row["bid_price_cents"]), 2000)

        # Same envelope bytes + same key: the original ack, no second bid, no
        # second payable.
        replay_ack = self.platform.submit_bid(
            envelope,
            headers={"X-LCP-Test": "true"},
            raw_body=json.dumps(envelope).encode(),
        )
        self.assertEqual(replay_ack, first_ack)
        self.assertEqual(self._count("bids"), bids_before + 1)

        # REDEEMED -> anything (a counter message) is LCP-004.
        with self.assertRaises(RequestError) as context:
            self._counter(
                negotiation_id,
                quote["lcp"]["message"]["id"],
                price=2100,
                rounds_remaining=3,
            )
        self.assertEqual(context.exception.code, "LCP-004")

        # Same token, different key after REDEEMED: closed, no second bid.
        closed_ack, _ = self._bid(token, idempotency_key="redeem-key-2")
        self.assertEqual(
            closed_ack["lcp"]["payload"]["rejection_reason"], "negotiation_closed"
        )
        self.assertEqual(self._count("bids"), bids_before + 1)

        # Same token from another sender: LCP-002.
        with self.assertRaises(RequestError) as context:
            self._bid(token, sender_id="buyer_002", idempotency_key="redeem-key-3")
        self.assertEqual(context.exception.code, "LCP-002")

        # Only a hash is stored; the plaintext never appears in storage or
        # in an audit row (the operational rule for token bytes).
        token_rows = self.platform.store._connection.execute(
            "SELECT * FROM negotiation_token"
        ).fetchall()
        self.assertEqual(len(token_rows), 1)
        serialized = json.dumps([dict(row) for row in token_rows])
        self.assertNotIn(token, serialized)
        self.assertEqual(
            str(token_rows[0]["token_hash"]), hash_negotiation_token(token)
        )
        audits = json.dumps(
            [
                event
                for event in self.platform.store.list_audit_events(
                    "negotiation", negotiation_id
                )
            ]
        )
        self.assertNotIn(token, audits)
        round_acks = json.dumps(
            [
                dict(row)["ack_json"]
                for row in self.platform.store.list_negotiation_rounds(
                    negotiation_id
                )
            ]
        )
        self.assertNotIn(token, round_acks)

    # ─── T6: capability absence ───────────────────────────────────────────

    def test_negotiation_capability_absence(self) -> None:
        off = self._platform(feature_negotiation=False)
        try:
            capabilities = off.capabilities()
            self.assertNotIn("negotiation", capabilities["features"])
            self.assertNotIn("quote", capabilities["message_types"])
            self.assertNotIn("counter", capabilities["message_types"])
            quote = _envelope(
                "quote",
                "platform_001",
                "buyer_001",
                {
                    "negotiation_id": f"neg_{uuid4().hex}",
                    "ping_id": "ping_x",
                    "offer_id": "mortgage-au",
                    "price_cents": 2000,
                    "currency": "AUD",
                    "floor_price_cents": 1500,
                    "rounds_remaining": 3,
                    "expires_at": "2026-11-04T12:10:00Z",
                },
                correlation_id="d70e8400-0000-4000-8000-000000000021",
                version="1.2.0",
                test=True,
            )
            with self.assertRaises(RequestError) as context:
                off.submit_quote(quote, raw_body=json.dumps(quote).encode())
            self.assertEqual(context.exception.code, "LCP-006")
            self.assertIsNone(
                off.store.get_negotiation(quote["lcp"]["payload"]["negotiation_id"])
            )
            self.assertEqual(
                off.store._connection.execute(
                    "SELECT COUNT(*) AS count FROM negotiation"
                ).fetchone()["count"],
                0,
            )

            service = PlatformService(off)
            status, _, body = service.dispatch(
                "POST",
                "/v1/lcp/quotes",
                headers={"Content-Type": "application/json"},
                body=json.dumps(quote).encode(),
            )
            self.assertEqual(status, 404)
            self.assertEqual(body["errors"][0]["code"], "LCP-001")
        finally:
            off.close()

        # Flag on, receiver advertising only 1.1.0: the sender refuses to emit.
        with self.assertRaises(RequestError) as context:
            self._quote(f"neg_{uuid4().hex}", max_rounds=3, receiver_id="buyer_legacy")
        self.assertEqual(context.exception.code, "LCP-006")

        # A peer with features but a lower lcp_versions element never opens
        # the gate: only the parsed semver advertisement does.
        with self.assertRaises(RequestError) as context:
            self._counter(
                f"neg_{uuid4().hex}",
                self.ping_envelope["lcp"]["message"]["id"],
                sender_id="buyer_legacy",
            )
        self.assertEqual(context.exception.code, "LCP-006")

    # ─── T7: tenant authorization ─────────────────────────────────────────

    def test_negotiation_tenant_authorization(self) -> None:
        negotiation_id = f"neg_{uuid4().hex}"
        _, quote = self._quote(negotiation_id, price=2000)

        # Buyer B presenting buyer A's id: LCP-002, no state change.
        with self.assertRaises(RequestError) as context:
            self._counter(
                negotiation_id,
                quote["lcp"]["message"]["id"],
                sender_id="buyer_002",
            )
        self.assertEqual(context.exception.code, "LCP-002")
        self.assertIsNone(context.exception.details)
        row = self._negotiation_row(negotiation_id)
        self.assertEqual(row["buyer_id"], "buyer_001")
        self.assertEqual(row["status"], "OPEN")

        # A different tenant is refused identically.
        with self.assertRaises(RequestError) as context:
            self._counter(
                negotiation_id,
                quote["lcp"]["message"]["id"],
                sender_id="buyer_other",
            )
        self.assertEqual(context.exception.code, "LCP-002")

        # The negotiation row never becomes visible outside its tenant
        # predicate: the platform has no read route, and the store's lookup
        # still names the owning buyer after both attempts.
        self.assertEqual(self._negotiation_row(negotiation_id)["buyer_id"], "buyer_001")

    # ─── T8: retention and erasure ────────────────────────────────────────

    def test_negotiation_retention_erasure(self) -> None:
        negotiation_id = f"neg_{uuid4().hex}"
        _, quote = self._quote(negotiation_id, price=2000)
        ack, _ = self._counter(
            negotiation_id,
            quote["lcp"]["message"]["id"],
            price=2000,
            decision="accept",
            rounds_remaining=3,
        )
        token = self._ack_negotiation(ack)["binding_token"]
        bids_before = self._count("bids")

        self.platform.erase_lead("lead_negotiation_001", actor_id="privacy_operator")

        # The negotiation trail survives erasure: ids, prices, timestamps, and
        # no consumer field (there is none to find).
        row = self._negotiation_row(negotiation_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "REFUSED")
        negotiation_columns = set(row.keys())
        for forbidden in ("phone", "email", "full_name", "consumer", "contact"):
            self.assertNotIn(forbidden, negotiation_columns)
        metadata = json.dumps(
            [
                event["metadata"]
                for event in self.platform.store.list_audit_events(
                    "negotiation", negotiation_id
                )
            ]
        )
        self.assertNotIn("+61", metadata)
        self.assertNotIn("jane.smith@example.com", metadata)
        self.assertIn(
            "negotiation.refused",
            json.dumps(
                [
                    event["action"]
                    for event in self.platform.store.list_audit_events(
                        "negotiation", negotiation_id
                    )
                ]
            ),
        )

        # The token stays hashed and is unreredeemable after erasure.
        token_row = self.platform.store.get_negotiation_token(
            hash_negotiation_token(token)
        )
        self.assertIsNotNone(token_row)
        self.assertIsNone(token_row["redeemed_at"])
        closed_ack, _ = self._bid(token, idempotency_key="post-erase-redeem")
        self.assertEqual(
            closed_ack["lcp"]["payload"]["rejection_reason"], "negotiation_closed"
        )
        self.assertEqual(self._count("bids"), bids_before)

        # Non-PII retention: past the deadline the row is unreachable and the
        # purge removes ids/prices, while the audit trail stays addressable.
        self.platform.store._connection.execute(
            "UPDATE negotiation SET retention_expires_at = ? WHERE negotiation_id = ?",
            ("2000-01-01T00:00:00Z", negotiation_id),
        )
        self.platform.store._connection.commit()
        self.assertIsNone(self._negotiation_row(negotiation_id))
        purged = self.platform.store.purge_expired_negotiations()
        self.assertGreaterEqual(purged, 1)
        self.assertEqual(
            self._count("negotiation", "negotiation_id = ?", (negotiation_id,)), 0
        )
        self.assertEqual(
            self._count("negotiation_round", "negotiation_id = ?", (negotiation_id,)), 0
        )
        self.assertEqual(
            self._count("negotiation_token", "negotiation_id = ?", (negotiation_id,)), 0
        )
        audit_actions = [
            event["action"]
            for event in self.platform.store.list_audit_events(
                "negotiation", negotiation_id
            )
        ]
        self.assertIn("negotiation.accepted", audit_actions)


if __name__ == "__main__":
    unittest.main()

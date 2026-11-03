"""SPEC-COVERAGE SEC-06 / SEC-08 / SEC-12 / SEC-14 gap work on the reference platform.

SEC-06 — SPEC.md §9 Authentication, verbatim (SPEC @
ace8861561f4dbe80bca17b42d41b6cca453df6c):

    "The signature is the lowercase hexadecimal HMAC-SHA256 digest of that
    input. `X-LCP-Idempotency-Key` MUST match
    `lcp.message.idempotency_key` on mutating messages."

Presence of the header is credited elsewhere (`ENV-05`, `SEC-02`,
`plat:test_http_requires_json_and_idempotency_headers`); the *equality* of
the header against the envelope field was never asserted. These tests drive
the mismatch through both shipped enforcement points: the HTTP service gate
(`service.py:_post`, ahead of any ingest/PII handling) and the HMAC verifier
(`auth.py:authenticate`, "Header/body idempotency keys differ"). Both refuse
with LCP-012 / 401 — SPEC §5: `LCP-012 SIGNATURE_INVALID` 401.

SEC-08 — SPEC.md §9 Phone hash (dedup), verbatim (same SHA):

    "`phone_hash = HMAC-SHA256(shared_secret, normalized_e164)"
    "- The `shared_secret` is **per-pair** — the publisher hashes with a
      secret shared with each specific buyer. Each buyer receives a
      different hash for the same phone number."
    "- **Privacy property:** an attacker who intercepts a ping cannot
      reverse the hash without the shared secret. Unlike unsalted SHA-256,
      a precomputed rainbow table over the phone-number keyspace is
      useless."

One lead, two buyers with different pair secrets: each buyer's ping carries
its own digest; the digests are non-correlated (neither equals the other,
neither is an unsalted SHA-256/SHA-1 of the phone, and one is not the other
re-keyed under the wrong secret); and the digest under the vector pair
secret reproduces the pinned ``test-vectors/sdk/contact-hash.json`` row
byte-exactly — SPEC §9 Contact hashes, "Pinned reference vectors":
"SDK builders and the platform MUST reproduce every row".

SEC-12 — SPEC.md §9 Agent-as-consumer timeout, verbatim (same SHA):

    "When a buyer's agent evaluates a delivered lead (post), the agent MUST
    respond within **30 seconds**. If no response is received, the lead
    auto-transitions to `EXPIRED` and may be re-offered to the next buyer
    in the routing order. The 30-second timeout is the default; endpoints
    MAY advertise a different timeout in their capability response."

The platform ships the response-timeout policy on the offer's
``ping_timeout_seconds`` window (default 30, ``messages.py``): an unanswered
window expires the ping through the claim sweep; a lead nobody answered
auto-transitions to EXPIRED; a timed-out agent does not strand the lead from
the buyer that answered inside its window. The *post-delivery* leg of the
clause (a delivered post whose consuming agent stays silent past a deadline
that flips POSTED -> EXPIRED and re-offers downstream) is NOT shipped —
recorded as a FINDING in https://github.com/SpearSystems/LCP/issues/193;
nothing here asserts it.

SEC-14 — SPEC.md §9 Contact hashes, verbatim (same SHA):

    "**The reference platform hashes phone only today.**" … "It is a
    **pin-in-progress, not yet shipped behavior**: no email-form
    ``contacted_hashes`` entry ships until this canonical is confirmed and
    the executed dedup-hash vectors (#153 **T9**, ``lcp-sdk-dedup-hash-001..N``,
    run by all ten SDKs and the reference platform) publish."

Email-form *computing* shipped with #171 (every SDK reproduces the pinned
``contact-hash.json`` rows); *emission* stays gated. The two
``test_sec14_*`` cases pin the gate itself on the platform's wire surface: a
correctly-keyed email-form attestation is stored for its own pair yet only
the phone-derived re-key reaches a later v1.1 post, and an email-only report
set emits no ``contacted_hashes`` at all. The row remains a governance-level
gap (the §9 pin plus #153 T9 publication, not a test), so these ids are
partial coverage, not a green claim.

SEC-09 (agent attestation verification / forged-token rejection) has NO
shipped behavior on any surface: the platform accepts free-text, wrong-
signature, and expired attestation tokens unverified while advertising
``conformance_level: "L3"`` — recorded as a FINDING in
https://github.com/SpearSystems/LCP/issues/192; no test here pretends the
rejection path exists.

All tests are deterministic and network-free: webhook targets are the
non-routable loopback port ``http://127.0.0.1:1/`` (immediate connection
refusal, never a delivery ack), and ``test_mode=True`` carries the
``X-LCP-Test`` flag through the flow.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from lcp_platform.auth import AuthenticationError, signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.messages import _envelope
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "test-vectors" / "sdk" / "contact-hash.json"

PUBLISHER_SECRET = "gapsec-publisher-secret"
# The pair secret pinned by test-vectors/sdk/contact-hash.json.
VECTOR_PAIR_SECRET = "pair-secret-for-vectors-0123456789"
# An added second-secret computation: a *different* pair secret shared with
# a different buyer for the SAME plaintext phone (the per-pair property).
SECOND_PAIR_SECRET = "second-pair-secret-klmnopqrstuvwxyz012345"
VECTOR_PHONE = "+15125550147"
DEAD_TARGET = "http://127.0.0.1:1/buyer"
_NAMESPACE = uuid5(NAMESPACE_URL, "lcp-gap-security")


def _compact(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


def _pair_digest(secret: str, preimage: str) -> str:
    """Independent stdlib recomputation of the §9 per-pair contact digest."""
    return hmac.new(
        secret.encode("utf-8"), preimage.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _load_lead(lead_id: str, *, idempotency_key: str) -> dict[str, Any]:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        lead = json.load(handle)
    lead["lcp"]["message"]["id"] = str(uuid5(_NAMESPACE, lead_id))
    lead["lcp"]["message"]["test"] = True
    lead["lcp"]["message"]["idempotency_key"] = idempotency_key
    lead["lcp"]["payload"]["lead_id"] = lead_id
    lead["lcp"]["payload"].pop("expiry", None)
    return lead


def _offer(offer_id: str, buyer_id: str, **overrides: object) -> dict[str, Any]:
    offer: dict[str, Any] = {
        "offer_id": offer_id,
        "buyer_id": buyer_id,
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


def _signed_headers(
    envelope: dict[str, Any], secret: str, *, header_key: str | None = None
) -> tuple[dict[str, str], bytes]:
    """Build §9-canonical HMAC headers over the transmitted header key."""
    body = _compact(envelope)
    message = envelope["lcp"]["message"]
    key = header_key if header_key is not None else message["idempotency_key"]
    timestamp = now_iso()
    headers = {
        "Content-Type": "application/json",
        "X-LCP-Test": "true",
        "X-LCP-Sender-Id": message["sender_id"],
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Idempotency-Key": key,
        "X-LCP-Signature": signature_for(secret, timestamp, key, body),
    }
    return headers, body


class GapSecurityTests(unittest.TestCase):
    def _platform(self, *, require_auth: bool) -> Platform:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=require_auth,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.addCleanup(platform.close)
        platform.upsert_credential("pub_123", hmac_secret=PUBLISHER_SECRET)
        return platform

    # ─── SEC-06: header key MUST equal the envelope key ─────────────────────

    def test_sec06_mismatched_header_key_refused_before_ingest(self) -> None:
        platform = self._platform(require_auth=True)
        platform.upsert_offer(_offer("gapsec-idem", "buyer_001"))
        lead = _load_lead("gapsec-idem-mismatch", idempotency_key="gapsec-envelope-key")
        headers, body = _signed_headers(
            lead, PUBLISHER_SECRET, header_key="gapsec-header-key"
        )
        status, _, response = PlatformService(platform).dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 401)
        error = response["errors"][0]
        self.assertEqual(error["code"], "LCP-012")
        self.assertIn("must match the envelope", error["message"])
        self.assertIn("request_id", response)
        # Refused ahead of processing: no lead row, no PII touched, no delivery.
        self.assertIsNone(platform.store.get_lead("gapsec-idem-mismatch"))
        self.assertEqual(platform.store.deliveries_for_lead("gapsec-idem-mismatch"), [])

    def test_sec06_matched_header_key_accepted(self) -> None:
        platform = self._platform(require_auth=True)
        platform.upsert_offer(_offer("gapsec-idem", "buyer_001"))
        lead = _load_lead("gapsec-idem-matched", idempotency_key="gapsec-same-key")
        headers, body = _signed_headers(lead, PUBLISHER_SECRET)
        self.assertEqual(headers["X-LCP-Idempotency-Key"], "gapsec-same-key")
        status, _, response = PlatformService(platform).dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["lcp"]["payload"]["status"], "RECEIVED")
        self.assertIsNotNone(platform.store.get_lead("gapsec-idem-matched"))

    def test_sec06_hmac_verifier_also_refuses_divergent_keys(self) -> None:
        # Defense in depth past the HTTP gate: the §9 verifier refuses a header
        # key that diverges from the envelope key even when the signature is
        # canonically valid for the transmitted header value.
        platform = self._platform(require_auth=True)
        lead = _load_lead("gapsec-idem-verifier", idempotency_key="gapsec-envelope-key")
        headers, body = _signed_headers(
            lead, PUBLISHER_SECRET, header_key="gapsec-header-key"
        )
        with self.assertRaises(AuthenticationError) as caught:
            platform.ingest(lead, headers=headers, raw_body=body)
        self.assertEqual(caught.exception.code, "LCP-012")
        self.assertIn("differ", str(caught.exception))

    # ─── SEC-08: per-pair non-correlation of phone digests ──────────────────

    def test_sec08_two_pair_secrets_give_noncorrelated_paired_digests(self) -> None:
        platform = self._platform(require_auth=False)
        platform.upsert_credential("buyer_001", hmac_secret=VECTOR_PAIR_SECRET)
        platform.upsert_credential("buyer_002", hmac_secret=SECOND_PAIR_SECRET)
        platform.upsert_offer(_offer("gapsec-pair-a", "buyer_001"))
        platform.upsert_offer(_offer("gapsec-pair-b", "buyer_002"))

        lead = _load_lead("gapsec-pair-lead", idempotency_key="gapsec-pair-key")
        lead["lcp"]["payload"]["consumer"]["phone"] = VECTOR_PHONE
        platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=_compact(lead))
        platform.process_once()
        pings = {
            ping["buyer_id"]: platform.store.decode_envelope(ping["envelope_json"])[
                "lcp"
            ]["payload"]
            for ping in platform.store.list_pings("gapsec-pair-lead")
        }
        self.assertEqual(set(pings), {"buyer_001", "buyer_002"})
        hash_a = pings["buyer_001"]["phone_hash"]
        hash_b = pings["buyer_002"]["phone_hash"]

        # The pinned contact-hash.json phone row is reproduced byte-exactly
        # under its pair secret (the vector row's secret is buyer_001's).
        vector_row = {
            row["name"]: row
            for row in json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
        }["lcp-contact-hash-phone-001"]
        self.assertEqual(vector_row["preimage"], VECTOR_PHONE)
        self.assertEqual(vector_row["secret"], VECTOR_PAIR_SECRET)
        self.assertEqual(hash_a, vector_row["digest"])

        # Independent recomputation, not the platform's own helper:
        self.assertEqual(
            hash_a,
            hmac.new(
                VECTOR_PAIR_SECRET.encode(), VECTOR_PHONE.encode(), hashlib.sha256
            ).hexdigest(),
        )
        self.assertEqual(
            hash_b,
            hmac.new(
                SECOND_PAIR_SECRET.encode(), VECTOR_PHONE.encode(), hashlib.sha256
            ).hexdigest(),
        )

        # Per-pair non-correlation: different buyers see different hashes.
        self.assertNotEqual(hash_a, hash_b)
        # Rainbow-table resistance: neither digest is an unsalted SHA-256 (the
        # §9 named attack) or SHA-1 (a conformance failure anywhere in a
        # contact-hash path) of the phone.
        unsalted_sha256 = hashlib.sha256(VECTOR_PHONE.encode()).hexdigest()
        unsalted_sha1 = hashlib.sha1(VECTOR_PHONE.encode()).hexdigest()
        for digest in (hash_a, hash_b):
            self.assertNotEqual(digest, unsalted_sha256)
            self.assertNotEqual(digest, unsalted_sha1)
        # Buyer_002's digest is not buyer_001's digest re-keyed under either
        # secret (§9: "HMAC of a received digest under another pair's secret
        # is not a re-key") — the digests carry no cross-pair correlation.
        for secret in (VECTOR_PAIR_SECRET, SECOND_PAIR_SECRET):
            self.assertNotEqual(
                hash_b,
                hmac.new(
                    secret.encode(), bytes.fromhex(hash_a), hashlib.sha256
                ).hexdigest(),
            )

    # ─── SEC-14: the email-form emission gate holds (#153 T9 pending) ──────

    def _contacted_report(
        self,
        platform: Platform,
        *,
        buyer_id: str,
        lead_id: str,
        digests: list[str],
    ) -> dict[str, Any]:
        details: dict[str, object] = {
            "contacted_at": now_iso(),
            "via": "phone",
            "contacted_hashes": digests,
        }
        event = _envelope(
            "event",
            buyer_id,
            "platform_001",
            {
                "lead_id": lead_id,
                "event": "CONTACTED",
                "timestamp": now_iso(),
                "details": details,
            },
            test=True,
        )
        event["lcp"]["version"] = "1.1.0"
        return platform.submit_event(
            event, headers={"X-LCP-Test": "true"}, raw_body=_compact(event)
        )

    def _award_and_deliver(
        self, platform: Platform, lead_id: str, buyer_id: str
    ) -> None:
        ping = next(
            ping
            for ping in platform.store.list_pings(lead_id)
            if ping["buyer_id"] == buyer_id
        )
        ping_envelope = platform.store.decode_envelope(ping["envelope_json"])
        bid = _envelope(
            "bid",
            buyer_id,
            "platform_001",
            {
                "ping_id": ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": 1900,
                "currency": "AUD",
            },
            correlation_id=ping_envelope["lcp"]["message"]["id"],
            test=True,
        )
        platform.submit_bid(bid, headers={"X-LCP-Test": "true"}, raw_body=_compact(bid))
        platform.store.expire_ping(ping["ping_id"])
        platform._route_lead(lead_id)
        for post in platform.store.deliveries_for_lead(lead_id):
            if post["kind"] == "post" and post["buyer_id"] == buyer_id:
                platform.store.mark_delivery(
                    post["delivery_id"],
                    status="DELIVERED",
                    attempts=1,
                    next_attempt_at=now_iso(),
                )

    def _post_for(
        self, platform: Platform, lead_id: str, buyer_id: str
    ) -> dict[str, Any]:
        post = next(
            delivery
            for delivery in platform.store.deliveries_for_lead(lead_id)
            if delivery["kind"] == "post" and delivery["buyer_id"] == buyer_id
        )
        return platform.store.decode_envelope(post["envelope_json"])["lcp"]["payload"]

    def test_sec14_email_form_entries_are_never_emitted(self) -> None:
        # SPEC §9 (verbatim): "The reference platform hashes phone only
        # today." / "no email-form `contacted_hashes` entry ships until this
        # canonical is confirmed and the executed dedup-hash vectors (#153
        # T9, `lcp-sdk-dedup-hash-001..N`, run by all ten SDKs and the
        # reference platform) publish." Computing is not emitting: a buyer
        # may attest a correctly-keyed email-form digest, and the platform
        # must store it while forwarding only the phone-derived re-key (the
        # plaintext it actually holds).
        platform = self._platform(require_auth=False)
        platform.upsert_credential(
            "buyer_001", hmac_secret=VECTOR_PAIR_SECRET, lcp_versions=["1.1.0"]
        )
        platform.upsert_credential(
            "buyer_002", hmac_secret=SECOND_PAIR_SECRET, lcp_versions=["1.1.0"]
        )
        platform.upsert_offer(_offer("gapsec-gate-a", "buyer_001"))
        platform.upsert_offer(_offer("gapsec-gate-b", "buyer_002"))
        lead = _load_lead("gapsec-gate-lead", idempotency_key="gapsec-gate-key")
        lead["lcp"]["payload"]["consumer"]["phone"] = VECTOR_PHONE
        lead["lcp"]["payload"]["exclusivity"] = {
            "exclusivity": "shared",
            "max_buyers": 2,
        }
        platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=_compact(lead))
        platform.process_once()

        self._award_and_deliver(platform, "gapsec-gate-lead", "buyer_001")
        email = lead["lcp"]["payload"]["consumer"]["email"]
        email_digest_b1 = _pair_digest(VECTOR_PAIR_SECRET, email)
        pinned_phone_digest = {
            row["name"]: row["digest"]
            for row in json.loads(VECTORS.read_text(encoding="utf-8"))["vectors"]
        }["lcp-contact-hash-phone-001"]
        ack = self._contacted_report(
            platform,
            buyer_id="buyer_001",
            lead_id="gapsec-gate-lead",
            digests=[pinned_phone_digest, email_digest_b1],
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
        stored = {
            row["digest"]
            for row in platform.store.list_contacted_digests("gapsec-gate-lead")
        }
        self.assertEqual(
            stored,
            {pinned_phone_digest, email_digest_b1},
            "the email-form attestation must still be stored for its own pair",
        )

        self._award_and_deliver(platform, "gapsec-gate-lead", "buyer_002")
        payload = self._post_for(platform, "gapsec-gate-lead", "buyer_002")
        emitted = payload.get("contacted_hashes") or []
        self.assertEqual(
            emitted,
            [_pair_digest(SECOND_PAIR_SECRET, VECTOR_PHONE)],
            "only the phone-derived re-key may reach the wire",
        )
        self.assertNotIn(email_digest_b1, emitted)
        self.assertNotIn(
            _pair_digest(SECOND_PAIR_SECRET, email),
            emitted,
            "no email-form digest may be emitted while the §9 gate holds",
        )

    def test_sec14_email_only_reports_emit_no_contacted_entries(self) -> None:
        # The other half of the gate: when the only reports are email-form
        # digests, the platform holds no re-keyable plaintext at all, so the
        # v1.1 post carries no contacted_hashes (absent, never a leaked or
        # fabricated entry).
        platform = self._platform(require_auth=False)
        platform.upsert_credential(
            "buyer_001", hmac_secret=VECTOR_PAIR_SECRET, lcp_versions=["1.1.0"]
        )
        platform.upsert_credential(
            "buyer_002", hmac_secret=SECOND_PAIR_SECRET, lcp_versions=["1.1.0"]
        )
        platform.upsert_offer(_offer("gapsec-gate2-a", "buyer_001"))
        platform.upsert_offer(_offer("gapsec-gate2-b", "buyer_002"))
        lead = _load_lead("gapsec-gate2-lead", idempotency_key="gapsec-gate2-key")
        lead["lcp"]["payload"]["consumer"]["phone"] = VECTOR_PHONE
        lead["lcp"]["payload"]["exclusivity"] = {
            "exclusivity": "shared",
            "max_buyers": 2,
        }
        platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=_compact(lead))
        platform.process_once()
        self._award_and_deliver(platform, "gapsec-gate2-lead", "buyer_001")

        email = lead["lcp"]["payload"]["consumer"]["email"]
        ack = self._contacted_report(
            platform,
            buyer_id="buyer_001",
            lead_id="gapsec-gate2-lead",
            digests=[_pair_digest(VECTOR_PAIR_SECRET, email)],
        )
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        self._award_and_deliver(platform, "gapsec-gate2-lead", "buyer_002")
        payload = self._post_for(platform, "gapsec-gate2-lead", "buyer_002")
        self.assertNotIn(
            "contacted_hashes",
            payload,
            "an email-only report set re-keys to nothing the platform can attest",
        )

    # ─── SEC-12: agent response window (30s default) → EXPIRED / re-award ───

    def _ingest_and_ping(self, platform: Platform, lead_id: str) -> None:
        lead = _load_lead(lead_id, idempotency_key=f"gapsec-timeout-{lead_id}")
        platform.ingest(lead, headers={"X-LCP-Test": "true"}, raw_body=_compact(lead))
        platform.process_once()

    def test_sec12_default_agent_window_is_thirty_seconds(self) -> None:
        platform = self._platform(require_auth=False)
        platform.upsert_credential("buyer_001", hmac_secret="gapsec-buyer-secret")
        offer = _offer("gapsec-timeout-default", "buyer_001")
        offer.pop("ping_timeout_seconds")  # exercise the §9 default
        platform.upsert_offer(offer)
        self._ingest_and_ping(platform, "gapsec-timeout-default-lead")
        ping = platform.store.list_pings("gapsec-timeout-default-lead")[0]
        envelope = platform.store.decode_envelope(ping["envelope_json"])
        sent = datetime.strptime(
            envelope["lcp"]["message"]["timestamp"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        expires = datetime.strptime(
            envelope["lcp"]["payload"]["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        window = (expires - sent).total_seconds()
        # The window is exactly the §9 default of 30 seconds; the LEP-0002
        # canonical whole-second truncation of both wire timestamps makes the
        # observable delta 29-30 (never more than 30).
        self.assertLessEqual(window, 30.0)
        self.assertGreaterEqual(window, 29.0)

    def test_sec12_unanswered_window_auto_expires_the_lead(self) -> None:
        platform = self._platform(require_auth=False)
        platform.upsert_credential("buyer_001", hmac_secret="gapsec-buyer-secret")
        platform.upsert_offer(
            _offer("gapsec-timeout-silent", "buyer_001", ping_timeout_seconds=1)
        )
        self._ingest_and_ping(platform, "gapsec-timeout-silent-lead")
        self.assertEqual(
            platform.store.get_lead("gapsec-timeout-silent-lead")["status"], "PINGED"
        )
        time.sleep(1.2)  # past the second-granularity expiry boundary
        platform.process_once()
        self.assertEqual(
            platform.store.list_pings("gapsec-timeout-silent-lead")[0]["status"],
            "EXPIRED",
        )
        self.assertEqual(
            platform.store.get_lead("gapsec-timeout-silent-lead")["status"], "EXPIRED"
        )

    def test_sec12_timed_out_agent_does_not_block_the_responder(self) -> None:
        # Two buyers share the lead. buyer_001's agent never answers inside its
        # window; buyer_002 answers accept in time. The expiry sweep closes the
        # auction and the responsive buyer is awarded — the unresponsive
        # window does not strand the lead (§9 "may be re-offered to the next
        # buyer in the routing order").
        platform = self._platform(require_auth=False)
        platform.upsert_credential("buyer_001", hmac_secret="gapsec-buyer-secret-1")
        platform.upsert_credential("buyer_002", hmac_secret="gapsec-buyer-secret-2")
        platform.upsert_offer(
            _offer("gapsec-timeout-slow", "buyer_001", ping_timeout_seconds=1)
        )
        platform.upsert_offer(
            _offer("gapsec-timeout-fast", "buyer_002", ping_timeout_seconds=1)
        )
        self._ingest_and_ping(platform, "gapsec-timeout-two-lead")
        pings = {
            ping["buyer_id"]: ping
            for ping in platform.store.list_pings("gapsec-timeout-two-lead")
        }
        fast_ping = pings["buyer_002"]
        fast_envelope = platform.store.decode_envelope(fast_ping["envelope_json"])
        bid = _envelope(
            "bid",
            "buyer_002",
            "platform_001",
            {
                "ping_id": fast_ping["ping_id"],
                "decision": "accept",
                "bid_price_cents": 2000,
                "currency": "AUD",
            },
            correlation_id=fast_envelope["lcp"]["message"]["id"],
            test=True,
        )
        platform.submit_bid(bid, headers={"X-LCP-Test": "true"}, raw_body=_compact(bid))
        time.sleep(1.2)  # both windows close; buyer_001 never answered
        platform.process_once()
        statuses = {
            ping["buyer_id"]: ping["status"]
            for ping in platform.store.list_pings("gapsec-timeout-two-lead")
        }
        self.assertEqual(statuses["buyer_001"], "EXPIRED")
        posts = [
            delivery
            for delivery in platform.store.deliveries_for_lead(
                "gapsec-timeout-two-lead"
            )
            if delivery["kind"] == "post"
        ]
        self.assertEqual([post["buyer_id"] for post in posts], ["buyer_002"])
        self.assertEqual(
            platform.store.get_lead("gapsec-timeout-two-lead")["status"], "POSTED"
        )


if __name__ == "__main__":
    unittest.main()

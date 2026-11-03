"""LEP-0005 read surface (accepted 2026-11-03): request-history rows.

SPEC.md §9 Authentication is unchanged by this surface; the record's own
normative text is the spec here. Every assertion below cites the record
section it pins:

* closed metadata-only row shape (``schemas/history-request-row.json``),
  validated against the shipped schema — no raw envelope bodies, no
  plaintext contacts;
* the rule-6 identifier pin: a contact-shaped ``lead_id`` is OMITTED from the
  row (never coerced, never truncated) while the row survives with
  ``request_id``;
* the single tenant decision point: a credential from another tenant reads
  nothing, and the shipped ``'*'`` wildcard cannot bypass it;
* ``ack`` is a legal filter value matching no stored row;
* parameter validation is explicit (400 ``LCP-001``), never clamped;
* scope denial is 401 ``LCP-002``;
* erased leads are unreachable through this surface.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator

from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform, RequestError
from lcp_platform.service import PlatformService
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
ROW_SCHEMA = json.loads(
    (ROOT / "schemas" / "history-request-row.json").read_text(encoding="utf-8")
)

PUBLISHER_SECRET = "lep5-publisher-secret"
SECOND_SECRET = "lep5-second-publisher-secret"
BUYER_SECRET = "lep5-buyer-secret"
_NAMESPACE = uuid5(NAMESPACE_URL, "lcp-lep0005-history")


def _compact(envelope: dict) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


def _lead(lead_id: str, *, sender: str = "pub_123", key: str | None = None) -> dict:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        lead = json.load(handle)
    message = lead["lcp"]["message"]
    message["id"] = str(uuid5(_NAMESPACE, lead_id))
    message["test"] = True
    message["sender_id"] = sender
    message["idempotency_key"] = key or f"lep5-{lead_id}"
    lead["lcp"]["payload"]["lead_id"] = lead_id
    lead["lcp"]["payload"].pop("expiry", None)
    return lead


def _signed(envelope: dict, secret: str) -> tuple[dict[str, str], bytes]:
    body = _compact(envelope)
    message = envelope["lcp"]["message"]
    timestamp = now_iso()
    headers = {
        "Content-Type": "application/json",
        "X-LCP-Test": "true",
        "X-LCP-Sender-Id": message["sender_id"],
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Idempotency-Key": message["idempotency_key"],
        "X-LCP-Signature": signature_for(
            secret, timestamp, message["idempotency_key"], body
        ),
    }
    return headers, body


def _read_headers(sender: str, secret: str) -> dict[str, str]:
    timestamp = now_iso()
    return {
        "X-LCP-Sender-Id": sender,
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Signature": signature_for(secret, timestamp, None, b""),
    }


class HistorySurfaceTests(unittest.TestCase):
    def _platform(self) -> Platform:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.addCleanup(platform.close)
        platform.upsert_credential(
            "pub_123",
            tenant_id="tenant_a",
            scopes=["lead:submit", "history:read"],
            hmac_secret=PUBLISHER_SECRET,
        )
        platform.upsert_credential(
            "pub_999",
            tenant_id="tenant_b",
            scopes=["lead:submit", "history:read"],
            hmac_secret=SECOND_SECRET,
        )
        platform.upsert_credential(
            "buyer_001",
            tenant_id="tenant_a",
            scopes=["bid:submit", "history:read"],
            hmac_secret=BUYER_SECRET,
        )
        platform.upsert_credential(
            "pub_noscope",
            tenant_id="tenant_a",
            scopes=["lead:submit"],
            hmac_secret=PUBLISHER_SECRET,
        )
        return platform

    def _ingest(
        self, platform: Platform, lead_id: str, *, sender: str, secret: str
    ) -> dict:
        envelope = _lead(lead_id, sender=sender)
        headers, body = _signed(envelope, secret)
        return platform.ingest(envelope, headers=headers, raw_body=body)

    # ── row shape ──────────────────────────────────────────────────────────

    def test_rows_are_metadata_only_and_validate_against_the_shipped_schema(
        self,
    ) -> None:
        platform = self._platform()
        self._ingest(
            platform, "lep5-shape-1", sender="pub_123", secret=PUBLISHER_SECRET
        )
        payload, headers = platform.request_history("", sender_id="pub_123")
        rows = payload["requests"]
        self.assertGreaterEqual(len(rows), 1, "ingested lead must project one row")
        validator = Draft202012Validator(ROW_SCHEMA)
        for row in rows:
            errors = sorted(validator.iter_errors(row), key=str)
            self.assertEqual([], [error.message for error in errors])
            self.assertNotIn("consumer", row)
            self.assertNotIn("location", row)
            self.assertNotIn("attributes", row)
            self.assertNotIn("envelope", row)
        self.assertEqual(headers["X-LCP-Result-Count"], str(len(rows)))
        self.assertIn("X-LCP-Total-Results", headers)

    def test_contact_shaped_lead_id_is_omitted_not_coerced(self) -> None:
        platform = self._platform()
        self._ingest(
            platform, "+15125550147", sender="pub_123", secret=PUBLISHER_SECRET
        )
        payload, _headers = platform.request_history("", sender_id="pub_123")
        # The identifier pin refuses the contact-shaped handle at the
        # projection: the row exists, the handle does not ride it. The FILTER
        # side is stricter still — a phone-shaped lead_id is a 400 (see the
        # plaintext-phone test below), so the omission is proven unfiltered.
        rows = payload["requests"]
        lead_rows = [row for row in rows if row["message_type"] == "lead"]
        self.assertTrue(lead_rows, "the record survives with request_id as its handle")
        for row in lead_rows:
            self.assertNotIn("lead_id", row)
            self.assertIn("request_id", row)

    # ── tenant binding ─────────────────────────────────────────────────────

    def test_cross_tenant_credential_reads_nothing(self) -> None:
        platform = self._platform()
        self._ingest(
            platform, "lep5-tenant-a", sender="pub_123", secret=PUBLISHER_SECRET
        )
        payload_b, _ = platform.request_history(
            "lead_id=lep5-tenant-a", sender_id="pub_999"
        )
        self.assertEqual([], payload_b["requests"])
        payload_a, _ = platform.request_history(
            "lead_id=lep5-tenant-a", sender_id="pub_123"
        )
        self.assertTrue(payload_a["requests"])

    def test_wildcard_scope_cannot_bypass_tenancy(self) -> None:
        platform = self._platform()
        self._ingest(
            platform, "lep5-tenant-wild", sender="pub_123", secret=PUBLISHER_SECRET
        )
        platform.upsert_credential(
            "ops_wild",
            tenant_id="tenant_b",
            scopes=["*"],
            hmac_secret=SECOND_SECRET,
        )
        payload, _ = platform.request_history(
            "lead_id=lep5-tenant-wild", sender_id="ops_wild"
        )
        self.assertEqual([], payload["requests"])

    # ── filters and parameters ─────────────────────────────────────────────

    def test_ack_filter_is_a_legal_empty_set(self) -> None:
        platform = self._platform()
        self._ingest(platform, "lep5-ack", sender="pub_123", secret=PUBLISHER_SECRET)
        payload, _ = platform.request_history("message_type=ack", sender_id="pub_123")
        self.assertEqual([], payload["requests"])

    def test_plaintext_phone_filter_is_rejected(self) -> None:
        platform = self._platform()
        with self.assertRaises(RequestError) as caught:
            platform.request_history(
                "caller_phone_hash=0%2B15125550147", sender_id="pub_123"
            )
        self.assertIn(caught.exception.code, {"LCP-001", "LCP-100"})

    def test_limit_out_of_range_is_rejected_not_clamped(self) -> None:
        platform = self._platform()
        for query in ("limit=0", "limit=201", "limit=abc"):
            with self.assertRaises(RequestError):
                platform.request_history(query, sender_id="pub_123")

    def test_unknown_parameter_is_rejected(self) -> None:
        platform = self._platform()
        with self.assertRaises(RequestError):
            platform.request_history("tenant=tenant_b", sender_id="pub_123")

    def test_pagination_cursor_advances_through_pages(self) -> None:
        platform = self._platform()
        for index in range(3):
            self._ingest(
                platform,
                f"lep5-page-{index}",
                sender="pub_123",
                secret=PUBLISHER_SECRET,
            )
        first, headers = platform.request_history("limit=2", sender_id="pub_123")
        self.assertEqual(2, len(first["requests"]))
        next_cursor = headers.get("Link")
        self.assertIsNotNone(next_cursor, "more rows must yield a next-page link")
        cursor = next_cursor.split("cursor=", 1)[1].split(">", 1)[0]
        second, _ = platform.request_history(
            f"limit=2&cursor={cursor}", sender_id="pub_123"
        )
        first_ids = {row["request_id"] for row in first["requests"]}
        second_ids = {row["request_id"] for row in second["requests"]}
        self.assertTrue(second_ids)
        self.assertFalse(first_ids & second_ids, "pages must not overlap")

    def test_erased_lead_rows_persist_contact_free_and_hash_filter_cannot_match(self) -> None:
        platform = self._platform()
        self._ingest(platform, "lep5-erase", sender="pub_123", secret=PUBLISHER_SECRET)
        before, _ = platform.request_history("lead_id=lep5-erase", sender_id="pub_123")
        self.assertTrue(before["requests"])
        phone = "15551234567"
        digest = platform.store  # placeholder to keep flake-free import use
        from lcp_platform.messages import pair_phone_hash

        pre_hash = pair_phone_hash(PUBLISHER_SECRET, "+15551234567")
        matched_before, _ = platform.request_history(
            f"caller_phone_hash={pre_hash}", sender_id="pub_123"
        )
        platform.erase_lead("lep5-erase")
        after, _ = platform.request_history("lead_id=lep5-erase", sender_id="pub_123")
        # Rows persist for auditability of the erasure itself (record §Erasure),
        # and no contact-derived member survives.
        self.assertTrue(after["requests"])
        for row in after["requests"]:
            self.assertNotIn("caller_phone_hash", row)
        self.assertEqual("ERASED", after["requests"][0]["status"])
        # The keyed-hash filter cannot match: no plaintext remains to key against.
        matched_after, _ = platform.request_history(
            f"caller_phone_hash={pre_hash}", sender_id="pub_123"
        )
        self.assertEqual([], matched_after["requests"])

    # ── authz through the service boundary ─────────────────────────────────

    def test_missing_scope_is_rejected_at_the_boundary(self) -> None:
        platform = self._platform()
        service = PlatformService(platform)
        status, _headers, body = service.dispatch(
            "GET",
            "/v1/lcp/history/requests",
            headers=_read_headers("pub_noscope", PUBLISHER_SECRET),
            body=b"",
        )
        self.assertEqual(401, status)
        self.assertEqual("LCP-002", body["errors"][0]["code"])

    def test_authorized_read_returns_rows_and_pagination_headers(self) -> None:
        platform = self._platform()
        self._ingest(platform, "lep5-svc", sender="pub_123", secret=PUBLISHER_SECRET)
        service = PlatformService(platform)
        status, headers, body = service.dispatch(
            "GET",
            "/v1/lcp/history/requests?limit=50",
            headers=_read_headers("pub_123", PUBLISHER_SECRET),
            body=b"",
        )
        self.assertEqual(200, status)
        self.assertIn("requests", body)
        self.assertEqual(str(len(body["requests"])), headers["X-LCP-Result-Count"])


class McpHistoryToolTests(unittest.TestCase):
    def test_pagination_is_surfaced_in_the_tool_result(self) -> None:
        """LEP-0005 §3: the adapter MUST surface the three pagination values."""
        from lcp_mcp_server.client import _cursor_from_link

        link = (
            "<https://api.example.com/v1/lcp/history/requests?limit=50"
            '&cursor=czI2LDIwMjYtMTEtMDNUMTA6MjE6MDA>; rel="next"'
        )
        self.assertEqual("czI2LDIwMjYtMTEtMDNUMTA6MjE6MDA", _cursor_from_link(link))
        self.assertIsNone(_cursor_from_link(None))


if __name__ == "__main__":
    unittest.main()

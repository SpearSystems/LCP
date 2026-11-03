"""SPEC §5 intake error-codification closure tests (#176 / #181 / #182).

Rows claimed by this file (docs/SPEC-COVERAGE.md dispositions proposed in the
PR): ERR-007, ERR-009, ERR-010. Every assertion drives the platform's own
HTTP dispatch surface (``PlatformService.dispatch``) — the protocol wire
behaviour, not just the schema layer.

SPEC clause quotes (verbatim, with the section that carries them):

* §5 (error table): ``LCP-007 | SCHEMA_VERSION_UNSUPPORTED | 422``,
  ``LCP-009 | INVALID_PHONE | 400``, ``LCP-010 | INVALID_TERRITORY | 422``.
* §3 (consumer): ``| phone | string | E.164 format. |``
* §3 (location): "`country_code` (ISO 3166-1 alpha-2, REQUIRED),
  `state_region` (per-country validation registry), `postal_code`
  (per-country format), `city`, `timezone` (IANA)."
* §6: "Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes)."
* §8 (version-string membership, normative): "A peer supports protocol
  extensions of minor `m` when its advertised set contains an element with
  `major == 1` and `minor >= m`; senders MUST compare parsed versions,
  never string literals or prefixes."
* §13 (retro, resolving the envelope-version identity): "Core schema
  version in envelope … or document that protocol version = core schema
  version" — the published envelope field is ``lcp.version``
  ("Protocol semver (e.g. 1.0.0)", schemas/envelope.json), which is the
  surface LCP-007 names unsupported.

Ordering-safety pins included: the #131 structural gate (unknown
``message.type`` → LCP-006 ahead of everything), the published schema
pattern axes (non-string phone, lowercase ``country_code`` stay the
LCP-100 catch-all — §3/§9 mandate no intake-side country normalization;
messy sources normalize through the mapping ``upper`` transform,
test_new_flows.py), and valid-but-unrouted territory staying a 200 ACK.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from uuid import uuid4

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService


ROOT = Path(__file__).resolve().parents[3]


class GapIntakeErrorCodesTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.platform.close()

    def load_lead(self, *, sender: str = "pub_123") -> dict:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["message"]["id"] = str(uuid4())
        lead["lcp"]["message"]["sender_id"] = sender
        lead["lcp"]["message"]["idempotency_key"] = f"idem-{uuid4().hex}"
        lead["lcp"]["payload"]["lead_id"] = f"lead_{uuid4().hex[:12]}"
        lead["lcp"]["payload"].pop("expiry", None)
        return lead

    def post_lead(self, envelope: dict) -> tuple[int, dict, dict]:
        body = json.dumps(envelope, separators=(",", ":")).encode()
        return PlatformService(self.platform).dispatch(
            "POST",
            "/v1/lcp/leads",
            headers={
                "Content-Type": "application/json",
                "X-LCP-Idempotency-Key": envelope["lcp"]["message"]["idempotency_key"],
                "X-LCP-Test": "true",
            },
            body=body,
        )

    # ── ERR-009 (§5 LCP-009 INVALID_PHONE ↔ 400, issue #176) ───────────────

    def test_intake_stamps_lcp009_on_non_e164_consumer_phone(self) -> None:
        # SPEC §5: "LCP-009 | INVALID_PHONE | 400"; §3: "phone | string |
        # E.164 format." Issue #176 repro: "0412 345 678" must stamp LCP-009
        # with the field path, not degrade to the LCP-100 catch-all.
        lead = self.load_lead()
        consumer = lead["lcp"]["payload"]["consumer"]
        consumer.pop("email", None)
        consumer.pop("first_name", None)
        consumer.pop("last_name", None)
        consumer["full_name"] = "Jane Smith"
        consumer["phone"] = "0412 345 678"
        status, _, payload = self.post_lead(lead)
        self.assertEqual(status, 400)
        error = payload["errors"][0]
        self.assertEqual(error["code"], "LCP-009")
        self.assertIn("consumer/phone", json.dumps(error))

    def test_intake_stamps_lcp009_on_non_e164_secondary_phone(self) -> None:
        # The §5 phone axis covers both published consumer phone fields
        # (schemas/core.json: phone and secondary_phone carry the E.164
        # pattern); a violating secondary_phone must not slip past.
        lead = self.load_lead()
        lead["lcp"]["payload"]["consumer"]["secondary_phone"] = "412 345 678"
        status, _, payload = self.post_lead(lead)
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-009")
        self.assertIn("secondary_phone", json.dumps(payload["errors"][0]))

    def test_lcp009_keeps_structural_axes_and_lcp006_precedent(self) -> None:
        # Ordering safety: the #131 structural gate keeps LCP-006 priority
        # (unknown message.type ahead of every re-code), and a *non-string*
        # phone stays the schema LCP-100 type axis — the re-code targets the
        # E.164 value violation only, "every other schema violation keeps
        # LCP-100" (issue #131 documented ordering point).
        swapped = self.load_lead()
        swapped["lcp"]["message"]["type"] = "quantum"
        swapped["lcp"]["payload"]["consumer"]["phone"] = "0412 345 678"
        status, _, payload = self.post_lead(swapped)
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-006")

        typed = self.load_lead()
        typed["lcp"]["payload"]["consumer"]["phone"] = 412345678
        status, _, payload = self.post_lead(typed)
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-100")

        good = self.load_lead()
        status, _, payload = self.post_lead(good)
        self.assertEqual(status, 200, f"valid E.164 control must ACK: {payload}")

    # ── ERR-007 (§5 LCP-007 SCHEMA_VERSION_UNSUPPORTED ↔ 422, issue #181) ──

    def test_intake_stamps_lcp007_on_unsupported_envelope_version(self) -> None:
        # SPEC §5: "LCP-007 | SCHEMA_VERSION_UNSUPPORTED | 422"; §6:
        # "Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes)."
        # Issue #181 repro: a future MAJOR ("2.0.0") must be rejected, not
        # silently ACKed and processed as v1. A future MINOR ("1.2.0") and a
        # different MAJOR ("0.9.0") are outside the §8 parsed-membership set
        # this endpoint advertises and stamp the same code.
        for version in ("2.0.0", "1.2.0", "0.9.0"):
            lead = self.load_lead()
            lead["lcp"]["version"] = version
            status, _, payload = self.post_lead(lead)
            self.assertEqual(status, 422, f"{version} expected 422: {payload}")
            self.assertEqual(payload["errors"][0]["code"], "LCP-007")
            self.assertIn("lcp/version", json.dumps(payload["errors"][0]))

    def test_lcp007_malformed_version_stays_structural_lcp100(self) -> None:
        # Issue #181 names the boundary: a malformed version ("1.0") lands in
        # the LCP-100 catch-all (schema pattern axis), never LCP-007 — while
        # advertised minors and their PATCH upticks (§6 "PATCH fixes") ACK.
        malformed = self.load_lead()
        malformed["lcp"]["version"] = "1.0"
        status, _, payload = self.post_lead(malformed)
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-100")

        for version in ("1.0.0", "1.1.0", "1.0.9", "1.1.5"):
            lead = self.load_lead()
            lead["lcp"]["version"] = version
            status, _, payload = self.post_lead(lead)
            self.assertEqual(status, 200, f"{version} must ACK: {payload}")

    def test_lcp007_supported_set_is_the_advertised_lcp_versions(self) -> None:
        # Closed-set membership from one source: what §8
        # GET /v1/lcp/capabilities advertises as lcp_versions is exactly what
        # intake supports — every advertised element must ACK a lead.
        _, _, advertised = PlatformService(self.platform).dispatch(
            "GET", "/v1/lcp/capabilities", headers={}, body=b""
        )
        versions = advertised["lcp_versions"]
        self.assertEqual(versions, ["1.0.0", "1.1.0"])
        for version in versions:
            lead = self.load_lead()
            lead["lcp"]["version"] = version
            status, _, payload = self.post_lead(lead)
            self.assertEqual(status, 200, f"advertised {version} rejected: {payload}")

    # ── ERR-010 (§5 LCP-010 INVALID_TERRITORY ↔ 422, issue #182) ───────────

    def test_intake_stamps_lcp010_on_pattern_valid_unassigned_country_code(
        self,
    ) -> None:
        # SPEC §5: "LCP-010 | INVALID_TERRITORY | 422"; §3 location:
        # "country_code (ISO 3166-1 alpha-2, REQUIRED)". Issue #182 repro:
        # a pattern-valid ("^[A-Z]{2}$" schema axis passes) but ISO-UNASSIGNED
        # code must reject with LCP-010 and the field path — today it is
        # silently ACKed and matches no offers.
        for code in ("ZZ", "QZ"):
            lead = self.load_lead()
            lead["lcp"]["payload"]["location"]["country_code"] = code
            status, _, payload = self.post_lead(lead)
            self.assertEqual(status, 422, f"{code} expected 422: {payload}")
            self.assertEqual(payload["errors"][0]["code"], "LCP-010")
            self.assertIn("country_code", json.dumps(payload["errors"][0]))

    def test_lcp010_assigned_codes_route_lowercase_stays_lcp100(self) -> None:
        # Ruling (documented per §3): the published §3 location text carries
        # NO normalization instruction — intake validates the wire value
        # as-is, so lowercase "au" remains the schema pattern violation
        # (LCP-100 catch-all; §9's verbatim-wire ethos; messy sources
        # normalize through the mapping "upper" transform, see
        # test_new_flows.py). Assigned ISO elements must keep flowing:
        # even with no matching offer a valid territory is a 200 ACK with
        # silent non-routing, never a 422.
        lowercase = self.load_lead()
        lowercase["lcp"]["payload"]["location"]["country_code"] = "au"
        status, _, payload = self.post_lead(lowercase)
        self.assertEqual(status, 400)
        self.assertEqual(payload["errors"][0]["code"], "LCP-100")

        unrouted = self.load_lead()
        unrouted["lcp"]["payload"]["location"]["country_code"] = "TV"
        status, _, payload = self.post_lead(unrouted)
        self.assertEqual(status, 200, f"assigned code rejected: {payload}")


if __name__ == "__main__":
    unittest.main()

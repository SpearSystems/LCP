"""LEP-0003 SDK helpers: contact-hash vectors and the v1.1 slot round-trip."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lcp_sdk.capability import receiver_advertises_slot_fields, slot_capability_errors
from lcp_sdk.signing import contact_hash
from lcp_sdk.validation import SchemaValidator

ROOT = Path(__file__).resolve().parents[4]
VECTORS = ROOT / "test-vectors" / "sdk" / "contact-hash.json"


def _example(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


class ContactHashVectorTests(unittest.TestCase):
    """Every pinned preimage→digest row must reproduce byte-exactly (#153 T9 run)."""

    def test_every_pinned_row_reproduces(self) -> None:
        suite = json.loads(VECTORS.read_text(encoding="utf-8"))
        failures: list[str] = []
        for vector in suite["vectors"]:
            observed = contact_hash(
                vector["secret"], vector["preimage"], domain=vector["domain"]
            )
            if observed != vector["digest"]:
                failures.append(
                    f"{vector['name']} ({vector['domain']}): preimage {vector['preimage']!r} "
                    f"pinned {vector['digest']} but Python SDK produced {observed}"
                )
        self.assertEqual(
            failures,
            [],
            "contact-hash vector reproduction failed:\n" + "\n".join(failures),
        )

    def test_phone_preimage_is_verbatim_bytes(self) -> None:
        # SPEC.md §9: phone is hashed exactly as delivered — formatting noise is preserved,
        # so a different wire string is a different digest (this is the pinned rule).
        digest = contact_hash(
            "pair-secret-for-vectors-0123456789", "+1 (512) 555-0147", domain="phone"
        )
        self.assertNotEqual(
            digest, "c47aff331606437528b34b7f919eb37704e87ee35eed80e52cfa36826a4a2e12"
        )

    def test_email_canonical_trims_and_lowercases_domain_only(self) -> None:
        pinned = "d28da9b5424990cf7ac6000cf4e04537ca487929e71cef12f91191f29f78a6c4"
        self.assertEqual(
            contact_hash(
                "pair-secret-for-vectors-0123456789",
                "  Jordan@Example.COM  ",
                domain="email",
            ),
            pinned,
        )
        # Local-part bytes stay verbatim and plus-addresses are distinct (§9 canonical).
        self.assertNotEqual(
            contact_hash(
                "pair-secret-for-vectors-0123456789",
                "jordan@example.com",
                domain="email",
            ),
            pinned,
        )
        self.assertNotEqual(
            contact_hash(
                "pair-secret-for-vectors-0123456789",
                "Jordan+crm@example.com",
                domain="email",
            ),
            pinned,
        )


class SlotCapabilityGateTests(unittest.TestCase):
    def test_membership_rule_parses_full_semver(self) -> None:
        self.assertFalse(receiver_advertises_slot_fields(["1.0.0"]))
        self.assertTrue(receiver_advertises_slot_fields(["1.1.0"]))
        self.assertTrue(receiver_advertises_slot_fields(["1.0.0", "1.1.0"]))
        self.assertTrue(
            receiver_advertises_slot_fields(["1.10.0"])
        )  # parsed minor 10 >= 1
        self.assertFalse(
            receiver_advertises_slot_fields(["1.20.0"])
            and receiver_advertises_slot_fields(["2.0.0"])
        )
        self.assertFalse(receiver_advertises_slot_fields(["2.0.0"]))  # major must be 1
        self.assertFalse(
            receiver_advertises_slot_fields(["1.1"])
        )  # literal never opens the gate
        self.assertFalse(
            receiver_advertises_slot_fields(["v1.1.0", "1.1.0.1", ""])
        )  # malformed never opens it

    def test_slot_bearing_ping_to_non_advertising_receiver_is_rejected(self) -> None:
        ping = _example("ping-slots.json")
        errors = slot_capability_errors(ping, ["1.0.0"])
        self.assertTrue(
            errors, "slot-bearing ping must be rejected for a v1.0-only receiver"
        )
        self.assertIn("slots_available", errors[0])
        self.assertEqual(slot_capability_errors(ping, ["1.1.0"]), [])

    def test_validated_envelope_rejects_slot_fields_for_v1_0_only_receiver(
        self,
    ) -> None:
        validator = SchemaValidator(ROOT / "schemas")
        ping = _example("ping-slots.json")
        self.assertEqual(validator.validate_envelope(ping), [])
        errors = validator.validate_envelope(
            ping, receiver_advertised_lcp_versions=["1.0.0"]
        )
        self.assertTrue(
            errors,
            "SDK validator must reject slot fields toward a non-advertising receiver",
        )
        self.assertEqual(
            validator.validate_envelope(
                ping, receiver_advertised_lcp_versions=["1.1.0"]
            ),
            [],
        )


class SlotRoundTripTests(unittest.TestCase):
    """Slot-bearing ping/bid/post examples validate through the SDK schema path
    (webhook/post acceptance), and the v1.0-only gate rejects them (#153-adjacent)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = SchemaValidator(ROOT / "schemas")

    def test_slot_bearing_messages_validate_against_v1_1_schemas(self) -> None:
        for name in (
            "ping-slots.json",
            "bid-slots.json",
            "post-contacted.json",
            "event-contacted.json",
        ):
            with self.subTest(example=name):
                self.assertEqual(self.validator.validate_envelope(_example(name)), [])

    def test_absent_contacted_hashes_and_empty_list_distinction_holds(self) -> None:
        post = _example("post-contacted.json")
        payload = post["lcp"]["payload"]
        self.assertEqual(self.validator.validate_envelope(post), [])
        del payload["contacted_hashes"]
        self.assertEqual(
            self.validator.validate_envelope(post),
            [],
            "absent must stay legal (v1.0 behavior)",
        )
        payload["contacted_hashes"] = []
        self.assertEqual(
            self.validator.validate_envelope(post),
            [],
            "[] = platform attests no prior contacts",
        )

    def test_empty_list_still_gates_on_receiver_advertisement(self) -> None:
        post = _example("post-contacted.json")
        post["lcp"]["payload"]["contacted_hashes"] = []
        errors = self.validator.validate_envelope(
            post, receiver_advertised_lcp_versions=["1.0.0"]
        )
        self.assertTrue(
            errors,
            "a [] attestation is still a v1.1 field a v1.0 receiver cannot parse",
        )


if __name__ == "__main__":
    unittest.main()

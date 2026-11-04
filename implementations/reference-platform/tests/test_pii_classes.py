"""D9 / LEP-0011: ``x-lcp-pii`` load-time refusals and intake rules.

`docs/PII-CLASSES.md` is the normative taxonomy. These tests pin the four
refusals the reference platform owes it: an unannotated property, an unknown
class, a downgrade against the core, and a ``prohibited`` class; plus the
intake refusal of a value in a field classed ``prohibited``.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from typing import Any

from lcp_platform.validation import (
    PII_CLASSES,
    SchemaValidator,
    ValidationError,
    core_pii_classes,
    pii_load_errors,
    prohibited_value_errors,
)

ROOT = Path(__file__).resolve().parents[3]
CORE: dict[str, Any] = json.loads(
    (ROOT / "schemas" / "core.json").read_text(encoding="utf-8")
)
CORE_DOCUMENTS: list[tuple[str, str, dict[str, Any]]] = [
    ("core", "schemas/core.json", CORE)
]


def _errors(
    kind: str,
    source: str,
    document: dict[str, Any],
    core: dict[str, Any] = CORE,
) -> list[str]:
    return pii_load_errors(
        [("core", "schemas/core.json", core), (kind, source, document)]
    )


class PiiLoadRuleTests(unittest.TestCase):
    def test_the_shipped_schema_set_loads(self) -> None:
        # Every schema property and vertical attribute is classed, so the
        # validator constructs; a missing annotation would raise here.
        validator = SchemaValidator(ROOT / "schemas")
        self.assertEqual(
            validator._core["$defs"]["consumer"]["properties"]["phone"]["x-lcp-pii"],
            "contact",
        )

    def test_unannotated_property_is_refused(self) -> None:
        errors = _errors(
            "message",
            "schemas/lead.json",
            {"properties": {"phone": {"type": "string"}}},
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("phone has no x-lcp-pii class", errors[0])

    def test_unknown_class_is_refused(self) -> None:
        errors = _errors(
            "message",
            "schemas/lead.json",
            {"properties": {"phone": {"type": "string", "x-lcp-pii": "secret"}}},
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown x-lcp-pii class 'secret'", errors[0])

    def test_downgrade_names_the_field_and_both_classes(self) -> None:
        # docs/PII-CLASSES.md "Worked refusals": the vertical cannot opt a
        # phone number out of `contact`.
        errors = _errors(
            "vertical",
            "verticals/mortgage.json",
            {
                "properties": {
                    "consumer": {
                        "type": "object",
                        "x-lcp-pii": "restricted",
                        "properties": {
                            "phone": {"type": "string", "x-lcp-pii": "internal"}
                        },
                    }
                }
            },
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("consumer.phone", errors[0])
        self.assertIn("core requires contact", errors[0])
        self.assertIn("declares internal", errors[0])
        self.assertIn("verticals/mortgage.json", errors[0])
        self.assertIn("LCP-100", errors[0])

    def test_upgrade_is_accepted(self) -> None:
        errors = _errors(
            "vertical",
            "verticals/mortgage.json",
            {
                "properties": {
                    "consumer": {
                        "type": "object",
                        "x-lcp-pii": "restricted",
                        "properties": {
                            "locale": {"type": "string", "x-lcp-pii": "contact"}
                        },
                    }
                }
            },
        )
        self.assertEqual(errors, [])

    def test_prohibited_property_is_refused(self) -> None:
        errors = _errors(
            "extension",
            "acme.intake.raw_form",
            {"properties": {"ssn": {"type": "string", "x-lcp-pii": "prohibited"}}},
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("may not be added", errors[0])

    def test_computed_is_legal_only_on_envelope_payload(self) -> None:
        payload_document = {
            "properties": {
                "lcp": {
                    "type": "object",
                    "x-lcp-pii": "restricted",
                    "properties": {"payload": {"x-lcp-pii": "computed"}},
                }
            }
        }
        self.assertEqual(
            pii_load_errors(
                [
                    ("core", "schemas/core.json", CORE),
                    ("envelope", "schemas/envelope.json", payload_document),
                ]
            ),
            [],
        )
        errors = _errors("message", "schemas/lead.json", payload_document)
        self.assertEqual(len(errors), 1)
        self.assertIn("legal only on envelope.json's payload", errors[0])

    def test_ping_safe_true_cannot_buy_a_downgrade(self) -> None:
        errors = _errors(
            "vertical",
            "verticals/insurance.json",
            {
                "properties": {
                    "health_condition_band": {
                        "type": "string",
                        "x-lcp-pii": "restricted",
                        "ping_safe": True,
                    }
                }
            },
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("ping_safe: true but classed restricted", errors[0])

    def test_a_downgraded_vertical_refuses_to_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "schemas", root / "schemas")
            shutil.copytree(ROOT / "verticals", root / "verticals")
            mortgage = root / "verticals" / "mortgage.json"
            document = json.loads(mortgage.read_text(encoding="utf-8"))
            document["properties"]["consumer"] = {
                "type": "object",
                "x-lcp-pii": "restricted",
                "properties": {"phone": {"type": "string", "x-lcp-pii": "internal"}},
            }
            mortgage.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValidationError) as raised:
                SchemaValidator(root / "schemas")
            self.assertIn("consumer.phone", str(raised.exception))
            self.assertIn("core requires contact", str(raised.exception))


class PiiIntakeRuleTests(unittest.TestCase):
    def test_prohibited_value_is_refused_naming_the_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {"ssn": {"type": "string", "x-lcp-pii": "prohibited"}},
        }
        errors = prohibited_value_errors({"ssn": "123-45-6789"}, schema, CORE)
        self.assertEqual(len(errors), 1)
        self.assertIn("payload.ssn", errors[0])
        self.assertIn("prohibited", errors[0])

    def test_absent_prohibited_value_is_not_an_error(self) -> None:
        schema = {
            "type": "object",
            "properties": {"ssn": {"type": "string", "x-lcp-pii": "prohibited"}},
        }
        self.assertEqual(prohibited_value_errors({"other": "x"}, schema, CORE), [])

    def test_a_shared_definition_carries_its_class_through_a_ref(self) -> None:
        core = {
            "$defs": {
                "probe": {
                    "type": "object",
                    "x-lcp-pii": "restricted",
                    "properties": {
                        "ssn": {"type": "string", "x-lcp-pii": "prohibited"}
                    },
                }
            }
        }
        schema = {
            "type": "object",
            "properties": {"probe": {"$ref": "core.json#/$defs/probe"}},
        }
        errors = prohibited_value_errors(
            {"probe": {"ssn": "123-45-6789"}}, schema, core
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("payload.probe.ssn", errors[0])

    def test_no_core_property_is_classed_prohibited(self) -> None:
        # docs/PII-CLASSES.md "Prohibited values": the class exists for the
        # coverage check, not for a core field.
        classes = core_pii_classes(CORE)
        self.assertTrue(classes)
        self.assertNotIn("prohibited", set(classes.values()))
        self.assertLessEqual(set(classes.values()), set(PII_CLASSES))


if __name__ == "__main__":
    unittest.main()

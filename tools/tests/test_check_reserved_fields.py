"""Reserved-core-field checker tests: deterministic, filesystem-only.

Covers the derived-from-core reserved set (drift both directions), the two
SPEC-mandated exemptions (attributes envelope keys at root, nested
country-scoped ``country_code``), recursion into nested/combinator/array
paths, fail-loud on malformed inputs, and the pre-hash/pre-write
integrations into ``check_sdk_schema_sync`` / ``generate_sdk_models``
(issue #134).
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1]))

import check_reserved_fields  # noqa: E402
import check_sdk_schema_sync  # noqa: E402
import generate_sdk_models  # noqa: E402
from check_reserved_fields import find_violations  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def vertical_doc(props: dict[str, Any], *, envelope: bool = True) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    if envelope:
        properties["vertical"] = {"type": "string", "const": "test"}
        properties["schema_version"] = {"type": "string", "const": "1.0.0"}
    properties.update(props)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://lcp.dev/verticals/test.json",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


class Sandbox(unittest.TestCase):
    """tmp repo seeded with the real core.json + spec/ so prose trips hold."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "schemas").mkdir()
        (self.tmp / "verticals").mkdir()
        shutil.copyfile(
            REPO / "schemas" / "core.json", self.tmp / "schemas" / "core.json"
        )
        shutil.copytree(REPO / "spec", self.tmp / "spec")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_vertical(self, name: str, doc: object) -> None:
        path = self.tmp / "verticals" / name
        if isinstance(doc, str):
            path.write_text(doc, encoding="utf-8")
        else:
            path.write_text(json.dumps(doc), encoding="utf-8")

    def patch_core(self, mutate) -> None:
        core = json.loads((self.tmp / "schemas" / "core.json").read_text())
        mutate(core)
        (self.tmp / "schemas" / "core.json").write_text(
            json.dumps(core), encoding="utf-8"
        )

    def find(self) -> list[str]:
        return find_violations(
            self.tmp / "schemas", self.tmp / "verticals", self.tmp / "spec"
        )


class TestCleanPass(Sandbox):
    def test_real_repo_verticals_have_no_false_positives(self) -> None:
        # Includes the legitimate nested mortgage_product.country_code and
        # the root vertical/schema_version envelope keys.
        self.assertEqual(
            find_violations(REPO / "schemas", REPO / "verticals", REPO / "spec"), []
        )

    def test_envelope_keys_and_nested_country_code_are_exempt(self) -> None:
        self.write_vertical(
            "test.json",
            vertical_doc(
                {
                    "product": {
                        "type": "object",
                        "properties": {
                            "country_code": {"type": "string"},
                            "tier": {"type": "string"},
                        },
                        "required": ["country_code"],
                    }
                }
            ),
        )
        self.assertEqual(self.find(), [])

    def test_envelope_keys_nested_are_flagged(self) -> None:
        self.write_vertical(
            "test.json",
            vertical_doc(
                {
                    "grp": {
                        "type": "object",
                        "properties": {"vertical": {"type": "string"}},
                    }
                }
            ),
        )
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("attributes.grp.vertical", errors[0])


class TestPlantedViolations(Sandbox):
    def test_top_level_phone(self) -> None:
        self.write_vertical("test.json", vertical_doc({"phone": {"type": "string"}}))
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("attributes.phone", errors[0])
        self.assertIn("redefines reserved core field 'phone'", errors[0])
        self.assertIn("SPEC §3", errors[0])

    def test_phone_inside_country_scoped_object(self) -> None:
        self.write_vertical(
            "test.json",
            vertical_doc(
                {
                    "mortgage_product": {
                        "type": "object",
                        "properties": {
                            "country_code": {"type": "string"},
                            "phone": {"type": "string"},
                            "us_product": {"type": "string"},
                        },
                        "required": ["country_code"],
                    }
                }
            ),
        )
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("attributes.mortgage_product.phone", errors[0])
        self.assertNotIn("country_code ", errors[0])

    def test_country_code_at_root_is_flagged(self) -> None:
        self.write_vertical(
            "test.json",
            vertical_doc({"country_code": {"type": "string"}}, envelope=False),
        )
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("attributes.country_code", errors[0])

    def test_recursion_through_combinators_and_arrays(self) -> None:
        doc = vertical_doc(
            {
                "wrapped": {
                    "type": "array",
                    "items": {"properties": {"email": {"type": "string"}}},
                },
                "either": {
                    "anyOf": [
                        {"properties": {"last_name": {"type": "string"}}},
                        {"type": "null"},
                    ]
                },
                "mapped": {
                    "type": "object",
                    "additionalProperties": {"properties": {"dob": {"type": "string"}}},
                },
            }
        )
        self.write_vertical("test.json", doc)
        joined = "\n".join(self.find())
        for path in (
            "attributes.wrapped.email",
            "attributes.either.last_name",
            "attributes.mapped.dob",
        ):
            self.assertIn(path, joined)

    def test_multiple_verticals_reported(self) -> None:
        self.write_vertical("a.json", vertical_doc({"phone": {"type": "string"}}))
        self.write_vertical("b.json", vertical_doc({"email": {"type": "string"}}))
        errors = self.find()
        self.assertEqual(len(errors), 2)
        self.assertIn("a.json", errors[0])
        self.assertIn("b.json", errors[1])


class TestDriftDetection(Sandbox):
    def test_new_core_field_is_automatically_reserved(self) -> None:
        def add(core: dict) -> None:
            core["$defs"]["consumer"]["properties"]["ssn"] = {"type": "string"}

        self.patch_core(add)
        self.write_vertical("test.json", vertical_doc({"ssn": {"type": "string"}}))
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("'ssn'", errors[0])

    def test_core_field_renamed_vs_spec_prose_fails(self) -> None:
        def drop_phone(core: dict) -> None:
            del core["$defs"]["consumer"]["properties"]["phone"]

        self.patch_core(drop_phone)
        self.write_vertical(
            "test.json", vertical_doc({"loan_type": {"type": "string"}})
        )
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("'phone'", errors[0])
        self.assertIn("drift", errors[0])


class TestFailLoud(Sandbox):
    def test_malformed_vertical_json(self) -> None:
        self.write_vertical("broken.json", "{not json")
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("invalid JSON", errors[0])

    def test_empty_verticals_dir_refuses(self) -> None:
        self.assertEqual(
            self.find(),
            [
                f"{self.tmp / 'verticals'}: no vertical schemas found — refusing to "
                "certify an empty verticals directory"
            ],
        )

    def test_missing_core_json_refuses(self) -> None:
        self.write_vertical("test.json", vertical_doc({}))
        (self.tmp / "schemas" / "core.json").unlink()
        errors = self.find()
        self.assertEqual(len(errors), 1)
        self.assertIn("core schema is the reserved-name source", errors[0])


class TestCli(Sandbox):
    def test_cli_exit_codes(self) -> None:
        self.write_vertical(
            "test.json", vertical_doc({"full_name": {"type": "string"}})
        )
        argv = [
            "check_reserved_fields.py",
            "--schemas-dir",
            str(self.tmp / "schemas"),
            "--verticals-dir",
            str(self.tmp / "verticals"),
            "--spec",
            str(self.tmp / "spec"),
        ]
        old = sys.argv
        sys.argv = argv
        try:
            self.assertEqual(check_reserved_fields.main(), 1)
        finally:
            sys.argv = old
        self.write_vertical(
            "test.json", vertical_doc({"loan_type": {"type": "string"}})
        )
        sys.argv = argv
        try:
            self.assertEqual(check_reserved_fields.main(), 0)
        finally:
            sys.argv = old


class TestChokePointIntegration(Sandbox):
    """Sync must fail pre-hash and generate must refuse pre-write (issue #134)."""

    def _seed_planted(self) -> None:
        self.write_vertical(
            "mortgage.json",
            vertical_doc(
                {"phone": {"type": "string", "ping_safe": True}}, envelope=False
            ),
        )

    def test_sync_check_fails_before_hashing(self) -> None:
        self._seed_planted()
        old_root, old_dir = check_sdk_schema_sync.ROOT, check_sdk_schema_sync.SDK_DIR
        old_argv = sys.argv
        check_sdk_schema_sync.ROOT = self.tmp
        check_sdk_schema_sync.SDK_DIR = self.tmp / "nonexistent"
        sys.argv = ["check_sdk_schema_sync.py", "--check"]
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = check_sdk_schema_sync.main()
        finally:
            sys.argv = old_argv
            check_sdk_schema_sync.ROOT = old_root
            check_sdk_schema_sync.SDK_DIR = old_dir
        self.assertEqual(code, 1)
        self.assertIn("reserved core field 'phone'", buf.getvalue())
        # failed pre-hash: never mentions manifest staleness
        self.assertNotIn("schema-manifest.json", buf.getvalue())

    def test_generate_write_refuses_without_writing(self) -> None:
        self._seed_planted()
        old_root, old_dir = generate_sdk_models.ROOT, generate_sdk_models.SDK_DIR
        generate_sdk_models.ROOT = self.tmp
        generate_sdk_models.SDK_DIR = self.tmp / "implementations" / "sdk"
        try:
            with self.assertRaises(SystemExit) as caught:
                generate_sdk_models.write_outputs()
        finally:
            generate_sdk_models.ROOT = old_root
            generate_sdk_models.SDK_DIR = old_dir
        self.assertEqual(caught.exception.code, 1)
        self.assertFalse((self.tmp / "implementations").exists())


if __name__ == "__main__":
    unittest.main()

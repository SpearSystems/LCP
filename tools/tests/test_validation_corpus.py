"""The shared SDK validation corpus must hold for the reference Python validator.

Every SDK validator is expected to agree with the pass/fail outcomes in
``test-vectors/sdk/validation-corpus.json``. This test pins the corpus to the
Python reference implementation and keeps the corpus itself self-consistent.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "implementations" / "sdk" / "python"))

from lcp_sdk.validation import SchemaValidator  # noqa: E402

CORPUS = ROOT / "test-vectors" / "sdk" / "validation-corpus.json"


class ValidationCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = SchemaValidator(ROOT / "schemas")
        with CORPUS.open(encoding="utf-8") as handle:
            cls.fixtures = json.load(handle)["fixtures"]

    def test_corpus_is_well_formed(self) -> None:
        ids: set[str] = set()
        for fixture in self.fixtures:
            self.assertIn("id", fixture)
            self.assertIn("rule", fixture)
            self.assertIn("expect", fixture)
            self.assertNotIn(fixture["id"], ids, "duplicate fixture id")
            ids.add(fixture["id"])
            self.assertTrue(
                ("envelope" in fixture) != ("offer" in fixture),
                f"{fixture['id']}: fixture must have exactly one of envelope|offer",
            )
            self.assertIn(fixture["expect"], {"pass", "fail"})

    def test_corpus_outcomes_match_python_validator(self) -> None:
        mismatches: list[str] = []
        for fixture in self.fixtures:
            try:
                if "envelope" in fixture:
                    errors = self.validator.validate_envelope(fixture["envelope"])
                else:
                    errors = self.validator.validate_offer(fixture["offer"])
                passed = not errors
            except Exception as exc:  # noqa: BLE001 - a crash is a rejection, but it must be reported
                mismatches.append(
                    f"{fixture['id']} ({fixture['rule']}): validator crashed "
                    f"({type(exc).__name__}: {exc}) instead of returning a structured result"
                )
                continue
            expected_pass = fixture["expect"] == "pass"
            if passed != expected_pass:
                mismatches.append(
                    f"{fixture['id']} ({fixture['rule']}): expected "
                    f"{fixture['expect']}, got errors={errors[:3]}"
                )
        self.assertEqual([], mismatches)

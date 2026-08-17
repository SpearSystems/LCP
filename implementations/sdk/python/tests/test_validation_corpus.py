"""Python SDK must agree with the shared cross-language validation corpus."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lcp_sdk.validation import SchemaValidator

ROOT = Path(__file__).resolve().parents[4]
CORPUS = ROOT / "test-vectors" / "sdk" / "validation-corpus.json"


class ValidationCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = SchemaValidator(ROOT / "schemas")
        with CORPUS.open(encoding="utf-8") as handle:
            cls.fixtures = json.load(handle)["fixtures"]

    def test_shared_validation_corpus_outcomes(self) -> None:
        mismatches: list[str] = []
        for fixture in self.fixtures:
            try:
                if "envelope" in fixture:
                    errors = self.validator.validate_envelope(fixture["envelope"])
                else:
                    errors = self.validator.validate_offer(fixture["offer"])
                passed = not errors
            except Exception as exc:  # noqa: BLE001
                mismatches.append(f"{fixture['id']}: validator crashed ({exc})")
                continue
            if passed != (fixture["expect"] == "pass"):
                mismatches.append(
                    f"{fixture['id']} ({fixture['rule']}): expected "
                    f"{fixture['expect']}, got errors={errors[:3]}"
                )
        self.assertEqual([], mismatches)

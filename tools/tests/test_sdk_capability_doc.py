"""The SDK capability report must stay honest about which SDKs exist."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "SDK-VALIDATION-CAPABILITIES.md"
# Display names exactly as written in docs/SDK-VALIDATION-CAPABILITIES.md.
SDK_NAMES = ("Python", "TypeScript", "Go", "C#", "Java", "PHP", "Rust", "Ruby", "Kotlin", "Swift")


class SdkCapabilityDocTests(unittest.TestCase):
    def test_document_exists_and_lists_every_sdk(self) -> None:
        self.assertTrue(DOC.exists(), "docs/SDK-VALIDATION-CAPABILITIES.md is missing")
        text = DOC.read_text(encoding="utf-8")
        for name in SDK_NAMES:
            self.assertIn(name, text, f"capability document does not mention SDK '{name}'")

    def test_document_references_the_shared_corpus(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("test-vectors/sdk/validation-corpus.json", text)
        corpus = ROOT / "test-vectors" / "sdk" / "validation-corpus.json"
        self.assertTrue(corpus.exists(), "shared validation corpus is missing")

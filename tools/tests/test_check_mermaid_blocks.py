import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_mermaid_blocks as lint  # noqa: E402


class MermaidLintTests(unittest.TestCase):
    def _check(self, body: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "page.md").write_text(body, encoding="utf-8")
            return lint.check_root(root)

    def test_clean_sequence_diagram_passes(self):
        self.assertEqual(
            [],
            self._check(
                "```mermaid\nsequenceDiagram\n"
                "    participant A\n    participant B\n"
                "    A->>B: hello\n"
                "    alt ok\n        B-->>A: hi\n    else no\n"
                "        B-->>A: no\n    end\n"
                "    Note over A,B: both\n```\n"
            ),
        )

    def test_semicolon_is_reported(self):
        problems = self._check(
            "```mermaid\nsequenceDiagram\n    participant A\n"
            "    participant B\n    A->>B: one; two\n```\n"
        )
        self.assertTrue(any("statement separator" in p for p in problems), problems)

    def test_literal_backslash_n_is_reported(self):
        problems = self._check(
            "```mermaid\nsequenceDiagram\n    participant A\n"
            "    participant B\n    A->>B: one\\ntwo\n```\n"
        )
        self.assertTrue(any("<br/>" in p for p in problems), problems)

    def test_unclosed_alt_is_reported(self):
        problems = self._check(
            "```mermaid\nsequenceDiagram\n    participant A\n    participant B\n"
            "    alt ok\n        A->>B: hi\n```\n"
        )
        self.assertTrue(any("never closed" in p for p in problems), problems)

    def test_undeclared_note_participant_is_reported(self):
        problems = self._check(
            "```mermaid\nsequenceDiagram\n    participant A\n    A->>A: hi\n"
            "    Note over A,B: both\n```\n"
        )
        self.assertTrue(any("undeclared participant" in p for p in problems), problems)

    def test_unbalanced_quote_is_reported(self):
        problems = self._check('```mermaid\nflowchart LR\n    A["label]\n```\n')
        self.assertTrue(any("unbalanced double quote" in p for p in problems), problems)

    def test_repository_markdown_is_clean(self):
        self.assertEqual([], lint.check_root(ROOT))


if __name__ == "__main__":
    unittest.main()

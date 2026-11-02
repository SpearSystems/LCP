"""SBOM-exception expiry checker tests: deterministic, filesystem-only.

Proves the blocking gate (JSON entries past re_review_by exit 1) and the
advisory markdown-register triage, including fail-loud on malformed or
missing registers.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from check_exception_expiry import main  # noqa: E402

TODAY = "2026-11-02"
MD_TEMPLATE = (
    "# Register\n\n"
    "## Current findings\n\n"
    "| Finding | Package | Fixed | Severity | Disposition | Owner | Next review |\n"
    "|---|---|---|---|---|---|---|\n"
    "{rows}"
)


def run(json_doc: object | None, md_rows: str = "", today: str = TODAY) -> int:
    tmp = Path(tempfile.mkdtemp())
    json_path = tmp / "exceptions.json"
    if json_doc is None:
        json_path = tmp / "missing.json"
    else:
        json_path.write_text(
            json.dumps(
                json_doc if isinstance(json_doc, str) else {"exceptions": json_doc}
            )
            if not isinstance(json_doc, str)
            else json_doc,
            encoding="utf-8",
        )
    md_path = tmp / "register.md"
    md_path.write_text(MD_TEMPLATE.format(rows=md_rows), encoding="utf-8")
    return main(
        ["--json-file", str(json_path), "--md-file", str(md_path), "--today", today]
    )


class JsonGateTests(unittest.TestCase):
    def test_undated_entries_warn_but_do_not_block(self):
        code = run({"CVE-1": {"packages": ["x"], "installed_versions": ["1"]}})
        self.assertEqual(code, 0)

    def test_future_date_in_term(self):
        code = run({"CVE-1": {"packages": ["x"], "re_review_by": "2026-12-02"}})
        self.assertEqual(code, 0)

    def test_past_date_fails_loud(self):
        code = run({"CVE-1": {"packages": ["x"], "re_review_by": "2026-01-01"}})
        self.assertEqual(code, 1)

    def test_boundary_date_is_still_in_term(self):
        code = run({"CVE-1": {"packages": ["x"], "re_review_by": TODAY}})
        self.assertEqual(code, 0)

    def test_malformed_date_blocks(self):
        code = run({"CVE-1": {"packages": ["x"], "re_review_by": "next tuesday"}})
        self.assertEqual(code, 1)

    def test_missing_file_is_environment_error(self):
        self.assertEqual(run(None), 2)

    def test_invalid_json_is_environment_error(self):
        code = run("{not json")
        self.assertEqual(code, 2)


class MarkdownTriageTests(unittest.TestCase):
    def row(self, finding: str, when: str) -> str:
        return (
            f"| {finding} | pkg 1.0 | 2.0 | HIGH | Scanner-only | Owners | {when} |\n"
        )

    def test_expired_md_rows_advisory_not_blocking(self):
        code = run({}, md_rows=self.row("CVE-9", "2026-08-22"))
        self.assertEqual(code, 0)

    def test_in_term_md_rows_ok(self):
        code = run({}, md_rows=self.row("CVE-9", "2026-12-02"))
        self.assertEqual(code, 0)


class RealTreeTests(unittest.TestCase):
    def test_shipped_registers_pass_today(self):
        # Runs against the actual repo files; if a shipped exception ever
        # expires this test starts failing in tools/tests, exactly as asked.
        root = Path(__file__).resolve().parents[2]
        code = main(
            [
                "--json-file",
                str(root / "docs/vulnerability-exceptions.json"),
                "--md-file",
                str(root / "docs/VULNERABILITY-EXCEPTIONS.md"),
            ]
        )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.reconcile_trivy_sarif import load_findings, reconcile, render_summary


class ReconcileTrivySarifTests(unittest.TestCase):
    def _write_sarif(self, result: dict) -> Path:
        directory = Path(self.tempdir.name)
        path = directory / "report.sarif"
        path.write_text(
            json.dumps({"runs": [{"results": [result]}]}), encoding="utf-8"
        )
        return path

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def _result(rule_id: str = "CVE-TEST-1", installed: str = "1.0") -> dict:
        return {
            "ruleId": rule_id,
            "message": {
                "text": (
                    "Package: demo\n"
                    f"Installed Version: {installed}\n"
                    f"Vulnerability {rule_id}\n"
                    "Severity: HIGH\n"
                    "Fixed Version: 2.0\n"
                )
            },
            "locations": [
                {"physicalLocation": {"artifactLocation": {"uri": "Python"}}}
            ],
        }

    def test_reviewed_finding_passes(self) -> None:
        findings = load_findings(self._write_sarif(self._result()))
        problems = reconcile(
            findings,
            {
                "CVE-TEST-1": {
                    "packages": ["demo"],
                    "installed_versions": ["1.0"],
                    "allow_fixed": True,
                }
            },
        )
        self.assertEqual([], problems)
        self.assertIn("Review status: **PASS**", render_summary(Path("report"), findings, problems))

    def test_unknown_finding_fails(self) -> None:
        findings = load_findings(self._write_sarif(self._result()))
        problems = reconcile(findings, {})
        self.assertEqual(1, len(problems))
        self.assertIn("unreviewed CVE-TEST-1 demo@1.0", problems[0])

    def test_version_mismatch_fails_even_when_id_is_known(self) -> None:
        findings = load_findings(self._write_sarif(self._result(installed="1.1")))
        problems = reconcile(
            findings,
            {
                "CVE-TEST-1": {
                    "packages": ["demo"],
                    "installed_versions": ["1.0"],
                    "allow_fixed": True,
                }
            },
        )
        self.assertTrue(any("installed-version mismatch" in problem for problem in problems))

    def test_non_high_or_critical_results_are_ignored(self) -> None:
        result = self._result()
        result["message"]["text"] = result["message"]["text"].replace("HIGH", "LOW")
        findings = load_findings(self._write_sarif(result))
        self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()

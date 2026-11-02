"""Unit tests for the SPEC→test coverage matrix gate (plan ARB §3.1).

Every failure mode the checker is supposed to catch is planted here as a
temp-file violation and asserted to fail non-zero — the same proof style the
other tools/tests use. The final test runs the checker against the real
repo matrix + real test-vectors so the shipped artifact cannot silently
drift.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import check_coverage_matrix as gate  # noqa: E402

BEGIN = "<!-- SPEC-COVERAGE-MATRIX:BEGIN version=1 columns=clause,gist,surface,tests,status -->"
END = "<!-- SPEC-COVERAGE-MATRIX:END -->"
HEADER = "| clause | gist | surface | tests | status |\n|---|---|---|---|---|"


def render(rows: list[str]) -> str:
    return f"# t\n\n{BEGIN}\n{HEADER}\n" + "\n".join(rows) + f"\n{END}\n"


def make_vectors_dir(tmp: Path) -> Path:
    vectors = tmp / "test-vectors"
    (vectors / "sdk").mkdir(parents=True)
    (vectors / "l1-envelope.json").write_text(
        json.dumps({"suite": "L1", "vectors": [{"id": "L1-001"}, {"id": "L1-002"}]})
    )
    (vectors / "sdk" / "validation-corpus.json").write_text(
        json.dumps({"fixtures": [{"id": "sdk-001"}, {"id": "sdk-002"}]})
    )
    return vectors


class GateTests(unittest.TestCase):
    def run_gate(
        self, matrix_text: str, vectors: Path, fail_on_gap: bool = False
    ) -> int:
        with tempfile.TemporaryDirectory() as raw:
            matrix = Path(raw) / "matrix.md"
            matrix.write_text(matrix_text)
            sys.argv = [
                "check_coverage_matrix.py",
                "--matrix",
                str(matrix),
                "--test-vectors-dir",
                str(vectors),
            ] + (["--fail-on-gap"] if fail_on_gap else [])
            return gate.main()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.vectors = make_vectors_dir(Path(self._tmp.name))

    def test_valid_matrix_passes(self) -> None:
        text = render(
            [
                "| A-01 | envelope shape | schema | L1-001, L1-002 | green |",
                "| A-02 | corpus gate | schema+corpus | sdk-001 | green |",
                "| A-03 | unproven clause | platform | - | gap(no vector yet) |",
                "| A-04 | governance text | governance | - | exempt(human-adopted policy) |",
                "| A-05 | partial coverage | schema | sdk-002 | gap(negative pending) |",
            ]
        )
        self.assertEqual(0, self.run_gate(text, self.vectors))

    def test_green_row_without_tests_fails(self) -> None:
        text = render(
            [
                "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |",
                "| A-02 | naked green | platform | - | green |",
            ]
        )
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_unknown_vector_reference_fails(self) -> None:
        text = render(
            [
                "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |",
                "| A-02 | typo | schema | L1-999 | green |",
            ]
        )
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_unmapped_vector_id_fails(self) -> None:
        # plan §3.1 direction (b): a fixture that exists but appears in no row.
        text = render(
            [
                "| A-01 | ok | schema | L1-001, L1-002, sdk-001 | green |",
                "| A-02 | hid | schema | - | gap(should have referenced sdk-002) |",
            ]
        )
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_bad_status_token_fails(self) -> None:
        text = render(
            [
                "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |",
                "| A-02 | red? | platform | - | red |",
            ]
        )
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_exempt_without_reason_fails(self) -> None:
        for status in ("exempt", "exempt()"):
            text = render(
                [
                    "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |",
                    f"| A-02 | shrug | governance | - | {status} |",
                ]
            )
            self.assertEqual(1, self.run_gate(text, self.vectors), f"status={status}")

    def test_duplicate_clause_id_fails(self) -> None:
        row = "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |"
        text = render([row, "| A-01 | dupe | schema | - | gap |"])
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_missing_header_block_fails(self) -> None:
        text = render(["| A-01 | no block | schema | L1-001 | green |"]).replace(
            BEGIN, ""
        )
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_column_mismatch_fails(self) -> None:
        bad = BEGIN.replace(
            "columns=clause,gist,surface,tests,status", "columns=clause,gist,status"
        )
        text = render(
            [
                "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |",
            ]
        ).replace(BEGIN, bad)
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_malformed_row_cell_count_fails(self) -> None:
        text = render(
            [
                "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |",
                "| A-02 | too few cells | green |",
            ]
        )
        self.assertEqual(1, self.run_gate(text, self.vectors))

    def test_fail_on_gap_mode_flips_the_gate(self) -> None:
        text = render(
            [
                "| A-01 | ok | schema | L1-001, L1-002, sdk-001, sdk-002 | green |",
                "| A-02 | real gap | platform | - | gap(pending) |",
            ]
        )
        self.assertEqual(0, self.run_gate(text, self.vectors))  # advisory today
        self.assertEqual(
            1, self.run_gate(text, self.vectors, fail_on_gap=True)
        )  # wave-5 mode


class RealRepoMatrixTests(unittest.TestCase):
    def test_shipped_matrix_is_valid_and_vectors_are_mapped(self) -> None:
        sys.argv = ["check_coverage_matrix.py"]
        self.assertEqual(0, gate.main(), "docs/SPEC-COVERAGE.md must pass its own gate")

    def test_shipped_matrix_rows_are_unique_and_status_wellformed(self) -> None:
        _attrs, rows = gate.parse_matrix(
            (ROOT / "docs" / "SPEC-COVERAGE.md").read_text(encoding="utf-8")
        )
        clauses = [row.clause for row in rows]
        self.assertEqual(len(clauses), len(set(clauses)))
        errors = gate.validate(rows, gate.collect_vector_ids(ROOT / "test-vectors"))
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()

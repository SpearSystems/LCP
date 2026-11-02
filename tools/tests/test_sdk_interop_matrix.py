"""Unit tests for tools/sdk_interop_matrix.py (cross-SDK interop, LEP-0002 step 1).

Deterministic and network-free: fixed vectors + canned probe outputs,
including the four known fractional emitters (Go/Java/Kotlin/Rust) from the
LEP-0002 evidence table and planted-violation fixtures proving the checks are
fail-loud, not vacuous.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1]))

import sdk_interop_matrix as interop  # noqa: E402

VECTOR = {
    "id": "lcp-sdk-hmac-001",
    "algorithm": "HMAC-SHA256",
    "secret": "sdk-shared-secret",
    "timestamp": "2026-08-15T10:20:00Z",
    "idempotency_key": "sdk-vector-001",
    "body_utf8": '{"hello":"world"}',
    "signature_hex": "50f90d29b46e92f257eae62c94a3d985bf9a925da03fee82e33c800fe54e7259",
}

EXPECTED_CANONICAL = hashlib.sha256(
    b"2026-08-15T10:20:00Z\nsdk-vector-001\n" + b'{"hello":"world"}'
).hexdigest()

WHOLE_SECOND_SDKS = ["python", "typescript", "ruby", "php", "swift", "csharp"]
FRACTIONAL_SDKS = ["go", "java", "kotlin", "rust"]

FAKE_SIG = "0" * 64


def canned_probe(
    sdk: str,
    signature_hex: str = VECTOR["signature_hex"],
    canonical: str = EXPECTED_CANONICAL,
    emitted: str | None = None,
) -> dict[str, Any]:
    """Build one probe result; default shape matches the SDK's real behaviour today."""
    if emitted is None:
        emitted = (
            "2026-08-15T10:20:00Z"
            if sdk in WHOLE_SECOND_SDKS
            else "2026-08-15T10:20:00.123456789Z"
        )
    return {
        "sdk": sdk,
        "canonical_sha256": canonical,
        "signature_hex": signature_hex,
        "emitted_timestamp": emitted,
    }


def all_probes(**overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    probes = {sdk: canned_probe(sdk) for sdk in interop.SDK_LANGS}
    probes.update(overrides)
    return probes


class ExtractTests(unittest.TestCase):
    def test_clean_line(self):
        text = "LCP_INTEROP " + json.dumps(canned_probe("python")) + "\n"
        self.assertEqual(interop.extract_probe(text, "python")["sdk"], "python")

    def test_noisy_test_runner_output(self):
        probe = json.dumps(canned_probe("kotlin"))
        text = f"Task :test\nsome gradle noise\nLCP_INTEROP {probe}\nBUILD SUCCESSFUL\n"
        self.assertEqual(
            interop.extract_probe(text, "kotlin")["signature_hex"],
            VECTOR["signature_hex"],
        )

    def test_bare_json_accepted(self):
        self.assertEqual(
            interop.extract_probe(json.dumps(canned_probe("rust")))["sdk"], "rust"
        )

    def test_sdk_mismatch_is_loud(self):
        text = "LCP_INTEROP " + json.dumps(canned_probe("rust"))
        with self.assertRaises(interop.ProbeError):
            interop.extract_probe(text, "go")

    def test_missing_field_is_loud(self):
        broken = canned_probe("php")
        del broken["emitted_timestamp"]
        with self.assertRaises(interop.ProbeError):
            interop.extract_probe("LCP_INTEROP " + json.dumps(broken), "php")

    def test_uppercase_hex_is_loud(self):
        broken = canned_probe("python", signature_hex=VECTOR["signature_hex"].upper())
        with self.assertRaises(interop.ProbeError):
            interop.extract_probe("LCP_INTEROP " + json.dumps(broken), "python")

    def test_no_probe_line_is_loud(self):
        with self.assertRaises(interop.ProbeError):
            interop.extract_probe("the test suite printed nothing useful", "go")


class SignatureIdentityTests(unittest.TestCase):
    def test_all_sdks_aggregate_green_on_signature(self):
        matrix = interop.build_matrix(VECTOR, all_probes())
        self.assertTrue(matrix["signature_identity"])
        self.assertEqual(matrix["exit_code"], 0)
        self.assertTrue(all(row["signature_matches_vector"] for row in matrix["rows"]))

    def test_tampered_signature_blocks(self):
        probes = all_probes(csharp=canned_probe("csharp", signature_hex=FAKE_SIG))
        matrix = interop.build_matrix(VECTOR, probes)
        self.assertFalse(matrix["signature_identity"])
        self.assertEqual(matrix["exit_code"], 1)
        self.assertIn("csharp", matrix["blocking"][0])

    def test_canonical_divergence_blocks_even_with_matching_hex_claim(self):
        # An SDK that claims the published signature but hashed different bytes
        # must not pass: the orchestrator recomputes the reference digest.
        probes = all_probes(ruby=canned_probe("ruby", canonical="f" * 64))
        matrix = interop.build_matrix(VECTOR, probes)
        self.assertFalse(matrix["signature_identity"])
        self.assertEqual(matrix["exit_code"], 1)

    def test_missing_probe_blocks_exit_two(self):
        probes = all_probes()
        del probes["go"]
        matrix = interop.build_matrix(VECTOR, probes)
        self.assertEqual(matrix["exit_code"], 2)
        self.assertIn("go", " ".join(matrix["blocking"]))
        row = next(r for r in matrix["rows"] if r["sdk"] == "go")
        self.assertFalse(row["present"])
        self.assertIsNone(row["shape_status"])


class ShapeMatrixTests(unittest.TestCase):
    def test_today_matrix_is_six_pass_four_fail(self):
        matrix = interop.build_matrix(VECTOR, all_probes())
        self.assertEqual(matrix["shape_counts"], {"pass": 6, "fail": 4, "missing": 0})
        for sdk in FRACTIONAL_SDKS:
            row = next(r for r in matrix["rows"] if r["sdk"] == sdk)
            self.assertEqual(row["shape_status"], "FAIL")
            self.assertIn("LEP-0002 evidence", row["shape_note"])
        self.assertEqual(matrix["shape_regressions"], [])
        self.assertEqual(matrix["shape_alignments"], [])

    def test_shape_fail_never_changes_exit_code(self):
        matrix = interop.build_matrix(VECTOR, all_probes())
        self.assertEqual(matrix["exit_code"], 0)  # four shape FAILs, still green

    def test_planted_fractional_in_whole_second_sdk_is_regression(self):
        # Acceptance proof: a future fractional regression in a currently
        # whole-second SDK is caught and flagged (advisory, not exit code).
        probes = all_probes(
            python=canned_probe("python", emitted="2026-08-15T10:20:00.123456Z")
        )
        matrix = interop.build_matrix(VECTOR, probes)
        row = next(r for r in matrix["rows"] if r["sdk"] == "python")
        self.assertEqual(row["shape_status"], "FAIL")
        self.assertIn("REGRESSION", row["shape_note"])
        self.assertEqual(matrix["shape_regressions"], ["python"])
        self.assertEqual(matrix["exit_code"], 0)

    def test_known_fractional_sdk_aligning_is_reported_as_progress(self):
        probes = all_probes(go=canned_probe("go", emitted="2026-08-15T10:20:00Z"))
        matrix = interop.build_matrix(VECTOR, probes)
        row = next(r for r in matrix["rows"] if r["sdk"] == "go")
        self.assertEqual(row["shape_status"], "PASS")
        self.assertIn("ALIGNED", row["shape_note"])
        self.assertEqual(matrix["shape_alignments"], ["go"])

    def test_wrong_instant_whole_second_shape_fails(self):
        probes = all_probes(rust=canned_probe("rust", emitted="2027-01-01T00:00:00Z"))
        matrix = interop.build_matrix(VECTOR, probes)
        row = next(r for r in matrix["rows"] if r["sdk"] == "rust")
        self.assertEqual(row["shape_status"], "FAIL")
        self.assertIn("wrong instant", row["shape_note"])


class CliTests(unittest.TestCase):
    def _write_probes(self, dirpath: str, probes: dict) -> None:
        for sdk, probe in probes.items():
            path = os.path.join(dirpath, f"probe-{sdk}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(probe, handle)

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = interop.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_aggregate_green_with_advisory_fails_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            vector_path = os.path.join(tmp, "hmac.json")
            with open(vector_path, "w", encoding="utf-8") as handle:
                json.dump(VECTOR, handle)
            probes_dir = os.path.join(tmp, "probes")
            os.makedirs(probes_dir)
            self._write_probes(probes_dir, all_probes())
            summary_path = os.path.join(tmp, "summary.md")
            report_path = os.path.join(tmp, "interop-report.json")
            code, _, _ = self._run(
                [
                    "aggregate",
                    "--vector",
                    vector_path,
                    "--probes",
                    probes_dir,
                    "--summary",
                    summary_path,
                    "--report",
                    report_path,
                ]
            )
            self.assertEqual(code, 0)
            with open(summary_path, encoding="utf-8") as handle:
                summary = handle.read()
            self.assertIn("Signature identity (blocking): PASS", summary)
            self.assertIn("6 PASS / 4 FAIL", summary)
            for sdk in FRACTIONAL_SDKS:
                self.assertIn(f"| {sdk} | PASS |", summary)
            with open(report_path, encoding="utf-8") as handle:
                report = json.load(handle)
            self.assertEqual(
                report["shape_counts"], {"pass": 6, "fail": 4, "missing": 0}
            )
            self.assertTrue(report["signature_identity"])

    def test_aggregate_exit_one_on_tampered_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            vector_path = os.path.join(tmp, "hmac.json")
            with open(vector_path, "w", encoding="utf-8") as handle:
                json.dump(VECTOR, handle)
            probes_dir = os.path.join(tmp, "probes")
            os.makedirs(probes_dir)
            self._write_probes(
                probes_dir,
                all_probes(
                    typescript=canned_probe("typescript", signature_hex=FAKE_SIG)
                ),
            )
            code, _, err = self._run(
                ["aggregate", "--vector", vector_path, "--probes", probes_dir]
            )
            self.assertEqual(code, 1)
            self.assertIn("typescript", err)

    def test_aggregate_rejects_malformed_probe_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            vector_path = os.path.join(tmp, "hmac.json")
            with open(vector_path, "w", encoding="utf-8") as handle:
                json.dump(VECTOR, handle)
            probes_dir = os.path.join(tmp, "probes")
            os.makedirs(probes_dir)
            self._write_probes(probes_dir, all_probes())
            with open(
                os.path.join(probes_dir, "probe-go.json"), "w", encoding="utf-8"
            ) as handle:
                handle.write("not json at all")
            code, _, _ = self._run(
                ["aggregate", "--vector", vector_path, "--probes", probes_dir]
            )
            self.assertEqual(code, 2)

    def test_extract_cli_writes_clean_json(self):
        noisy = (
            f"compile noise\nLCP_INTEROP {json.dumps(canned_probe('swift'))}\nrest\n"
        )
        out, err = io.StringIO(), io.StringIO()
        real_stdin, sys.stdin = sys.stdin, io.StringIO(noisy)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = interop.main(["extract", "--sdk", "swift"])
        finally:
            sys.stdin = real_stdin
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["sdk"], "swift")

    def test_extract_cli_fails_loud(self):
        real_stdin, sys.stdin = sys.stdin, io.StringIO("nothing here")
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = interop.main(["extract", "--sdk", "java"])
        finally:
            sys.stdin = real_stdin
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

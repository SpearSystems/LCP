"""Unit tests for tools/sdk_interop_matrix.py (post-LEP-0002 interop gate).

Deterministic and network-free: fixed vectors + canned probe outputs, and
planted-violation fixtures proving the shape + identity checks are fail-loud,
not vacuous. Carries the evidence-table drift guard:
KNOWN_FRACTIONAL_SDKS (empty after LEP-0002 implementation) is pinned against
the LEP-0002 signer table parsed from governance/LEPs/, so the mirror can
never silently stale. Post-implementation the table's `Emitted form` column
describes PRE-implementation divergence; the caption line above the table
must carry a supersession marker so the parser knows the rows are historical.
The sync test asserts (baseline == parsed_current_emission) — when the
baseline is empty the parser requires the marker (rows in the table are
ignored). A silent stale-parse (a doc edit that removes the marker or
reverts a fractional expectation) turns this suite red.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
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

# Post-LEP-0002 baseline: every SDK is expected to emit canonical whole-second.
WHOLE_SECOND_SDKS = list(interop.SDK_LANGS)
FRACTIONAL_SDKS: list[str] = []  # no SDK is fractional in the current baseline

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
    """Post-LEP-0002: generation-shape is blocking; all ten probe to canonical."""

    def test_all_ten_matrix_is_shape_pass_and_exit_green(self):
        matrix = interop.build_matrix(VECTOR, all_probes())
        self.assertEqual(matrix["shape_counts"], {"pass": 10, "fail": 0, "missing": 0})
        self.assertEqual(matrix["shape_regressions"], [])
        self.assertEqual(matrix["shape_alignments"], [])
        self.assertEqual(matrix["exit_code"], 0)

    def test_planted_fractional_java_reddens_suite(self):
        # Planted proof: revert the Java expectation to fractional and the
        # shape matrix goes red with a blocking exit code. This is the exact
        # regression the LEP-0002 implementation PR must never reintroduce.
        probes = all_probes(java=canned_probe("java", emitted="2026-08-15T10:20:00.123456789Z"))
        matrix = interop.build_matrix(VECTOR, probes)
        row = next(r for r in matrix["rows"] if r["sdk"] == "java")
        self.assertEqual(row["shape_status"], "FAIL")
        self.assertIn("REGRESSION", row["shape_note"])
        self.assertEqual(matrix["shape_regressions"], ["java"])
        self.assertEqual(matrix["exit_code"], 1)

    def test_shape_fail_now_changes_exit_code(self):
        # The pre-implementation "advisory" contract is gone: any shape FAIL
        # exits 1 (SPEC.md §9 canonical generation is a MUST).
        probes = all_probes(
            python=canned_probe("python", emitted="2026-08-15T10:20:00.123456Z")
        )
        matrix = interop.build_matrix(VECTOR, probes)
        self.assertEqual(matrix["exit_code"], 1)

    def test_planted_fractional_in_whole_second_sdk_is_regression(self):
        # A future fractional regression in any SDK is caught and flagged as
        # blocking REGRESSION.
        probes = all_probes(
            python=canned_probe("python", emitted="2026-08-15T10:20:00.123456Z")
        )
        matrix = interop.build_matrix(VECTOR, probes)
        row = next(r for r in matrix["rows"] if r["sdk"] == "python")
        self.assertEqual(row["shape_status"], "FAIL")
        self.assertIn("REGRESSION", row["shape_note"])
        self.assertEqual(matrix["shape_regressions"], ["python"])
        self.assertEqual(matrix["exit_code"], 1)

    def test_wrong_instant_whole_second_shape_fails(self):
        probes = all_probes(rust=canned_probe("rust", emitted="2027-01-01T00:00:00Z"))
        matrix = interop.build_matrix(VECTOR, probes)
        row = next(r for r in matrix["rows"] if r["sdk"] == "rust")
        self.assertEqual(row["shape_status"], "FAIL")
        self.assertIn("wrong instant", row["shape_note"])
        self.assertEqual(matrix["exit_code"], 1)


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

    def test_aggregate_all_green_writes_report(self):
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
            self.assertIn("10 PASS / 0 FAIL", summary)
            for sdk in interop.SDK_LANGS:
                self.assertIn(f"| {sdk} | PASS |", summary)
            with open(report_path, encoding="utf-8") as handle:
                report = json.load(handle)
            self.assertEqual(
                report["shape_counts"], {"pass": 10, "fail": 0, "missing": 0}
            )
            self.assertTrue(report["signature_identity"])

    def test_aggregate_exit_one_on_shape_regression(self):
        # Post-LEP-0002 blocking proof: a Java revert to fractional reddens
        # the CLI aggregate even though signature identity stays intact.
        with tempfile.TemporaryDirectory() as tmp:
            vector_path = os.path.join(tmp, "hmac.json")
            with open(vector_path, "w", encoding="utf-8") as handle:
                json.dump(VECTOR, handle)
            probes_dir = os.path.join(tmp, "probes")
            os.makedirs(probes_dir)
            self._write_probes(
                probes_dir,
                all_probes(java=canned_probe("java", emitted="2026-08-15T10:20:00.123456789Z")),
            )
            code, _, err = self._run(
                ["aggregate", "--vector", vector_path, "--probes", probes_dir]
            )
            self.assertEqual(code, 1)
            self.assertIn("java", err)
            self.assertIn("blocking", err)

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


# ---------------------------------------------------------------------------
# LEP-0002 evidence-table drift guard (DossierLep0002 process finding #2).
#
# tools/sdk_interop_matrix.KNOWN_FRACTIONAL_SDKS is a hand-maintained mirror
# of the signer-side divergence table in LEP-0002. This section parses the
# table out of the LEP itself and asserts the two agree, so a documentation
# edit (e.g. step 2 switching Java/Kotlin/Rust to canonical whole-second
# emission) can never silently stale the matrix baseline. The governance file
# is read, never rewritten: parser behaviour is proven against fixture
# strings only.
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
LEP0002_PATH = (
    ROOT / "governance" / "LEPs" / "LEP-0002-canonical-timestamp-and-signing-interop.md"
)
EVIDENCE_HEADER = ("SDK", "File:line", "Expression", "Emitted form")

# Display names in the LEP table → tool language ids (interop.SDK_LANGS).
SDK_DISPLAY_ID_OVERRIDES = {"C#": "csharp"}


def _structure_problem(detail: str) -> ValueError:
    """Fail-loud error for evidence-table structure changes.

    A structural change (header rename, column count, separator loss, unknown
    SDK row) is always a deliberate documentation edit, so the message routes
    the editor to update the tool baseline and these fixtures together.
    """
    return ValueError(
        f"LEP-0002 signer evidence table structure changed: {detail} A table "
        "structure change is a deliberate edit — update "
        "tools/sdk_interop_matrix.KNOWN_FRACTIONAL_SDKS and "
        "tools/tests/test_sdk_interop_matrix.py fixtures together."
    )


def _split_markdown_row(line: str) -> list[str]:
    """Split one markdown table row on unescaped pipes, cells stripped."""
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())]
    if cells and not cells[0]:  # leading pipe
        cells = cells[1:]
    if cells and not cells[-1]:  # trailing pipe
        cells = cells[:-1]
    return cells


def _lep_sdk_id(display: str) -> str:
    """Map an evidence-table SDK display name to a tool language id."""
    name = display.strip().strip("*`")  # tolerate bold/code decoration
    sdk = SDK_DISPLAY_ID_OVERRIDES.get(name, name.lower())
    if sdk not in interop.SDK_LANGS:
        raise _structure_problem(
            f"evidence table names SDK {display!r}, which "
            "tools/sdk_interop_matrix.SDK_LANGS does not know."
        )
    return sdk


# A caption above the LEP-0002 evidence table marks the `Emitted form` column
# as the *pre-implementation* divergence record; the current emission baseline
# is the empty set (SPEC.md Â§9 canonical MUST). The parser requires the
# marker whenever KNOWN_FRACTIONAL_SDKS is empty, so a doc edit that silently
# removes the marker (or reintroduces a fractional expectation) turns the sync
# test red — silent stale-parse is the failure mode this guard exists to avoid.
SUPERSESSION_MARKER = "superseded by implementation PR"
CAPTION_WINDOW_LINES = 20


def _find_supersession_caption(lines: list, header_idx: int) -> bool:
    """True when the caption above the table names an implementation PR."""
    start = max(0, header_idx - CAPTION_WINDOW_LINES)
    window = " ".join(lines[start:header_idx])
    return SUPERSESSION_MARKER.lower() in window.lower()


def parse_lep_fractional_rows(text: str) -> set:
    """Return tool ids of SDKs whose Emitted-form cell starts with
    **fractional in the LEP-0002 signer-side evidence table, IGNORING any
    supersession caption. Used by parser tests to prove the row-scan itself
    still works when the caption is removed.

    Tolerant of markdown cosmetics inside rows (whitespace/soft wraps, bold
    or code decoration on the SDK name, cell case); strict about the table's
    header shape, separator row, column count, and the SDK name set — any
    structural change raises ValueError with a both-files-together message.
    Mid-cell uses of the word 'fractional' (e.g. Go's "no fractional-seconds
    token", the "7 whole-second / 3 fractional" CI note) never match: the
    cell must *start* with the bold marker **fractional.
    """
    lines = text.splitlines()
    header_at = [
        i
        for i, ln in enumerate(lines)
        if ln.lstrip().startswith("|")
        and tuple(_split_markdown_row(ln)) == EVIDENCE_HEADER
    ]
    if len(header_at) != 1:
        raise _structure_problem(
            f"expected exactly one header row '| {' | '.join(EVIDENCE_HEADER)} |',"
            f" found {len(header_at)}."
        )
    header_idx = header_at[0]

    sep_idx = header_idx + 1
    sep_cells = (
        _split_markdown_row(lines[sep_idx])
        if sep_idx < len(lines) and lines[sep_idx].lstrip().startswith("|")
        else []
    )
    if len(sep_cells) != len(EVIDENCE_HEADER) or not all(
        re.fullmatch(r":?-{3,}:?", cell) for cell in sep_cells
    ):
        raise _structure_problem(
            "the header row must be followed by a 4-column |---| separator row."
        )

    # Join physical lines until each row carries a full set of cells and ends
    # its closing pipe, so rows wrapped across lines still parse; a blank
    # line ends the table.
    rows: list[list[str]] = []
    buf = ""
    for ln in lines[sep_idx + 1 :]:
        if not ln.strip():
            break
        if (
            buf
            and buf.endswith("|")
            and len(_split_markdown_row(buf)) >= len(EVIDENCE_HEADER)
        ):
            rows.append(_split_markdown_row(buf))
            buf = ""
        buf = f"{buf} {ln}" if buf else ln
    if buf:
        rows.append(_split_markdown_row(buf))

    fractional: set[str] = set()
    for n, cells in enumerate(rows, start=1):
        if len(cells) != len(EVIDENCE_HEADER):
            raise _structure_problem(
                f"table row {n} has {len(cells)} cells, expected "
                f"{len(EVIDENCE_HEADER)}: {' | '.join(cells)!r}."
            )
        emitted = " ".join(cells[3].split())
        if emitted.lower().startswith("**fractional"):
            fractional.add(_lep_sdk_id(cells[0]))
    return fractional


def parse_lep_fractional_sdks(text: str) -> set:
    """Return the SDKs whose CURRENT emission diverges from canonical.

    Post-implementation the LEP-0002 evidence table describes PRE-
    implementation divergence; when a caption above the table carries the
    'superseded by implementation PR' marker, the CURRENT-emission set is
    empty (the rows are the historical dossier record). Otherwise the parser
    falls back to the raw row scan so a doc edit that removes the marker (or
    reintroduces a fractional expectation) still reaches the sync-test
    baseline check — silent stale-parse is the failure mode we avoid.
    """
    lines = text.splitlines()
    header_at = [
        i
        for i, ln in enumerate(lines)
        if ln.lstrip().startswith("|")
        and tuple(_split_markdown_row(ln)) == EVIDENCE_HEADER
    ]
    if header_at and _find_supersession_caption(lines, header_at[0]):
        return set()
    return parse_lep_fractional_rows(text)


EVIDENCE_TABLE_FIXTURE = """\
Some prose before the table.

**Signer-side divergence (generation):**

| SDK | File:line | Expression | Emitted form |
|---|---|---|---|
| Python | `p.py:1` | `strftime("%Y-%m-%dT%H:%M:%SZ")` | whole seconds |
| Ruby | `r.rb:1` | `Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ")` | whole seconds |
| Go | `g.go:1` | `time.Now().UTC().Format(time.RFC3339)` | **whole seconds** — the `time.RFC3339` layout carries no fractional-seconds token (CI matrix measures 7 whole-second / 3 fractional) |
| C# | `c.cs:1` | `DateTimeOffset.UtcNow.ToString(...)` | whole seconds |
| Java | `J.java:1` | `Instant.now().toString()` | **fractional** (0/3/6/9 digits as returned by the clock) |
| Kotlin | `k.kt:1` | `Instant.now().toString()` | **fractional** (as Java) |
| Rust | `r.rs:1` | `OffsetDateTime::now_utc().format(&Rfc3339)` | **Fractional** (nanosecond subsecond formatted when nonzero) |

Prose after the table.
"""


class LepEvidenceTableParserTests(unittest.TestCase):
    """Row-scan parser unit tests against fixture strings only; never mutate
    governance files. EVIDENCE_TABLE_FIXTURE carries no supersession caption,
    so the raw row scanner (parse_lep_fractional_rows) is the target for the
    row-structure tests."""

    def test_extracts_only_bold_leading_fractional_rows(self):
        # Row-scan test: EVIDENCE_TABLE_FIXTURE carries no caption, so the raw
        # row parser is the target. Go's cell mentions 'fractional' twice mid-cell
        # and must NOT match; Rust's capitalized '**Fractional' must match; C#
        # maps to 'csharp' and is excluded as whole seconds.
        self.assertEqual(
            parse_lep_fractional_rows(EVIDENCE_TABLE_FIXTURE),
            {"java", "kotlin", "rust"},
        )

    def test_tolerates_wrapped_rows_and_decorated_names(self):
        wrapped = EVIDENCE_TABLE_FIXTURE.replace(
            "| **Fractional** (nanosecond subsecond formatted when nonzero) |",
            "| **Fractional** (nanosecond subsecond\n  formatted when nonzero) |",
        ).replace("| Rust |", "| **Rust** |")
        self.assertIn("rust", parse_lep_fractional_rows(wrapped))

    def test_fourth_fractional_claim_is_not_silently_dropped(self):
        # Fixture copy of the table claiming Ruby emits fractional too. The
        # row-scan reports all four; a doc edit that widens the set (or
        # removes the caption on the real LEP) goes red against the empty
        # post-implementation baseline in LepEvidenceBaselineTests.
        planted = EVIDENCE_TABLE_FIXTURE.replace(
            '`Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ")` | whole seconds |',
            '`Time.now.utc.strftime("%Y-%m-%dT%H:%M:%SZ")` | **fractional** (planted) |',
        )
        self.assertEqual(
            parse_lep_fractional_rows(planted),
            {"java", "kotlin", "ruby", "rust"},
        )
        self.assertNotEqual(
            parse_lep_fractional_rows(planted),
            set(interop.KNOWN_FRACTIONAL_SDKS),
        )

    def test_caption_marker_switches_parser_to_current_emission(self):
        # A caption above the table naming the implementation PR flips the
        # wrapper's interpretation: the rows are the historical dossier, the
        # current-emission baseline is empty.
        captioned = EVIDENCE_TABLE_FIXTURE.replace(
            "**Signer-side divergence (generation):**",
            "**Signer-side divergence (generation):** _state at draft time, "
            "superseded by implementation PR #157_",
        )
        self.assertEqual(parse_lep_fractional_sdks(captioned), set())
        # The raw row-scan still sees the three fractional rows, proving the
        # wrapper — not a parser bug — is what returns empty.
        self.assertEqual(
            parse_lep_fractional_rows(captioned),
            {"java", "kotlin", "rust"},
        )

    def test_no_caption_falls_back_to_row_scan(self):
        # Without the marker, the wrapper must delegate to the row-scan so
        # a silent caption deletion (or a pre-implementation doc edit)
        # cannot mask a widened fractional set.
        self.assertEqual(
            parse_lep_fractional_sdks(EVIDENCE_TABLE_FIXTURE),
            parse_lep_fractional_rows(EVIDENCE_TABLE_FIXTURE),
        )


    def test_header_rename_fails_loud(self):
        broken = EVIDENCE_TABLE_FIXTURE.replace(
            "| Emitted form |", "| Timestamp form |"
        )
        with self.assertRaises(ValueError) as ctx:
            parse_lep_fractional_rows(broken)
        self.assertIn("found 0", str(ctx.exception))
        self.assertIn("KNOWN_FRACTIONAL_SDKS", str(ctx.exception))

    def test_extra_column_fails_loud(self):
        broken = EVIDENCE_TABLE_FIXTURE.replace(
            "| SDK | File:line | Expression | Emitted form |",
            "| SDK | File:line | Expression | Emitted form | Notes |",
        ).replace("|---|---|---|---|", "|---|---|---|---|---|")
        with self.assertRaises(ValueError) as ctx:
            parse_lep_fractional_rows(broken)
        self.assertIn("structure changed", str(ctx.exception))

    def test_missing_separator_fails_loud(self):
        broken = EVIDENCE_TABLE_FIXTURE.replace("|---|---|---|---|", "")
        with self.assertRaises(ValueError) as ctx:
            parse_lep_fractional_rows(broken)
        self.assertIn("separator row", str(ctx.exception))

    def test_malformed_row_fails_loud(self):
        broken = EVIDENCE_TABLE_FIXTURE.replace(
            "| C# | `c.cs:1` | `DateTimeOffset.UtcNow.ToString(...)` | whole seconds |",
            "| C# | `c.cs:1` | whole seconds |",
        )
        with self.assertRaises(ValueError) as ctx:
            parse_lep_fractional_rows(broken)
        self.assertIn("cells", str(ctx.exception))

    def test_unknown_fractional_sdk_fails_loud(self):
        broken = EVIDENCE_TABLE_FIXTURE.replace("| Java |", "| Dart |")
        with self.assertRaises(ValueError) as ctx:
            parse_lep_fractional_rows(broken)
        self.assertIn("Dart", str(ctx.exception))


class LepEvidenceBaselineTests(unittest.TestCase):
    """#152 TRUTHFULNESS drift guard.

    Post-implementation the LEP-0002 evidence table's `Emitted form` column
    is a historical record (state at draft time); the wrapper returns the
    empty set whenever the supersession caption is present. The sync test
    compares the empty baseline against the table's CURRENT-emission
    interpretation, so a doc edit that removes the caption (equivalent to
    "reverting Java to fractional expectation") turns the suite red — the
    failure mode we set out to avoid is a silent stale-parse.
    """

    def _lep_text(self) -> str:
        if not LEP0002_PATH.is_file():
            self.fail(f"LEP-0002 moved or missing: expected {LEP0002_PATH}")
        return LEP0002_PATH.read_text(encoding="utf-8")

    def test_known_fractional_baseline_matches_lep_evidence_table(self):
        text = self._lep_text()
        emitted = parse_lep_fractional_sdks(text)
        self.assertEqual(
            emitted,
            set(interop.KNOWN_FRACTIONAL_SDKS),
            "tools/sdk_interop_matrix.KNOWN_FRACTIONAL_SDKS has drifted from the "
            "LEP-0002 signer evidence table's CURRENT emission; update both "
            "together (revert the caption for a genuine LEP reopening).",
        )

    def test_supersession_caption_present_when_baseline_empty(self):
        # The baseline is empty post-implementation; the table must therefore
        # carry the supersession marker so the wrapper ignores the historical
        # rows. Silent caption deletion is the failure mode we guard against.
        if interop.KNOWN_FRACTIONAL_SDKS:
            self.skipTest("non-empty baseline; marker not required")
        text = self._lep_text()
        self.assertIn(
            SUPERSESSION_MARKER.lower(),
            text.lower(),
            "LEP-0002 evidence table lost its supersession caption while the "
            "KNOWN_FRACTIONAL_SDKS baseline is empty — a Java/Kotlin/Rust "
            "revert or an unmarked doc edit; the interop matrix would go red.",
        )

    def test_planted_reverted_java_fractional_expectation_goes_red(self):
        # Planted proof: a doc edit that removes the caption while keeping the
        # fractional rows (equivalent to reverting Java's expectation) must be
        # caught by the sync test. We simulate by stripping the marker from
        # the real LEP text and asserting the parser now disagrees with the
        # empty baseline.
        text = self._lep_text()
        stripped = text.replace(SUPERSESSION_MARKER, "[caption redacted]")
        self.assertNotEqual(
            parse_lep_fractional_sdks(stripped),
            set(interop.KNOWN_FRACTIONAL_SDKS),
            "the parser must disagree with the empty baseline once the "
            "supersession caption is gone (this is the reverted-Java case).",
        )


if __name__ == "__main__":
    unittest.main()

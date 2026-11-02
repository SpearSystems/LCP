#!/usr/bin/env python3
"""SPEC→test coverage matrix gate (ARB plan section 3.1).

Parses the machine-readable matrix block in ``docs/SPEC-COVERAGE.md`` and
fails loudly when:

  * the BEGIN/END header block is missing, duplicated, malformed, or its
    declared ``columns=`` do not match the actual table header;
  * a clause id is duplicated or empty;
  * a status cell is not ``green``, ``gap``, or ``exempt(reason)``;
  * a ``green`` row lists no test ids;
  * a listed test token that looks like a shared-vector id
    (``L1-*`` / ``L2-*`` / ``L3-*`` / ``sdk-NNN`` / ``lcp-sdk-hmac-NNN``)
    does not exist under ``test-vectors/`` (catches deleted vectors and
    typos);
  * a shared-vector id that exists under ``test-vectors/`` appears in no
    matrix row (plan direction (b): fixtures cannot hide from the ledger).

``gap`` rows are counted and listed — the honest v1 gap backlog — but do
not fail the run. ``--fail-on-gap`` (the wave-5 flip) makes any gap
non-zero exit, and the advisory workflow can be switched from report-only
to blocking in one edit.

Stage limit: the "every id has a row" direction is enforced only for ids
matching the known vector families, so suites that land later (e.g.
``test-vectors/sdk/negative/``) never false-fail this gate before their
matrix rows are written. New ids inside the known families are enforced
immediately.

Only the standard library is used; the run is deterministic and network-free.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]

BEGIN_RE = re.compile(r"<!--\s*SPEC-COVERAGE-MATRIX:BEGIN\s+(?P<attrs>.*?)-->")
END_RE = re.compile(r"<!--\s*SPEC-COVERAGE-MATRIX:END\s*-->")
STATUS_RE = re.compile(
    r"^(?P<kind>green|gap|exempt)(?:\((?P<reason>.*)\))?$", re.DOTALL
)
# Tokens that name shared conformance/corpus/hmac/negative vectors; everything
# else in the Tests column (plat:, sdk-py:, mcp:, tool:, file paths) is an
# opaque, human-greppable reference.
VECTOR_ID_RE = re.compile(
    r"^(?:L[123]-\d+[a-z]?|sdk-\d+|lcp-sdk-hmac-\d+|lcp-neg-[a-z]+-\d+)$"
)
EMPTY_TESTS = {"", "-"}

COLUMNS = ("clause", "gist", "surface", "tests", "status")


class Row(NamedTuple):
    line: int
    clause: str
    tests: list[str]
    kind: str
    reason: str


class MatrixError(Exception):
    """Raised for structural problems that make the matrix unparsable."""


def split_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def parse_matrix(text: str) -> tuple[dict[str, str], list[Row]]:
    begins = list(BEGIN_RE.finditer(text))
    ends = list(END_RE.finditer(text))
    if not begins:
        raise MatrixError(
            "missing '<!-- SPEC-COVERAGE-MATRIX:BEGIN ... -->' header block"
        )
    if len(begins) != 1:
        raise MatrixError(f"{len(begins)} BEGIN markers found; exactly 1 required")
    if not ends:
        raise MatrixError("missing '<!-- SPEC-COVERAGE-MATRIX:END -->' marker")
    if len(ends) != 1:
        raise MatrixError(f"{len(ends)} END markers found; exactly 1 required")
    if ends[0].start() < begins[0].end():
        raise MatrixError("END marker appears before BEGIN marker")

    attrs: dict[str, str] = {}
    for token in begins[0].group("attrs").split():
        if "=" not in token:
            raise MatrixError(f"BEGIN header token '{token}' is not key=value")
        key, _, value = token.partition("=")
        attrs[key] = value
    version = attrs.get("version")
    if version != "1":
        raise MatrixError(
            f"unsupported matrix header version={version!r}; expected version=1"
        )
    declared = tuple(attrs.get("columns", "").split(","))
    if declared != COLUMNS:
        raise MatrixError(
            f"header declares columns={','.join(declared)!r}; checker requires '{','.join(COLUMNS)}'"
        )

    body = text[begins[0].end() : ends[0].start()]
    lines = body.splitlines()
    table = [
        (num + 1, line)
        for num, line in enumerate(lines)
        if line.strip().startswith("|")
    ]
    if len(table) < 2:
        raise MatrixError("no markdown table found inside the matrix block")
    header_line, header = table[0]
    header_cells = tuple(cell.lower() for cell in split_cells(header))
    if header_cells != COLUMNS:
        raise MatrixError(
            f"table header {header_cells} does not match declared columns {COLUMNS} (line {header_line})"
        )

    rows: list[Row] = []
    for line_no, raw in table[1:]:
        cells = split_cells(raw)
        if all(set(cell) <= set("-: ") and cell for cell in cells):
            continue  # separator row
        if len(cells) != len(COLUMNS):
            raise MatrixError(
                f"line {line_no}: expected {len(COLUMNS)} cells, found {len(cells)}"
            )
        clause, _gist, _surface, tests_cell, status_cell = cells
        status = STATUS_RE.match(status_cell)
        if not status:
            raise MatrixError(
                f"line {line_no}: status {status_cell!r} is not green|gap|exempt(reason)"
            )
        tests = [
            token.strip()
            for token in tests_cell.split(",")
            if token.strip() not in EMPTY_TESTS
        ]
        rows.append(
            Row(
                line=line_no,
                clause=clause,
                tests=tests,
                kind=status.group("kind"),
                reason=(status.group("reason") or "").strip(),
            )
        )
    if not rows:
        raise MatrixError("matrix table has header but no data rows")
    return attrs, rows


def collect_vector_ids(vectors_dir: Path) -> set[str]:
    """Every ``id`` in current conformance/corpus/hmac fixture shapes."""
    ids: set[str] = set()
    for path in sorted(vectors_dir.rglob("*.json")):
        if "__pycache__" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MatrixError(
                f"{path.relative_to(ROOT)}: unparseable vector file: {exc}"
            ) from exc
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("id"), str):
            ids.add(data["id"])
        for key in ("vectors", "fixtures"):
            entries = data.get(key)
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                        ids.add(entry["id"])
    return ids


def validate(rows: list[Row], known_ids: set[str]) -> list[str]:
    errors: list[str] = []
    seen: dict[str, int] = {}
    referenced: set[str] = set()
    for row in rows:
        where = f"row {row.clause or '(empty clause)'} (line {row.line})"
        if not row.clause:
            errors.append(f"{where}: clause id must not be empty")
        elif row.clause in seen:
            errors.append(
                f"{where}: duplicate clause id (first seen line {seen[row.clause]})"
            )
        else:
            seen[row.clause] = row.line
        if row.kind == "green" and not row.tests:
            errors.append(
                f"{where}: green row lacks test ids — every testable clause needs at least one named test"
            )
        if row.kind == "exempt" and not row.reason:
            errors.append(f"{where}: exempt status requires a parenthesised reason")
        for token in row.tests:
            if VECTOR_ID_RE.match(token):
                if token in known_ids:
                    referenced.add(token)
                else:
                    errors.append(
                        f"{where}: test id {token!r} does not exist under test-vectors/ (deleted vector or typo?)"
                    )
    orphans = sorted(
        token for token in known_ids - referenced if VECTOR_ID_RE.match(token)
    )
    for token in orphans:
        errors.append(
            f"vector id {token} exists under test-vectors/ but appears in no matrix row (plan section 3.1(b))"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--matrix",
        default=str(ROOT / "docs" / "SPEC-COVERAGE.md"),
        help="coverage matrix markdown file",
    )
    parser.add_argument(
        "--test-vectors-dir",
        default=str(ROOT / "test-vectors"),
        help="directory of shared test vectors",
    )
    parser.add_argument(
        "--fail-on-gap",
        action="store_true",
        help="wave-5 mode: exit non-zero when any gap row exists",
    )
    options = parser.parse_args()

    matrix_path = Path(options.matrix)
    vectors_dir = Path(options.test_vectors_dir)
    try:
        attrs, rows = parse_matrix(matrix_path.read_text(encoding="utf-8"))
        known_ids = collect_vector_ids(vectors_dir)
        errors = validate(rows, known_ids)
    except (MatrixError, OSError) as exc:
        print(f"coverage matrix check failed:\n- {exc}")
        return 1

    counts = {"green": 0, "gap": 0, "exempt": 0}
    gaps: list[str] = []
    for row in rows:
        counts[row.kind] += 1
        if row.kind == "gap":
            gaps.append(row.clause)
    print(
        f"coverage matrix v{attrs['version']}: {len(rows)} clauses — "
        f"{counts['green']} green, {counts['gap']} gap, {counts['exempt']} exempt; "
        f"{len(known_ids)} shared vector ids"
    )
    if gaps:
        print("gap backlog: " + ", ".join(gaps))

    if errors:
        print("coverage matrix check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    if options.fail_on_gap and counts["gap"]:
        print("--fail-on-gap: unresolved gap rows present")
        return 1
    print("coverage matrix structure valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

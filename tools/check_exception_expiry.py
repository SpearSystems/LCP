#!/usr/bin/env python3
"""SBOM-exception re-review expiry checker (ARB plan §3.4, lane W1-CI-Checkers).

Vulnerability exceptions must not outlive their justification. This checks
both registers:

- ``docs/vulnerability-exceptions.json`` (machine-readable, consumed by
  ``tools/reconcile_trivy_sarif.py``): each entry MAY carry ``re_review_by``
  (an ISO date). Entries past their date, or with an unparseable date, FAIL.
  Entries without a date are reported as warnings so the gap stays visible
  (adding REQUIRED dates is a register-schema change, not this checker's call).
- ``docs/VULNERABILITY-EXCEPTIONS.md`` (human register): every row of a
  findings table with a ``Next review`` ISO date that is in the past is
  reported. In this wave those are WARNINGS (plan grace pattern: warn now,
  flip to blocking once stale rows are triaged — tracked for W5); planted
  JSON rows are the blocking proof for the mechanism.

Exit codes: 0 = no expiry violations (warnings allowed), 1 = expired
machine-readable exceptions found, 2 = usage/environment error (unreadable
or malformed register).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = "docs/vulnerability-exceptions.json"
MD_PATH = "docs/VULNERABILITY-EXCEPTIONS.md"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TABLE_ROW_RE = re.compile(r"^\|(?P<cells>.+)\|\s*$")


class RegisterError(RuntimeError):
    """Register missing or malformed — exit 2 (fail loud, not silently green)."""


def parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError as err:
        raise RegisterError(f"unparseable date {text!r}: {err}") from err


def check_json_exceptions(
    path: Path, today: date
) -> tuple[list[str], list[str], list[str]]:
    """Return (violations, warnings, dated_ok) for the machine-readable register."""
    if not path.is_file():
        raise RegisterError(f"exception register missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RegisterError(f"{path} is not valid JSON: {err}") from err
    exceptions = document.get("exceptions")
    if not isinstance(exceptions, dict):
        raise RegisterError(f"{path}: 'exceptions' object missing")
    violations: list[str] = []
    warnings: list[str] = []
    dated_ok: list[str] = []
    for cve, entry in sorted(exceptions.items()):
        if not isinstance(entry, dict):
            warnings.append(f"JSON entry {cve}: not an object; cannot check expiry")
            continue
        raw = entry.get("re_review_by") or entry.get("review_by")
        if raw is None:
            warnings.append(
                f"JSON entry {cve}: no re_review_by date — justification has no expiry"
            )
            continue
        try:
            deadline = parse_date(str(raw))
        except RegisterError:
            violations.append(
                f"JSON entry {cve}: malformed re_review_by {raw!r} (want YYYY-MM-DD)"
            )
            continue
        if deadline < today:
            violations.append(
                f"JSON entry {cve}: re-review deadline {deadline.isoformat()} expired "
                f"({(today - deadline).days} day(s) ago) — re-justify or remove the exception"
            )
        else:
            dated_ok.append(f"JSON entry {cve}: re-review {deadline.isoformat()}")
    return violations, warnings, dated_ok


def check_markdown_register(path: Path, today: date) -> tuple[list[str], list[str]]:
    """Return (expired_rows, dated_rows) from 'Next review' columns of tables."""
    if not path.is_file():
        raise RegisterError(f"markdown register missing: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    expired: list[str] = []
    dated: list[str] = []
    block: list[tuple[int, list[str]]] = []
    for lineno, line in enumerate(lines + [""], 1):
        match = TABLE_ROW_RE.match(line)
        if not match:
            if block:
                expired, dated = _scan_table_block(block, today, expired, dated)
                block = []
            continue
        block.append((lineno, [c.strip() for c in match.group("cells").split("|")]))
    return expired, dated


def _scan_table_block(
    rows: list[tuple[int, list[str]]],
    today: date,
    expired: list[str],
    dated: list[str],
) -> tuple[list[str], list[str]]:
    """Parse one contiguous table block; count 'Next review' dates vs today."""
    header = rows[0][1]
    if "Next review" not in header:
        return expired, dated
    col = header.index("Next review")
    for lineno, cells in rows[1:]:
        if all(not c or set(c) <= set("-: ") for c in cells):
            continue  # separator row
        if len(cells) < col + 1 or not DATE_RE.match(cells[col]):
            continue
        deadline = date.fromisoformat(cells[col])
        label = cells[0] or f"row {lineno}"
        row = f"MD register line {lineno}: {label}: next review {cells[col]}"
        if deadline < today:
            expired.append(
                f"{row} expired {(today - deadline).days} day(s) ago — triage (blocking in W5)"
            )
        else:
            dated.append(row)
    return expired, dated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail on SBOM exceptions past their re-review date"
    )
    parser.add_argument(
        "--root", default=str(ROOT), help="repository root (default: this checkout)"
    )
    parser.add_argument(
        "--json-file", default=None, help="override machine-readable register path"
    )
    parser.add_argument(
        "--md-file", default=None, help="override markdown register path"
    )
    parser.add_argument(
        "--today", default=None, help="ISO date override (for tests/replays)"
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    today = parse_date(args.today) if args.today else date.today()
    json_path = Path(args.json_file) if args.json_file else root / JSON_PATH
    md_path = Path(args.md_file) if args.md_file else root / MD_PATH
    try:
        violations, warnings, dated = check_json_exceptions(json_path, today)
        md_expired, md_dated = check_markdown_register(md_path, today)
    except RegisterError as err:
        print(f"exception-expiry: register error: {err}", file=sys.stderr)
        return 2
    for row in dated + md_dated:
        print(f"  ok    {row}")
    for row in md_expired:
        print(f"  warn  {row}")
    for row in warnings:
        print(f"  warn  {row}")
    for row in violations:
        print(f"  FAIL  {row}")
    blocking = violations
    advisory = md_expired + warnings
    print(
        f"exception-expiry: {len(dated)} dated-in-term, "
        f"{len(advisory)} advisory issue(s), {len(blocking)} violation(s)"
    )
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())

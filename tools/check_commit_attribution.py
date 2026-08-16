#!/usr/bin/env python3
"""Reject prohibited generated-attribution trailers in Git commit messages.

The policy deliberately keeps attribution in the pull request description and
CLA process rather than allowing generated commit trailers. It checks only the
commit range supplied by CI, so historical commits are not rewritten or
revalidated.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import Iterable


PROHIBITED_TRAILERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "generated attribution",
        re.compile(
            r"^\s*(?:[^\w\s]+\s*)?generated\s+(?:with|by)\b.*$",
            re.IGNORECASE,
        ),
    ),
    (
        "co-author attribution",
        re.compile(r"^\s*co[- ]?authored[- ]by\s*:\s*.+$", re.IGNORECASE),
    ),
)


Violation = tuple[int, str, str]


def find_violations(message: str) -> list[Violation]:
    """Return (line number, policy name, offending line) tuples."""

    violations: list[Violation] = []
    for line_number, line in enumerate(message.splitlines(), start=1):
        for policy_name, pattern in PROHIBITED_TRAILERS:
            if pattern.match(line):
                violations.append((line_number, policy_name, line.strip()))
                break
    return violations


def _commit_messages(base: str, head: str) -> Iterable[tuple[str, str]]:
    """Yield (commit SHA, commit message) pairs from base (exclusive) to head."""

    if re.fullmatch(r"0+", base):
        raise ValueError(
            "the push has no previous commit; establish the policy baseline "
            "before checking a new history"
        )

    result = subprocess.run(
        [
            "git",
            "log",
            "--reverse",
            "--format=%H%x00%B%x1e",
            f"{base}..{head}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for record in result.stdout.split("\x1e"):
        if not record.strip():
            continue
        commit_sha, message = record.split("\x00", maxsplit=1)
        yield commit_sha, message


def check_range(base: str, head: str) -> list[str]:
    """Return human-readable violations for a Git commit range."""

    failures: list[str] = []
    for commit_sha, message in _commit_messages(base, head):
        for line_number, policy_name, line in find_violations(message):
            failures.append(
                f"{commit_sha}: line {line_number}: {policy_name}: {line}"
            )
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject generated attribution trailers in a Git commit range."
    )
    parser.add_argument("--base", required=True, help="exclusive base commit SHA")
    parser.add_argument("--head", required=True, help="inclusive head commit SHA or ref")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        failures = check_range(args.base, args.head)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"Attribution policy could not inspect the commit range: {exc}", file=sys.stderr)
        return 2

    if failures:
        print("Prohibited generated attribution found in commit messages:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "Move attribution and AI disclosure to the pull request description; "
            "do not add generated or co-author trailers to commits.",
            file=sys.stderr,
        )
        return 1

    print(f"Attribution policy passed for {args.base}..{args.head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

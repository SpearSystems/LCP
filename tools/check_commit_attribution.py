#!/usr/bin/env python3
"""Normalize generated-attribution lines in Git commit messages.

The policy keeps attribution in the pull request description and CLA process.
The tracked commit-msg hook removes generated attribution before Git creates a
commit, while CI reports messages created without the hook without rewriting
pull-request or protected-branch history. Human co-author trailers are kept
unless they identify a configured automation identity.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable


DEFAULT_CONFIG_PATH = Path(".github/attribution-policy.json")
PROHIBITED_TRAILERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "generated attribution",
        re.compile(
            r"^\s*(?:[^\w\s]+\s*)?generated\s+(?:with|by)\b.*$",
            re.IGNORECASE,
        ),
    ),
)
COAUTHOR_TRAILER_PATTERN = re.compile(
    r"^\s*co[- ]?authored[- ]by\s*:\s*(?P<author>.+)$",
    re.IGNORECASE,
)
COAUTHOR_EMAIL_PATTERN = re.compile(r"<(?P<email>[^<>\s]+@[^<>\s]+)>")
DEFAULT_GENERATED_COAUTHOR_NAMES = frozenset({"codebuff"})
DEFAULT_GENERATED_COAUTHOR_EMAILS = frozenset({"noreply@codebuff.com"})
AttributionIdentities = tuple[frozenset[str], frozenset[str]]
DEFAULT_IDENTITIES: AttributionIdentities = (
    DEFAULT_GENERATED_COAUTHOR_NAMES,
    DEFAULT_GENERATED_COAUTHOR_EMAILS,
)


Violation = tuple[int, str, str]


def _configured_values(
    config: dict[str, object],
    key: str,
    defaults: frozenset[str],
) -> frozenset[str]:
    """Read and normalize one configured identity list."""

    if key not in config:
        return defaults

    values = config[key]
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"{key} must be an array of strings")

    normalized = frozenset(value.strip().casefold() for value in values if value.strip())
    if len(normalized) != len(values):
        raise ValueError(f"{key} must not contain empty or duplicate values")
    return normalized


def load_policy_config(config_path: Path) -> AttributionIdentities:
    """Load generated co-author identities from JSON, using safe defaults."""

    if not config_path.exists():
        return DEFAULT_IDENTITIES

    try:
        with config_path.open("r", encoding="utf-8") as file:
            config = json.load(file)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {config_path}: {exc}") from exc

    if not isinstance(config, dict):
        raise ValueError(f"attribution policy must be a JSON object: {config_path}")

    allowed_keys = {"generated_coauthor_names", "generated_coauthor_emails"}
    unknown_keys = set(config) - allowed_keys
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(f"unsupported attribution policy keys: {unknown}")

    return (
        _configured_values(
            config,
            "generated_coauthor_names",
            DEFAULT_GENERATED_COAUTHOR_NAMES,
        ),
        _configured_values(
            config,
            "generated_coauthor_emails",
            DEFAULT_GENERATED_COAUTHOR_EMAILS,
        ),
    )


def _generated_policy(
    line: str,
    identities: AttributionIdentities = DEFAULT_IDENTITIES,
) -> str | None:
    """Return the policy name when a line is generated attribution."""

    for policy_name, pattern in PROHIBITED_TRAILERS:
        if pattern.match(line):
            return policy_name

    coauthor_match = COAUTHOR_TRAILER_PATTERN.match(line)
    if not coauthor_match:
        return None

    generated_names, generated_emails = identities
    author = coauthor_match.group("author").strip()
    email_match = COAUTHOR_EMAIL_PATTERN.search(author)
    if email_match and email_match.group("email").casefold() in generated_emails:
        return "generated co-author attribution"

    display_name = author.split("<", maxsplit=1)[0].strip().casefold()
    if display_name in generated_names:
        return "generated co-author attribution"
    return None


def find_violations(
    message: str,
    identities: AttributionIdentities = DEFAULT_IDENTITIES,
) -> list[Violation]:
    """Return (line number, policy name, offending line) tuples."""

    violations: list[Violation] = []
    for line_number, line in enumerate(message.splitlines(), start=1):
        policy_name = _generated_policy(line, identities)
        if policy_name:
            violations.append((line_number, policy_name, line.strip()))
    return violations


def strip_attributions(
    message: str,
    identities: AttributionIdentities = DEFAULT_IDENTITIES,
) -> str:
    """Remove only generated-attribution lines from a message."""

    violating_line_numbers = {
        line_number for line_number, _, _ in find_violations(message, identities)
    }
    if not violating_line_numbers:
        return message

    return "".join(
        line
        for line_number, line in enumerate(message.splitlines(keepends=True), start=1)
        if line_number not in violating_line_numbers
    )


def normalize_message_file(
    message_file: Path,
    identities: AttributionIdentities = DEFAULT_IDENTITIES,
) -> list[Violation]:
    """Strip generated lines in place and return what was removed."""

    with message_file.open("r", encoding="utf-8", newline="") as file:
        original = file.read()

    violations = find_violations(original, identities)
    if not violations:
        return []

    cleaned = strip_attributions(original, identities)
    with message_file.open("w", encoding="utf-8", newline="") as file:
        file.write(cleaned)
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


def check_range(
    base: str,
    head: str,
    identities: AttributionIdentities = DEFAULT_IDENTITIES,
) -> list[str]:
    """Return human-readable messages that the hook would normalize."""

    failures: list[str] = []
    for commit_sha, message in _commit_messages(base, head):
        for line_number, policy_name, line in find_violations(message, identities):
            failures.append(
                f"{commit_sha}: line {line_number}: {policy_name}: {line}"
            )
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strip generated attribution from a commit message before commit."
    )
    parser.add_argument(
        "--message-file",
        help="commit-msg hook file to normalize in place",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="JSON policy file containing generated co-author identities",
    )
    parser.add_argument("--base", help="exclusive base commit SHA for a non-blocking report")
    parser.add_argument("--head", help="inclusive head commit SHA or ref for a non-blocking report")
    args = parser.parse_args()

    if args.message_file and (args.base or args.head):
        parser.error("--message-file cannot be combined with --base or --head")
    if bool(args.base) != bool(args.head):
        parser.error("--base and --head must be provided together")
    if not args.message_file and not args.base:
        parser.error("provide --message-file or both --base and --head")
    return args


def main() -> int:
    args = parse_args()

    try:
        identities = load_policy_config(Path(args.config))
    except (OSError, ValueError) as exc:
        print(f"Attribution policy could not load: {exc}", file=sys.stderr)
        return 2

    if args.message_file:
        try:
            violations = normalize_message_file(Path(args.message_file), identities)
        except OSError as exc:
            print(f"Commit message could not be normalized: {exc}", file=sys.stderr)
            return 2

        if violations:
            print(
                f"Stripped {len(violations)} generated attribution line(s) "
                "from the commit message."
            )
        return 0

    if re.fullmatch(r"0+", args.base):
        print("No previous commit is available; nothing to normalize.")
        return 0

    try:
        messages_to_normalize = check_range(args.base, args.head, identities)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"Attribution policy could not inspect the commit range: {exc}", file=sys.stderr)
        return 2

    if messages_to_normalize:
        print(
            "Commit messages containing generated attribution were found. "
            "The local commit-msg hook strips these lines before new commits; "
            "existing history is not rewritten."
        )
        for message in messages_to_normalize:
            print(f"  {message}")
    else:
        print(f"Attribution policy report clean for {args.base}..{args.head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

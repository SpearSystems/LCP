#!/usr/bin/env python3
"""Validate the numbered LEP registry and proposal records.

This is a governance consistency gate, not an LEP approval mechanism. It
ensures every numbered proposal is indexed, linked, structurally complete, and
has a status that agrees with the registry. Human review and maintainer
decisions remain mandatory for accepting a proposal.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RELATIVE = Path("governance/LEP-REGISTRY.md")
LEP_DIR_RELATIVE = Path("governance/LEPs")
LEP_ID_PATTERN = re.compile(r"^LEP-(\d{4})$")
LEP_FILE_PATTERN = re.compile(r"^LEP-(\d{4})-.+\.md$")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
STATUS_PATTERN = re.compile(r"-\s+\*\*Status:\*\*\s+\*{0,2}([A-Za-z]+)")

STATUSES = {
    "Discuss",
    "Draft",
    "Review",
    "Accepted",
    "Rejected",
    "Deferred",
    "Implemented",
    "Deprecated",
    "Superseded",
}
TERMINAL_DECISION_STATUSES = {
    "Accepted",
    "Rejected",
    "Deferred",
    "Implemented",
    "Deprecated",
    "Superseded",
}
REQUIRED_METADATA = (
    "Title",
    "Author(s)",
    "Date",
    "Status",
    "Target version",
)
REQUIRED_SECTIONS = (
    "## Problem statement and evidence",
    "## Scope",
    "## Proposed wire shape",
    "## Privacy, security, and universal-core audit",
    "## Compatibility, versioning, and deprecation impact",
    "## SDK, OpenAPI, MCP, conformance, and operational impact",
    "## Alternatives considered",
    "## Rollout, migration, observability, and rollback",
    "## Deferred questions",
)


def _registry_rows(text: str) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    problems: list[str] = []
    header_seen = False
    for line_number, line in enumerate(text.splitlines(), 1):
        if line.startswith("| LEP |"):
            header_seen = True
            continue
        if not line.startswith("| LEP-"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) != 10:
            problems.append(
                f"registry line {line_number} has {len(cells)} columns; expected 10"
            )
            continue
        rows.append(
            {
                "id": cells[0],
                "title": cells[1],
                "status": cells[2],
                "author": cells[3],
                "opened": cells[4],
                "review_deadline": cells[5],
                "decision": cells[6],
                "target_version": cells[7],
                "proposal": cells[8],
                "traceability": cells[9],
            }
        )
    if not header_seen:
        problems.append("registry is missing the canonical LEP table header")
    if not rows:
        problems.append("registry contains no LEP records")
    return rows, problems


def _status(value: str) -> str:
    return value.replace("**", "").split("—", 1)[0].strip().title()


def _validate_document(path: Path, record: dict[str, str], root: Path) -> list[str]:
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path.relative_to(root)}: cannot read proposal ({exc})"]

    for field in REQUIRED_METADATA:
        metadata_pattern = re.compile(
            rf"^\s*-\s+\*\*{re.escape(field)}:\*\*\s*(.+?)\s*$",
            re.MULTILINE,
        )
        match = metadata_pattern.search(text)
        if not match or not match.group(1).strip(" -*"):
            problems.append(f"{path.relative_to(root)}: missing or empty metadata field '{field}'")
    for heading in REQUIRED_SECTIONS:
        if heading not in text:
            problems.append(f"{path.relative_to(root)}: missing required section '{heading}'")

    match = STATUS_PATTERN.search(text)
    if not match:
        problems.append(f"{path.relative_to(root)}: cannot parse declared status")
    else:
        document_status = match.group(1).title()
        registry_status = _status(record["status"])
        if document_status != registry_status:
            problems.append(
                f"{path.relative_to(root)}: status '{document_status}' does not match registry '{registry_status}'"
            )

    expected_id = record["id"]
    if not re.search(rf"^#\s+{re.escape(expected_id)}\b", text, re.MULTILINE):
        problems.append(f"{path.relative_to(root)}: title does not identify {expected_id}")
    return problems


def validate_lep_registry(repo_root: str | Path = ROOT) -> list[str]:
    """Return governance consistency problems for a repository checkout."""
    root = Path(repo_root).resolve()
    registry_path = root / REGISTRY_RELATIVE
    lep_dir = root / LEP_DIR_RELATIVE
    if not registry_path.is_file():
        return [f"missing registry: {REGISTRY_RELATIVE}"]
    if not lep_dir.is_dir():
        return [f"missing proposal directory: {LEP_DIR_RELATIVE}"]

    rows, problems = _registry_rows(registry_path.read_text(encoding="utf-8"))
    records_by_id: dict[str, dict[str, str]] = {}
    numbers: list[int] = []
    for record in rows:
        lep_id = record["id"]
        match = LEP_ID_PATTERN.fullmatch(lep_id)
        if not match:
            problems.append(f"invalid LEP number: {lep_id}")
            continue
        if lep_id in records_by_id:
            problems.append(f"duplicate registry record: {lep_id}")
            continue
        records_by_id[lep_id] = record
        numbers.append(int(match.group(1)))
        registry_status = _status(record["status"])
        if registry_status not in STATUSES:
            problems.append(f"{lep_id}: invalid status '{record['status']}'")
        if registry_status in TERMINAL_DECISION_STATUSES and record["decision"] in {"", "—", "-"}:
            problems.append(f"{lep_id}: {registry_status} record needs a decision/date")
        if registry_status == "Review" and not re.search(r"\d{4}-\d{2}-\d{2}", record["review_deadline"]):
            problems.append(f"{lep_id}: Review record needs an ISO review deadline")

        link = LINK_PATTERN.search(record["proposal"])
        if not link:
            problems.append(f"{lep_id}: registry record has no proposal link")
            continue
        proposal_path = (registry_path.parent / link.group(1).split("#", 1)[0]).resolve()
        expected_dir = (root / LEP_DIR_RELATIVE).resolve()
        if proposal_path.parent != expected_dir:
            problems.append(f"{lep_id}: proposal link must target {LEP_DIR_RELATIVE}")
            continue
        if (
            not LEP_FILE_PATTERN.fullmatch(proposal_path.name)
            or not proposal_path.name.startswith(f"{lep_id}-")
        ):
            problems.append(
                f"{lep_id}: proposal filename must be LEP-NNNN-slug.md and start with '{lep_id}-'"
            )
            continue
        if not proposal_path.is_file():
            problems.append(f"{lep_id}: proposal file is missing: {proposal_path.relative_to(root)}")
            continue
        problems.extend(_validate_document(proposal_path, record, root))

    if numbers:
        expected = list(range(1, max(numbers) + 1))
        if sorted(numbers) != expected:
            problems.append(
                f"LEP numbers must be sequential from 0001; found {sorted(numbers)}"
            )

    for proposal_path in sorted(lep_dir.glob("LEP-*.md")):
        match = LEP_FILE_PATTERN.fullmatch(proposal_path.name)
        if not match:
            problems.append(
                f"proposal filename must be LEP-NNNN-slug.md: {proposal_path.relative_to(root)}"
            )
            continue
        lep_id = f"LEP-{match.group(1)}"
        if lep_id not in records_by_id:
            problems.append(f"proposal is not registered: {proposal_path.relative_to(root)}")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    problems = validate_lep_registry(args.repo_root)
    if problems:
        for problem in problems:
            print(f"LEP registry error: {problem}")
        return 1
    count = len(list((Path(args.repo_root).resolve() / LEP_DIR_RELATIVE).glob("LEP-*.md")))
    print(f"LEP registry validation passed: {count} proposal(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

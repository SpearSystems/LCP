#!/usr/bin/env python3
"""Lint the Mermaid blocks that ship in the public Markdown.

GitHub renders Mermaid client-side and reports "Unable to render rich display"
only *after* a reader opens the page, so a syntax slip in a capability diagram
is invisible to every test we run. This checker catches the failure classes we
have actually hit and the ones the grammar makes easy to hit:

- ``;`` inside a block (Mermaid's statement separator, illegal mid-label);
- a literal ``\\n`` inside a note (GitHub needs ``<br/>``);
- unbalanced ``alt``/``loop``/``opt``/``par``/``critical``/``break`` blocks;
- a ``Note over A,B`` naming a participant that was never declared;
- an odd number of double quotes on a line.

``ponytail:`` this is a syntax-hazard linter, not a Mermaid parser. If the
diagrams grow past sequence/flowchart basics, replace it with a mermaid-cli
render gate rather than extending the regexes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
CONTAINER_OPEN = re.compile(r"^\s*(alt|loop|opt|par|critical|break|subgraph)\b")
CONTAINER_END = re.compile(r"^\s*end\s*$")
PARTICIPANT = re.compile(r"^\s*(?:participant|actor)\s+(\w+)")
NOTE = re.compile(r"^\s*Note\s+(?:over|left of|right of)\s+([\w,\s]+):")
SEQUENCE = re.compile(r"^\s*sequenceDiagram\b", re.MULTILINE)

MARKDOWN_ROOTS = ("README.md", "docs", "spec", "governance")


def markdown_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for entry in MARKDOWN_ROOTS:
        path = root / entry
        if path.is_file():
            files.append(path)
            continue
        if path.is_dir():
            files.extend(p for p in sorted(path.rglob("*.md")) if p.is_file())
    return files


def block_problems(block: str) -> list[str]:
    problems: list[str] = []
    sequence = bool(SEQUENCE.search(block))
    participants: set[str] = set()
    if sequence:
        participants = {
            match.group(1)
            for match in (PARTICIPANT.match(line) for line in block.splitlines())
            if match
        }
    opened = 0
    for number, line in enumerate(block.splitlines(), 1):
        if ";" in line:
            problems.append(
                f"line {number}: `;` is Mermaid's statement separator and cannot "
                f"appear inside a label — {line.strip()}"
            )
        if "\\n" in line:
            problems.append(
                f"line {number}: literal `\\n` does not break lines on GitHub; "
                f"use `<br/>` — {line.strip()}"
            )
        if line.count('"') % 2:
            problems.append(f"line {number}: unbalanced double quote — {line.strip()}")
        if CONTAINER_OPEN.match(line):
            opened += 1
        elif CONTAINER_END.match(line):
            opened -= 1
            if opened < 0:
                problems.append(f"line {number}: `end` without a matching block")
    if opened:
        problems.append(f"unbalanced blocks: {opened} container(s) never closed")
    if sequence:
        for number, line in enumerate(block.splitlines(), 1):
            match = NOTE.match(line)
            if not match:
                continue
            for name in (part.strip() for part in match.group(1).split(",")):
                if name and name not in participants:
                    problems.append(
                        f"line {number}: note names undeclared participant {name!r}"
                    )
    return problems


def check_root(root: Path) -> list[str]:
    problems: list[str] = []
    for path in markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for index, block in enumerate(BLOCK.findall(text), 1):
            for problem in block_problems(block):
                problems.append(f"{path.relative_to(root)}: block {index}: {problem}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    problems = check_root(args.root)
    if problems:
        print("mermaid lint failed:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    files = markdown_files(args.root)
    blocks = sum(len(BLOCK.findall(p.read_text(encoding="utf-8"))) for p in files)
    print(
        f"mermaid lint passed: {blocks} block(s) across {len(files)} Markdown file(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

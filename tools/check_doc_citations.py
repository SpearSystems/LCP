#!/usr/bin/env python3
"""Docs-citation drift checker (ARB plan §3.4, lane W1-CI-Checkers).

Governance dossiers and SPEC appendices cite code as ``path:line`` or
``path:symbol``. Docs drift when code moves underneath them; a stale
"verified" citation in a public repo is a defect (plan §0.4). This checker
regex-extracts every such citation from SPEC.md, docs/*.md, and
governance/LEPs/*.md and verifies it against the current tree:

- the cited file must exist (repo-root-relative, then document-relative);
- ``path:123`` citations need the file to have at least that many lines;
- ``path:SomeSymbol`` citations need the identifier to appear in the file
  (full token first, then the last dot-segment).

Fenced code blocks are skipped so example text never counts as a citation.

Advisory by design this wave (plan grace note: warn now, block after one
release): exit code is 0 even when drift is found, and the summary lists
every broken citation. ``--strict`` is the W5 flip switch (exit 1 on drift).

Exit codes: 0 = advisory run, 1 = broken citations with --strict,
2 = usage/environment error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOC_SETS = ("SPEC.md", "docs", "governance/LEPs")

EXTS = "cjs cs go java js json kt md mjs php proto py rb rs sh sql toml ts tsx yaml yml"
# path:line | path#Lline | path:symbol — path must look like a source/doc file.
CITATION_RE = re.compile(
    r"(?<!/)(?P<path>[A-Za-z0-9_.\-/]+\.(?:" + EXTS.replace(" ", "|") + r"))"
    r"(?::(?P<line>(?!\d{4}-)\d+)(?:-(?P<line_end>\d+))?(?![\d-])"
    r"|#L(?P<gh_line>\d+))"
    r"|(?<!/)(?P<path2>[A-Za-z0-9_.\-/]+\.(?:" + EXTS.replace(" ", "|") + r"))"
    r":(?P<symbol>[A-Za-z_][A-Za-z0-9_.]{2,}(?:\(\))?)",
)
FENCE_RE = re.compile(r"^(```|~~~)")
SKIP_DIRS = {
    ".build",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


@dataclass(frozen=True)
class Citation:
    doc: str
    line: int
    path: str
    target: str  # line number as text, or symbol

    def __str__(self) -> str:
        return f"{self.doc}:{self.line} -> {self.path}:{self.target}"


def collect_docs(root: Path) -> list[Path]:
    docs: list[Path] = []
    for entry in DOC_SETS:
        base = root / entry
        if base.is_file():
            docs.append(base)
        elif base.is_dir():
            docs.extend(sorted(base.glob("*.md")))
    return docs


def extract_citations(doc: Path, text: str) -> list[Citation]:
    """Pull citations from a document, skipping fenced code blocks."""
    rel = str(doc)
    found: list[Citation] = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in CITATION_RE.finditer(line):
            path = match.group("path") or match.group("path2")
            if path.startswith("/"):
                continue  # URL tail or machine-absolute path, not a repo citation
            line = match.group("line")
            target = (
                (match.group("line_end") or line)
                if line
                else match.group("gh_line") or match.group("symbol") or ""
            )
            found.append(Citation(rel, lineno, path, target))
    return found


def resolve_path(cit: Citation, root: Path) -> list[Path]:
    """Candidate files for a citation: root-relative, document-relative,
    then bare-filename match against the tree (LEP evidence tables cite
    ``client.py:64`` without the directory)."""
    direct = root / cit.path
    if direct.is_file():
        return [direct]
    sibling = (root / cit.doc).parent / cit.path
    if sibling.is_file():
        return [sibling]
    if "/" not in cit.path:
        return _basename_index(root).get(cit.path, [])
    return []


_INDEX_CACHE: dict[str, dict[str, list[Path]]] = {}


def _basename_index(root: Path) -> dict[str, list[Path]]:
    key = str(root)
    if key not in _INDEX_CACHE:
        index: dict[str, list[Path]] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                index.setdefault(name, []).append(Path(dirpath) / name)
        _INDEX_CACHE[key] = index
    return _INDEX_CACHE[key]


def verify(cit: Citation, root: Path) -> str | None:
    """Return None if any candidate satisfies the citation, else a reason."""
    candidates = resolve_path(cit, root)
    if not candidates:
        return f"{cit.path} not found in tree"
    if cit.target.isdigit():
        wanted = int(cit.target)
        for path in candidates:
            if (
                len(path.read_text(encoding="utf-8", errors="replace").splitlines())
                >= wanted
            ):
                return None
        return f"no copy of {cit.path} has {wanted} lines"
    token = cit.target.removesuffix("()")
    pattern = re.compile(rf"\b{re.escape(token)}\b")
    tail = re.compile(rf"\b{re.escape(token.rsplit('.', 1)[-1])}\b")
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="replace")
        if pattern.search(text) or (
            tail.pattern != pattern.pattern and tail.search(text)
        ):
            return None
    return f"symbol {cit.target!r} not present in {cit.path}"


def check_doc_citations(root: Path = ROOT) -> tuple[int, list[str]]:
    """Return (total citations checked, broken-reason lines)."""
    total = 0
    broken: list[str] = []
    for doc in collect_docs(root):
        rel = doc.relative_to(root).as_posix()
        for cit in extract_citations(
            doc, doc.read_text(encoding="utf-8", errors="replace")
        ):
            cit = Citation(rel, cit.line, cit.path, cit.target)
            total += 1
            reason = verify(cit, root)
            if reason:
                broken.append(f"{cit}: {reason}")
    return total, broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify path:line / path:symbol citations in docs"
    )
    parser.add_argument(
        "--root", default=str(ROOT), help="repository root (default: this checkout)"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 on drift (W5 flip; advisory by default)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        total, broken = check_doc_citations(root)
    except OSError as err:
        print(f"doc-citations: environment error: {err}", file=sys.stderr)
        return 2
    for line in broken:
        print(f"  drift  {line}")
    mode = "strict" if args.strict else "advisory"
    print(
        f"doc-citation-drift: {total} citation(s) checked, {len(broken)} broken [{mode}]"
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Docs citation drift ({mode})\n")
            handle.write(f"- {total} citations checked, {len(broken)} broken\n")
            for line in broken[:50]:
                handle.write(f"- {line}\n")
            if len(broken) > 50:
                handle.write(f"- … {len(broken) - 50} more\n")
    return 1 if (broken and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())

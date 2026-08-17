"""Public markdown links and anchors must resolve.

Scans public Markdown files throughout the repository, excluding local/tooling
and build artifacts: each relative link must point at an existing file, and
each ``#anchor`` must match the GitHub slug of a heading in the target file.
Runs as part of the tooling tests, so CI catches broken docs links
automatically.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXCLUDED_PARTS = {
    "node_modules",
    "target",
    ".gradle",
    ".build",
    ".venv",
    "vendor",
    "__pycache__",
    "dist",
    "coverage",
    ".git",
    ".gitnexus",
    ".claude",
    ".freebuff",
}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^```|^~~~")


def _github_slug(heading: str) -> str:
    """Approximate GitHub's anchor slug for a heading line."""
    text = heading.strip().lower()
    cleaned = re.sub(r"[^a-z0-9 _-]", "", text)
    return cleaned.replace(" ", "-")


def _lines_outside_fences(text: str) -> list[tuple[int, str]]:
    """Return (1-based line number, line) pairs outside fenced code blocks."""
    result: list[tuple[int, str]] = []
    in_fence = False
    for line_number, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if not in_fence:
            result.append((line_number, line))
    return result


def _markdown_files() -> list[Path]:
    """Return public Markdown files while excluding local/tooling artifacts."""
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*.md")):
        relative = path.relative_to(ROOT)
        if relative.name in {"AGENTS.md", "CLAUDE.md"}:
            continue
        if not any(part in EXCLUDED_PARTS for part in relative.parts):
            files.append(path)
    return files


def _heading_slugs(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    slugs: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)$", line)
        if match:
            slugs.add(_github_slug(match.group(1)))
    return slugs


def check_doc_links(root: Path = ROOT) -> list[str]:
    """Return a list of broken-link problems across the public docs."""
    problems: list[str] = []
    heading_cache: dict[Path, set[str]] = {}
    for source in _markdown_files():
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{source}: unreadable ({exc})")
            continue
        source_slugs = _heading_slugs(source)
        for line_number, line in _lines_outside_fences(text):
            for match in LINK_RE.finditer(line):
                target = match.group(1).strip()
                if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                if "#" in target:
                    path_part, anchor = target.rsplit("#", 1)
                else:
                    path_part, anchor = target, None
                if not path_part:
                    continue
                target_path = (source.parent / path_part).resolve()
                if ROOT.resolve() not in target_path.parents and target_path != ROOT.resolve():
                    problems.append(
                        f"{source}:{line_number}: link '{target}' escapes the repository root"
                    )
                    continue
                if not target_path.exists():
                    problems.append(f"{source}:{line_number}: link '{target}' file not found")
                    continue
                if anchor:
                    if target_path not in heading_cache:
                        heading_cache[target_path] = _heading_slugs(target_path)
                    if anchor not in heading_cache[target_path]:
                        problems.append(
                            f"{source}:{line_number}: link '{target}' anchor "
                            f"#{anchor} not found in {target_path.relative_to(ROOT)}"
                        )
            # Same-file '#anchor' links.
            for match in LINK_RE.finditer(line):
                target = match.group(1).strip()
                if target.startswith("#") and len(target) > 1:
                    anchor = target[1:]
                    if anchor not in source_slugs:
                        problems.append(
                            f"{source}:{line_number}: link '{target}' anchor not found"
                        )
    return problems


class DocLinkTests(unittest.TestCase):
    def test_github_slug_matches_expected_anchors(self) -> None:
        self.assertEqual(_github_slug("MCP relationship"), "mcp-relationship")
        self.assertEqual(_github_slug("3. Publish an offer"), "3-publish-an-offer")
        self.assertEqual(_github_slug("Validate a real Postgres deployment"),
                         "validate-a-real-postgres-deployment")

    def test_all_public_doc_links_resolve(self) -> None:
        problems = check_doc_links()
        self.assertEqual([], problems, "broken doc links:\n" + "\n".join(problems))

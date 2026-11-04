#!/usr/bin/env python3
"""Build the deterministic static specification site (W6-02, decision D10).

The site is generated from the normative sources and never hand-edited:
``SPEC.md`` (the index), ``spec/*.md`` (the normative text), ``schemas/*.json``,
``verticals/*.json``, and ``governance/LEPs/*.md``.  Every page carries its
source path and the build hash of the source set, and every internal link is
checked against the generated output before anything is written.

Rendering is intentionally dependency-free: ``requirements-ci.txt`` pins no
Markdown renderer, so this tool implements the small subset the specification
actually uses — ATX headings, paragraphs, ordered/unordered lists (nested by
indentation), blockquotes, tables with alignment, fenced code, inline code,
bold/italic, links, and horizontal rules.  Unsupported constructs are stated
as limitations rather than silently mangled:

*  no setext (underlined) headings — a lone ``---`` line is a horizontal rule;
*  no indented (four-space) code blocks — every code block must be fenced;
*  no reference-style links, footnotes, raw HTML, or ``_italic_`` emphasis;
*  images are not rendered (the normative sources contain none);
*  links to repository files outside the published source set are rendered as
  text with their repository path, because the site publishes the normative
  documents only.

Output is committed under ``site/`` so a publish artefact is reviewable in a
diff.  ``--check`` regenerates in memory and fails when ``site/`` is stale,
which is what keeps a hand-edited page out of the tree.

Usage::

    python3 tools/build_spec_site.py            # write site/
    python3 tools/build_spec_site.py --check    # fail if site/ is stale
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = "tools/build_spec_site.py"
DEFAULT_OUTPUT = ROOT / "site"
BUILD_HASH_LENGTH = 12
# Editor/OS droppings the output tree may acquire; never generated, never stale.
IGNORED_OUTPUT_NAMES = frozenset({".DS_Store"})

LEP_DIR = "governance/LEPs"
SCHEMA_DIR = "schemas"
VERTICAL_DIR = "verticals"
SPEC_DIR = "spec"

LINK_SENTINEL = "\x01"
CODE_SENTINEL = "\x00"
CODE_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")

FENCE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[A-Za-z0-9_+.-]*)[ \t]*$"
)
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<text>.*?)[ \t]*#*[ \t]*$")
HR_RE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$")
LIST_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|\d{1,9}[.)])[ \t]+(?P<rest>.*)$"
)
QUOTE_RE = re.compile(r"^[ \t]*>[ \t]?(?P<rest>.*)$")
TABLE_SEPARATOR_RE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$"
)
LINK_RE = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)\)")
BOLD_RE = re.compile(r"\*\*(?P<body>.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(?P<body>[^*]+?)\*(?!\*)")
ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")

NAV_ENTRIES = (
    ("index.html", "LCP"),
    ("spec/index.html", "Specification"),
    ("schemas/index.html", "Schemas"),
    ("verticals/index.html", "Verticals"),
    ("leps/index.html", "LEPs"),
)

STYLE = """\
:root {
  color-scheme: light dark;
  --fg: #16181d;
  --muted: #5c6270;
  --bg: #ffffff;
  --panel: #f4f5f7;
  --rule: #d9dce2;
  --accent: #1c5d99;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #e6e8ec;
    --muted: #9aa1ae;
    --bg: #14161a;
    --panel: #1c1f25;
    --rule: #2c313a;
    --accent: #7ab6ea;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: var(--fg);
  background: var(--bg);
}
a { color: var(--accent); }
header.site {
  border-bottom: 1px solid var(--rule);
  background: var(--panel);
  padding: 0.75rem 1.5rem;
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  align-items: baseline;
}
header.site nav { display: flex; flex-wrap: wrap; gap: 0.9rem; font-size: 0.9rem; }
header.site .brand { font-weight: 600; letter-spacing: 0.01em; }
.layout { display: flex; gap: 2rem; max-width: 78rem; margin: 0 auto; padding: 1.5rem; }
main { min-width: 0; flex: 1 1 auto; }
aside.toc { flex: 0 0 16rem; font-size: 0.85rem; position: sticky; top: 1rem; align-self: flex-start; max-height: calc(100vh - 2rem); overflow: auto; }
aside.toc h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
aside.toc ul { list-style: none; padding-left: 0; margin: 0; }
aside.toc li.depth-3 { padding-left: 1rem; }
h1, h2, h3, h4 { line-height: 1.25; margin: 2rem 0 0.75rem; }
h1 { font-size: 1.9rem; margin-top: 0.5rem; }
h2 { font-size: 1.35rem; border-bottom: 1px solid var(--rule); padding-bottom: 0.25rem; }
h3 { font-size: 1.1rem; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.88em; background: var(--panel); padding: 0.1em 0.3em; border-radius: 3px; }
pre.code-block { background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; padding: 0.9rem 1rem; overflow: auto; }
pre.code-block code { background: none; padding: 0; font-size: 0.85rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-size: 0.92rem; }
th, td { border: 1px solid var(--rule); padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }
th { background: var(--panel); }
blockquote { margin: 1rem 0; padding: 0.25rem 1rem; border-left: 3px solid var(--accent); color: var(--muted); }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2rem 0; }
footer.page { border-top: 1px solid var(--rule); margin-top: 3rem; padding: 1rem 1.5rem 2rem; font-size: 0.8rem; color: var(--muted); max-width: 78rem; margin-left: auto; margin-right: auto; }
.unpublished { color: var(--muted); }
.unpublished code { background: none; }
@media (max-width: 900px) {
  .layout { flex-direction: column-reverse; }
  aside.toc { position: static; max-height: none; flex: 0 0 auto; }
}
"""


class BuildError(Exception):
    """A condition that must fail the build (missing source, broken link)."""


@dataclass
class Source:
    """One normative file that feeds the site."""

    path: str  # repository-relative, posix
    kind: str  # "markdown" | "json"
    out_path: str  # site-relative page path
    text: str
    digest: str


@dataclass
class BuildStats:
    """Counts and notes reported after a render."""

    build_hash: str
    pages: int
    sources: int
    out_of_scope: list[str]


@dataclass
class Page:
    """One generated page."""

    source: str
    out_path: str
    title: str
    body: str = ""
    anchors: set[str] = field(default_factory=set)
    headings: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass
class PendingLink:
    """A link recorded during rendering, resolved after every page exists."""

    page: str  # output page that carries the link
    source: str  # repository-relative source the relative path resolves against
    label: str
    target: str


# --------------------------------------------------------------------------
# Markdown subset
# --------------------------------------------------------------------------


def github_slug(text: str) -> str:
    """Approximate GitHub's anchor slug, matching tools/tests/test_doc_links.py."""
    cleaned = re.sub(r"[^a-z0-9 _-]", "", text.strip().lower())
    return cleaned.replace(" ", "-")


def _strip_inline_markup(text: str) -> str:
    """Remove inline Markdown markers so a heading slug matches the visible text."""
    text = LINK_RE.sub(lambda match: match.group("label"), text)
    text = text.replace("**", "").replace("`", "")
    return text.replace("*", "")


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _split_row(line: str) -> list[str]:
    """Split a table row on pipes that are not inside a code span."""
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    for char in row:
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append("".join(current))
            current = []
            continue
        current.append(char)
    cells.append("".join(current))
    return [cell.strip() for cell in cells]


def _is_block_start(line: str) -> bool:
    return bool(
        FENCE_RE.match(line)
        or HEADING_RE.match(line)
        or HR_RE.match(line)
        or LIST_RE.match(line)
        or QUOTE_RE.match(line)
    )


class MarkdownRenderer:
    """Render the Markdown subset used by the specification into HTML."""

    def __init__(self, page: Page, pending: list[PendingLink]) -> None:
        self.page = page
        self.pending = pending

    # -- inline -------------------------------------------------------------

    def inline(self, text: str) -> str:
        """Render inline Markdown: code spans, links, bold and italic.

        Code spans are replaced with placeholders before escaping so that
        emphasis and links may wrap them (``**`x`**``, ``[`x`](url)``).
        """
        codes: list[str] = []
        pieces: list[str] = []
        index = 0
        while index < len(text):
            if text[index] == "`":
                run_end = index
                while run_end < len(text) and text[run_end] == "`":
                    run_end += 1
                run = text[index:run_end]
                close = text.find(run, run_end)
                if close != -1:
                    code = text[run_end:close]
                    if (
                        len(code) > 1
                        and code.startswith(" ")
                        and code.endswith(" ")
                        and code.strip()
                    ):
                        code = code[1:-1]
                    pieces.append(f"{CODE_SENTINEL}{len(codes)}{CODE_SENTINEL}")
                    codes.append(html.escape(code, quote=False))
                    index = close + len(run)
                    continue
            pieces.append(text[index])
            index += 1

        escaped = html.escape("".join(pieces), quote=False)
        rendered = self._inline_text(escaped, codes)
        return CODE_PLACEHOLDER_RE.sub(
            lambda match: f"<code>{codes[int(match.group(1))]}</code>", rendered
        )

    def _inline_text(self, escaped: str, codes: list[str]) -> str:
        """Apply links and emphasis to already-escaped text.

        A link label is captured before the body substitutes code spans, so the
        label's own code placeholders are expanded here.
        """

        def expand(text: str) -> str:
            return CODE_PLACEHOLDER_RE.sub(
                lambda match: f"<code>{codes[int(match.group(1))]}</code>", text
            )

        def record_link(match: re.Match[str]) -> str:
            self.pending.append(
                PendingLink(
                    page=self.page.out_path,
                    source=self.page.source,
                    label=expand(self._inline_emphasis(match.group("label"))),
                    target=match.group("target"),
                )
            )
            return f"{LINK_SENTINEL}{len(self.pending) - 1}{LINK_SENTINEL}"

        return self._inline_emphasis(LINK_RE.sub(record_link, escaped))

    @staticmethod
    def _inline_emphasis(text: str) -> str:
        text = BOLD_RE.sub(
            lambda match: f"<strong>{match.group('body')}</strong>", text
        )
        return ITALIC_RE.sub(lambda match: f"<em>{match.group('body')}</em>", text)

    # -- blocks -------------------------------------------------------------

    def render(self, lines: list[str]) -> str:
        blocks: list[str] = []
        index = 0
        total = len(lines)
        while index < total:
            line = lines[index]
            if not line.strip():
                index += 1
                continue

            fence = FENCE_RE.match(line)
            if fence:
                block, index = self._fence(lines, index, fence)
                blocks.append(block)
                continue

            heading = HEADING_RE.match(line)
            if heading:
                blocks.append(self._heading(heading))
                index += 1
                continue

            if HR_RE.match(line):
                blocks.append("<hr>")
                index += 1
                continue

            if (
                "|" in line
                and index + 1 < total
                and TABLE_SEPARATOR_RE.match(lines[index + 1])
            ):
                block, index = self._table(lines, index)
                blocks.append(block)
                continue

            if QUOTE_RE.match(line):
                block, index = self._quote(lines, index)
                blocks.append(block)
                continue

            if LIST_RE.match(line):
                block, index = self._list(lines, index)
                blocks.append(block)
                continue

            block, index = self._paragraph(lines, index)
            blocks.append(block)
        return "\n".join(block for block in blocks if block) + ("\n" if blocks else "")

    def _fence(
        self, lines: list[str], index: int, fence: re.Match[str]
    ) -> tuple[str, int]:
        marker = fence.group("fence")
        language = fence.group("lang")
        index += 1
        body: list[str] = []
        while index < len(lines):
            closing = FENCE_RE.match(lines[index])
            if (
                closing
                and closing.group("fence")[0] == marker[0]
                and len(closing.group("fence")) >= len(marker)
            ):
                index += 1
                break
            body.append(lines[index])
            index += 1
        class_attribute = (
            f' class="language-{html.escape(language, quote=True)}"' if language else ""
        )
        code = html.escape("\n".join(body), quote=False)
        return (
            f'<pre class="code-block"><code{class_attribute}>{code}</code></pre>',
            index,
        )

    def _heading(self, match: re.Match[str]) -> str:
        level = len(match.group("hashes"))
        text = match.group("text")
        plain = _strip_inline_markup(text)
        slug = github_slug(plain)
        candidate = slug
        suffix = 1
        while candidate in self.page.anchors:
            candidate = f"{slug}-{suffix}"
            suffix += 1
        self.page.anchors.add(candidate)
        if level >= 2:
            self.page.headings.append((level, candidate, plain))
        return f'<h{level} id="{candidate}">{self.inline(text)}</h{level}>'

    def _table(self, lines: list[str], index: int) -> tuple[str, int]:
        header = _split_row(lines[index])
        separators = _split_row(lines[index + 1])
        aligns: list[str] = []
        for separator in separators:
            left = separator.startswith(":")
            right = separator.endswith(":")
            if left and right:
                aligns.append("center")
            elif right:
                aligns.append("right")
            elif left:
                aligns.append("left")
            else:
                aligns.append("")
        rows: list[list[str]] = []
        index += 2
        while index < len(lines) and lines[index].strip() and "|" in lines[index]:
            rows.append(_split_row(lines[index]))
            index += 1

        def cell(tag: str, text: str, column: int) -> str:
            align = aligns[column] if column < len(aligns) else ""
            attribute = f' style="text-align: {align}"' if align and tag == "td" else ""
            return f"<{tag}{attribute}>{self.inline(text)}</{tag}>"

        out = ["<table>", "<thead>", "<tr>"]
        out.extend(cell("th", text, column) for column, text in enumerate(header))
        out.extend(["</tr>", "</thead>", "<tbody>"])
        for row in rows:
            out.append("<tr>")
            out.extend(cell("td", text, column) for column, text in enumerate(row))
            out.append("</tr>")
        out.extend(["</tbody>", "</table>"])
        return "\n".join(out), index

    def _quote(self, lines: list[str], index: int) -> tuple[str, int]:
        inner: list[str] = []
        while index < len(lines):
            match = QUOTE_RE.match(lines[index])
            if not match:
                break
            inner.append(match.group("rest"))
            index += 1
        return f"<blockquote>\n{self.render(inner)}</blockquote>", index

    def _list(self, lines: list[str], index: int) -> tuple[str, int]:
        first = LIST_RE.match(lines[index])
        assert first is not None
        base_indent = _indent_width(first.group("indent"))
        ordered = first.group("marker")[0].isdigit()
        start = int(re.sub(r"\D", "", first.group("marker"))) if ordered else 1
        items: list[str] = []
        total = len(lines)

        while index < total:
            match = LIST_RE.match(lines[index])
            if not match:
                break
            if _indent_width(match.group("indent")) != base_indent:
                break
            if match.group("marker")[0].isdigit() != ordered:
                break
            content_indent = match.start("rest")
            item_lines = [match.group("rest")]
            index += 1
            while index < total:
                line = lines[index]
                if not line.strip():
                    lookahead = index + 1
                    while lookahead < total and not lines[lookahead].strip():
                        lookahead += 1
                    if (
                        lookahead < total
                        and _indent_width(lines[lookahead]) > base_indent
                    ):
                        item_lines.append("")
                        index += 1
                        continue
                    break
                if _indent_width(line) <= base_indent:
                    break
                item_lines.append(line[min(_indent_width(line), content_indent) :])
                index += 1
            items.append(self._list_item(self.render(item_lines)))

        tag = "ol" if ordered else "ul"
        start_attribute = f' start="{start}"' if ordered and start != 1 else ""
        body = "\n".join(f"<li>{item}</li>" for item in items)
        return f"<{tag}{start_attribute}>\n{body}\n</{tag}>", index

    @staticmethod
    def _list_item(rendered: str) -> str:
        match = re.fullmatch(r"<p>(.*)</p>\n?", rendered, re.S)
        return match.group(1) if match else rendered.strip()

    def _paragraph(self, lines: list[str], index: int) -> tuple[str, int]:
        parts: list[str] = []
        total = len(lines)
        while index < total and lines[index].strip():
            if parts and _is_block_start(lines[index]):
                break
            parts.append(lines[index].strip())
            index += 1
        return f"<p>{self.inline(' '.join(parts))}</p>", index


def render_markdown(source: Source, pending: list[PendingLink]) -> tuple[Page, str]:
    """Render one Markdown source into a page body and its heading index."""
    text = source.text
    title = source.path
    for line in text.splitlines():
        heading = HEADING_RE.match(line)
        if heading and len(heading.group("hashes")) == 1:
            title = _strip_inline_markup(heading.group("text")).strip()
            break
    page = Page(source=source.path, out_path=source.out_path, title=title)
    renderer = MarkdownRenderer(page, pending)
    page.body = renderer.render(text.splitlines())
    return page, page.body


# --------------------------------------------------------------------------
# JSON schema pages
# --------------------------------------------------------------------------


def _code(value: object) -> str:
    return f"<code>{html.escape(str(value), quote=False)}</code>"


def _schema_type(schema: dict[str, Any]) -> str:
    for key in ("$ref", "const", "enum", "oneOf", "anyOf", "allOf", "type", "format"):
        if key in schema:
            value = schema[key]
            if key == "$ref":
                return "ref " + _code(value)
            if key == "enum":
                members = ", ".join(
                    _code(json.dumps(item, ensure_ascii=False)) for item in value
                )
                return f"enum ({members})"
            if key in {"oneOf", "anyOf", "allOf"}:
                return key
            if key == "const":
                return "const " + _code(json.dumps(value, ensure_ascii=False))
            return _code(value)
    return "untyped"


def _schema_summary(schema: dict[str, Any]) -> str:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return ""
    required = set(schema.get("required", []) or [])
    has_pii = any(
        isinstance(spec, dict) and "x-lcp-pii" in spec for spec in properties.values()
    )
    has_ping = any(
        isinstance(spec, dict) and "ping_safe" in spec for spec in properties.values()
    )
    columns = ["Property", "Type"]
    if has_pii:
        columns.append("PII class")
    if has_ping:
        columns.append("Ping-safe")
    columns.extend(["Required", "Description"])

    rows: list[str] = []
    for name in sorted(properties):
        spec = properties[name] if isinstance(properties[name], dict) else {}
        cells = [f"<code>{html.escape(name)}</code>", _schema_type(spec)]
        if has_pii:
            cells.append(_code(spec.get("x-lcp-pii", "—")))
        if has_ping:
            value = spec.get("ping_safe")
            cells.append("yes" if value is True else "no" if value is False else "—")
        cells.append("yes" if name in required else "")
        cells.append(html.escape(str(spec.get("description", "")), quote=False))
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")

    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    return (
        '<h2 id="properties">Top-level properties</h2>\n'
        "<table>\n<thead>\n"
        f"<tr>{head}</tr>\n"
        "</thead>\n<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>"
    )


def render_schema(source: Source) -> Page:
    """Render a JSON Schema source into a page with a summary and full document."""
    try:
        schema = json.loads(source.text)
    except json.JSONDecodeError as error:
        raise BuildError(f"{source.path}: invalid JSON ({error})") from error
    if not isinstance(schema, dict):
        raise BuildError(f"{source.path}: a schema must be a JSON object")

    title = str(schema.get("title") or source.path)
    description = schema.get("description")
    parts: list[str] = [f'<h1 id="schema">{html.escape(title)}</h1>']
    meta: list[str] = []
    if schema.get("$id"):
        meta.append(f"<code>{html.escape(str(schema['$id']))}</code>")
    if isinstance(description, str) and description:
        meta.append(html.escape(description, quote=False))
    if meta:
        parts.append("<p>" + " · ".join(meta) + "</p>")
    summary = _schema_summary(schema)
    if summary:
        parts.append(summary)
    document = json.dumps(schema, indent=2, ensure_ascii=False) + "\n"
    parts.append('<h2 id="document">Document</h2>')
    parts.append(
        '<pre class="code-block"><code class="language-json">'
        + html.escape(document, quote=False)
        + "</code></pre>"
    )
    page = Page(source=source.path, out_path=source.out_path, title=title)
    page.body = "\n".join(parts) + "\n"
    page.anchors = set(re.findall(r'id="([^"]+)"', page.body))
    page.headings = [(2, "properties", "Top-level properties")] if summary else []
    page.headings.append((2, "document", "Document"))
    return page


# --------------------------------------------------------------------------
# Source collection
# --------------------------------------------------------------------------


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def collect_sources() -> list[Source]:
    """Collect the normative sources, failing when any expected input is absent."""
    missing: list[str] = []
    entries: list[tuple[Path, str, str]] = []

    index_path = ROOT / "SPEC.md"
    if index_path.is_file():
        entries.append((index_path, "markdown", "index.html"))
    else:
        missing.append("SPEC.md")

    spec_files = sorted((ROOT / SPEC_DIR).glob("*.md"))
    if not spec_files:
        missing.append(f"{SPEC_DIR}/*.md")
    for path in spec_files:
        if path.name == "README.md":
            entries.append((path, "markdown", "spec/index.html"))
        else:
            entries.append((path, "markdown", f"{SPEC_DIR}/{path.stem}.html"))

    for directory, kind in ((SCHEMA_DIR, "json"), (VERTICAL_DIR, "json")):
        files = sorted((ROOT / directory).glob("*.json"))
        if not files:
            missing.append(f"{directory}/*.json")
        for path in files:
            entries.append((path, kind, f"{directory}/{path.stem}.html"))

    lep_files = sorted((ROOT / LEP_DIR).glob("*.md"))
    if not lep_files:
        missing.append(f"{LEP_DIR}/*.md")
    for path in lep_files:
        entries.append((path, "markdown", f"leps/{path.stem}.html"))

    if missing:
        raise BuildError("missing source(s): " + ", ".join(sorted(missing)))

    sources: list[Source] = []
    for path, kind, out_path in entries:
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        sources.append(
            Source(
                path=_relative(path),
                kind=kind,
                out_path=out_path,
                text=text,
                digest=digest,
            )
        )
    return sources


def build_hash(sources: list[Source]) -> str:
    """Digest of the whole source set — stable across machines and rebuilds."""
    accumulator = hashlib.sha256()
    accumulator.update(f"{GENERATOR}\n".encode())
    for source in sorted(sources, key=lambda item: item.path):
        accumulator.update(f"{source.path}:{source.digest}\n".encode())
    return accumulator.hexdigest()[:BUILD_HASH_LENGTH]


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


def _prefix(out_path: str) -> str:
    depth = out_path.count("/")
    return "../" * depth


def _layout(page: Page, build: str) -> str:
    prefix = _prefix(page.out_path)
    nav = "".join(
        f'<a href="{prefix}{href}">{html.escape(label)}</a>'
        for href, label in NAV_ENTRIES
    )
    toc = ""
    if page.headings:
        items = "".join(
            f'<li class="depth-{level}"><a href="#{anchor}">{html.escape(text)}</a></li>'
            for level, anchor, text in page.headings
        )
        toc = f'<aside class="toc" aria-label="On this page">\n<h2>On this page</h2>\n<ul>\n{items}\n</ul>\n</aside>\n'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="lcp-source" content="{html.escape(page.source, quote=True)}">
<meta name="lcp-build" content="{build}">
<title>{html.escape(page.title)} — LCP</title>
<link rel="stylesheet" href="{prefix}assets/site.css">
</head>
<body>
<header class="site">
<span class="brand">LCP — Lead Context Protocol</span>
<nav aria-label="Sections">{nav}</nav>
</header>
<div class="layout">
<main>
<article>
{page.body}</article>
</main>
{toc}</div>
<footer class="page">
Source: <code>{html.escape(page.source)}</code> · build <code>{build}</code> ·
generated by <code>{GENERATOR}</code> — do not edit this file.
</footer>
</body>
</html>
"""


def _listing_page(
    *,
    source: str,
    out_path: str,
    title: str,
    intro: str,
    rows: list[tuple[str, str, str, str]],
) -> Page:
    """An index page: one row per published document in the group."""
    body = [f'<h1 id="index">{html.escape(title)}</h1>', f"<p>{intro}</p>"]
    if rows:
        body.append(
            "<table>\n<thead>\n<tr><th>Document</th><th>Source</th><th>Summary</th></tr>\n</thead>\n<tbody>"
        )
        for href, row_title, source_path, summary in rows:
            prefix = _prefix(out_path)
            body.append(
                "<tr>"
                f'<td><a href="{prefix}{href}">{html.escape(row_title)}</a></td>'
                f"<td><code>{html.escape(source_path)}</code></td>"
                f"<td>{summary}</td>"
                "</tr>"
            )
        body.append("</tbody>\n</table>")
    page = Page(source=source, out_path=out_path, title=title)
    page.body = "\n".join(body) + "\n"
    page.anchors = {"index"}
    return page


def _markdown_summary(text: str) -> str:
    """First paragraph text of a Markdown document, for listing pages."""
    paragraphs: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            if paragraphs:
                break
            continue
        # Headings, tables, blockquotes, list items, and their indented
        # continuations are metadata or structure, never the summary.
        if (
            _indent_width(line)
            or stripped.startswith(("#", "|", ">"))
            or LIST_RE.match(line)
        ):
            if paragraphs:
                break
            continue
        paragraphs.append(stripped)
    summary = " ".join(paragraphs)
    if len(summary) > 220:
        summary = summary[:217].rstrip() + "…"
    return html.escape(_strip_inline_markup(summary), quote=False) or "—"


def _json_summary(source: Source) -> str:
    try:
        document = json.loads(source.text)
    except json.JSONDecodeError:
        return "—"
    description = document.get("description") if isinstance(document, dict) else None
    text = str(description or "").strip()
    if len(text) > 220:
        text = text[:217].rstrip() + "…"
    return html.escape(text, quote=False) or "—"


# --------------------------------------------------------------------------
# Link resolution and checking
# --------------------------------------------------------------------------


def resolve_link(
    pending: PendingLink,
    *,
    by_source: dict[str, str],
    index_by_dir: dict[str, str],
    anchors: dict[str, set[str]],
    notes: set[str],
) -> str:
    target = pending.target
    if not target:
        return pending.label
    if target.startswith("#"):
        anchor = unquote(target[1:])
        if anchor not in anchors.get(pending.page, set()):
            raise BuildError(f"{pending.page}: broken in-page anchor {target!r}")
        return f'<a href="#{html.escape(anchor, quote=True)}">{pending.label}</a>'
    if ABSOLUTE_RE.match(target):
        if target.startswith(("http://", "https://", "//")):
            return f'<a href="{html.escape(target, quote=True)}" rel="noopener">{pending.label}</a>'
        return f'<a href="{html.escape(target, quote=True)}">{pending.label}</a>'

    path_part, _, fragment = target.partition("#")
    fragment = unquote(fragment)
    current_dir = Path(pending.source).parent
    if not path_part:
        resolved = pending.source
    else:
        resolved = _normalise((current_dir / unquote(path_part)).as_posix())

    out_path = by_source.get(resolved)
    if out_path is None:
        if path_part.endswith("/") or resolved in index_by_dir:
            out_path = index_by_dir.get(resolved.rstrip("/"))
        if out_path is None and resolved == ".":
            out_path = "index.html"

    if out_path is None:
        source_exists = (
            (ROOT / resolved).exists()
            if resolved and not resolved.startswith("..")
            else False
        )
        if not source_exists:
            raise BuildError(
                f"{pending.page}: broken internal link {target!r} "
                f"(no such source {resolved!r})"
            )
        notes.add(resolved)
        marker = f' title="Not published in the specification site: {html.escape(resolved, quote=True)}"'
        label = pending.label
        if resolved not in label and Path(resolved).name not in label:
            label = f"{label} (<code>{html.escape(resolved)}</code>)"
        return f'<span class="unpublished"{marker}>{label}</span>'

    if fragment:
        page_anchors = anchors.get(out_path, set())
        if fragment not in page_anchors:
            raise BuildError(
                f"{pending.page}: broken anchor {target!r} — "
                f"{out_path} has no id {fragment!r}"
            )

    href = _relative_href(pending.page, out_path)
    if fragment:
        href = f"{href}#{fragment}"
    return f'<a href="{html.escape(href, quote=True)}">{pending.label}</a>'


def _normalise(path: str) -> str:
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return "../" + "/".join(path.split("/"))
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _relative_href(from_page: str, to_page: str) -> str:
    from_parts = Path(from_page).parent.parts
    to_parts = Path(to_page).parts
    common = 0
    for left, right in zip(from_parts, to_parts):
        if left != right:
            break
        common += 1
    upward = [".."] * (len(from_parts) - common)
    return "/".join([*upward, *to_parts[common:]])


HREF_RE = re.compile(r'(?:href|src)="(?P<value>[^"]*)"')
ID_RE = re.compile(r'\bid="(?P<value>[^"]*)"')


def check_links(pages: dict[str, str]) -> list[str]:
    """Every href/src in the generated output must resolve inside the output."""
    problems: list[str] = []
    known = set(pages)
    for out_path in sorted(pages):
        ids = set(ID_RE.findall(pages[out_path]))
        for value in HREF_RE.findall(pages[out_path]):
            if not value or ABSOLUTE_RE.match(value):
                continue
            path_part, _, fragment = value.partition("#")
            if not path_part:
                if fragment and unquote(fragment) not in ids:
                    problems.append(f"{out_path}: dangling anchor #{fragment}")
                continue
            resolved = _normalise(
                (Path(out_path).parent / unquote(path_part)).as_posix()
            )
            if resolved not in known:
                problems.append(
                    f"{out_path}: link {value!r} misses the output ({resolved})"
                )
                continue
            if fragment and unquote(fragment) not in set(
                ID_RE.findall(pages[resolved])
            ):
                problems.append(
                    f"{out_path}: link {value!r} has no target id {fragment!r}"
                )
    return problems


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def render_site() -> tuple[dict[str, str], BuildStats]:
    """Render the whole site into memory: {site-relative path: text}."""
    sources = collect_sources()
    build = build_hash(sources)

    pending: list[PendingLink] = []
    pages: dict[str, Page] = {}
    by_source: dict[str, str] = {}
    for source in sources:
        if source.kind == "json":
            page = render_schema(source)
        else:
            page, _ = render_markdown(source, pending)
        pages[page.out_path] = page
        by_source[source.path] = page.out_path

    index_by_dir: dict[str, str] = {}
    for directory in (SCHEMA_DIR, VERTICAL_DIR):
        index_by_dir[directory] = f"{directory}/index.html"
        rows = [
            (page.out_path, page.title, source.path, _json_summary(source))
            for source in sources
            if source.kind == "json" and source.path.startswith(f"{directory}/")
            for page in (pages[source.out_path],)
        ]
        index_page = _listing_page(
            source=f"{directory}/",
            out_path=f"{directory}/index.html",
            title=f"LCP {directory.capitalize()}",
            intro=(
                f"Normative JSON documents published from <code>{directory}/</code>. "
                "Each page shows the top-level property summary and the full document."
            ),
            rows=rows,
        )
        pages[index_page.out_path] = index_page
        by_source[f"{directory}/"] = index_page.out_path

    lep_rows = [
        (page.out_path, page.title, source.path, _markdown_summary(source.text))
        for source in sources
        if source.path.startswith(f"{LEP_DIR}/")
        for page in (pages[source.out_path],)
    ]
    lep_index = _listing_page(
        source=f"{LEP_DIR}/",
        out_path="leps/index.html",
        title="LCP enhancement proposals",
        intro=(
            "Enhancement proposals published from "
            f"<code>{LEP_DIR}/</code>. Open proposals are non-normative; accepted "
            "ones are recorded in the registry."
        ),
        rows=lep_rows,
    )
    pages[lep_index.out_path] = lep_index
    by_source[f"{LEP_DIR}/"] = lep_index.out_path

    if "spec/index.html" not in pages:
        spec_index = _listing_page(
            source=f"{SPEC_DIR}/",
            out_path="spec/index.html",
            title="LCP specification — file map",
            intro="The normative specification documents, in reading order.",
            rows=[
                (page.out_path, page.title, source.path, _markdown_summary(source.text))
                for source in sources
                if source.path.startswith(f"{SPEC_DIR}/")
                for page in (pages[source.out_path],)
            ],
        )
        pages[spec_index.out_path] = spec_index
        by_source[f"{SPEC_DIR}/"] = spec_index.out_path

    anchors = {out_path: page.anchors for out_path, page in pages.items()}
    notes: set[str] = set()

    # Layout first: the placeholders survive composition.
    documents: dict[str, str] = {}
    for out_path in sorted(pages):
        documents[out_path] = _layout(pages[out_path], build)

    # Resolve placeholders against the complete page set.
    lookup = dict(enumerate(pending))
    for out_path in sorted(documents):
        text = documents[out_path]

        def replace(match: re.Match[str]) -> str:
            item = lookup[int(match.group(1))]
            return resolve_link(
                item,
                by_source=by_source,
                index_by_dir=index_by_dir,
                anchors=anchors,
                notes=notes,
            )

        documents[out_path] = re.sub(
            f"{LINK_SENTINEL}(\\d+){LINK_SENTINEL}", replace, text
        )

    documents["assets/site.css"] = STYLE

    problems = check_links(documents)
    if problems:
        raise BuildError(
            "broken internal link(s) in the generated site:\n  " + "\n  ".join(problems)
        )

    manifest = {
        "generator": GENERATOR,
        "build_hash": build,
        "pages": [
            {
                "path": out_path,
                "source": pages[out_path].source
                if out_path in pages
                else "assets/site.css",
                "title": pages[out_path].title
                if out_path in pages
                else "Site stylesheet",
            }
            for out_path in sorted(documents)
        ],
        "out_of_scope_links": sorted(notes),
    }
    documents["build.json"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    stats = BuildStats(
        build_hash=build,
        pages=len([path for path in documents if path.endswith(".html")]),
        sources=len(sources),
        out_of_scope=sorted(notes),
    )
    return documents, stats


def write_site(documents: dict[str, str], output: Path) -> tuple[int, int, int]:
    """Write changed files, delete stale ones. Returns (written, unchanged, removed)."""
    written = unchanged = removed = 0
    output.mkdir(parents=True, exist_ok=True)
    for relative, text in sorted(documents.items()):
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = text.encode("utf-8")
        if destination.is_file() and destination.read_bytes() == payload:
            unchanged += 1
            continue
        destination.write_bytes(payload)
        written += 1
    for existing in sorted(output.rglob("*")):
        if not existing.is_file() or existing.name in IGNORED_OUTPUT_NAMES:
            continue
        relative = existing.relative_to(output).as_posix()
        if relative not in documents:
            existing.unlink()
            removed += 1
    for directory in sorted(
        (path for path in output.rglob("*") if path.is_dir()), reverse=True
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    return written, unchanged, removed


def check_site(documents: dict[str, str], output: Path) -> list[str]:
    """Return the stale/missing/unexpected files relative to the rendered site."""
    problems: list[str] = []
    for relative in sorted(documents):
        destination = output / relative
        expected = documents[relative].encode("utf-8")
        if not destination.is_file():
            problems.append(f"missing: {relative}")
        elif destination.read_bytes() != expected:
            problems.append(f"stale: {relative}")
    if output.is_dir():
        for existing in sorted(output.rglob("*")):
            if existing.is_file() and existing.name not in IGNORED_OUTPUT_NAMES:
                relative = existing.relative_to(output).as_posix()
                if relative not in documents:
                    problems.append(f"unexpected (not generated): {relative}")
    else:
        problems.append(f"missing directory: {output}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="render in memory and fail when the output directory is stale",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help=f"output directory (default: {DEFAULT_OUTPUT.relative_to(ROOT)})",
    )
    arguments = parser.parse_args(argv)
    output = Path(arguments.out).resolve()

    try:
        documents, stats = render_site()
    except BuildError as error:
        print(f"build failed: {error}", file=sys.stderr)
        return 1

    if arguments.check:
        problems = check_site(documents, output)
        if problems:
            print(
                "site is stale — run `python3 tools/build_spec_site.py` and commit the result:",
                file=sys.stderr,
            )
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print(f"site is up to date: {stats.pages} pages, build {stats.build_hash}")
        return 0

    written, unchanged, removed = write_site(documents, output)
    print(
        f"site built: {stats.pages} pages from {stats.sources} sources, "
        f"build {stats.build_hash}"
    )
    print(f"wrote {written}, unchanged {unchanged}, removed {removed} under {output}")
    if stats.out_of_scope:
        print(
            "not published (repository-only link targets): "
            + ", ".join(stats.out_of_scope)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

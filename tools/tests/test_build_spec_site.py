"""The specification site generator: rendering subset, link check, freshness.

The committed ``site/`` tree is generated output. These tests pin the three
things that keep it trustworthy: the Markdown subset renders the constructs the
specification uses, a broken internal link or anchor fails the build, and
``--check`` reports a stale or hand-edited tree.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools import build_spec_site as site

ROOT = Path(__file__).resolve().parents[2]


def render(text: str) -> tuple[site.Page, str]:
    """Render a Markdown fragment through the generator's own renderer."""
    source = site.Source(
        path="spec/fragment.md",
        kind="markdown",
        out_path="spec/fragment.html",
        text=text,
        digest="",
    )
    return site.render_markdown(source, [])


class MarkdownSubsetTests(unittest.TestCase):
    def test_headings_keep_github_slugs(self) -> None:
        page, body = render("## 9. Security\n\n### Phone hash (dedup)\n")
        self.assertIn('<h2 id="9-security">', body)
        self.assertIn('<h3 id="phone-hash-dedup">', body)
        self.assertEqual(
            [anchor for _, anchor, _ in page.headings],
            ["9-security", "phone-hash-dedup"],
        )

    def test_inline_code_survives_bold_and_link_labels(self) -> None:
        source = site.Source(
            path="spec/fragment.md",
            kind="markdown",
            out_path="spec/fragment.html",
            text="**`x-lcp-pii` is required** and [`D9`](../docs/DECISIONS.md).\n",
            digest="",
        )
        pending: list[site.PendingLink] = []
        _, body = site.render_markdown(source, pending)
        self.assertIn("<strong><code>x-lcp-pii</code> is required</strong>", body)
        self.assertNotIn("**", body)
        self.assertEqual([link.label for link in pending], ["<code>D9</code>"])
        self.assertEqual([link.target for link in pending], ["../docs/DECISIONS.md"])

    def test_lists_nest_by_indentation(self) -> None:
        _, body = render("- one\n  - two\n- three\n")
        self.assertIn("<ul>", body)
        self.assertEqual(body.count("<li>"), 3)
        self.assertIn("two", body)

    def test_table_cells_keep_code_pipes(self) -> None:
        _, body = render("| Code | Path |\n|---|---|\n| `a|b` | x |\n")
        self.assertEqual(body.count("<th>"), 2)
        self.assertIn("<code>a|b</code>", body)

    def test_fenced_code_is_escaped_not_interpreted(self) -> None:
        _, body = render('```json\n{"x": "<script>&"}\n```\n')
        self.assertIn('class="language-json"', body)
        self.assertIn("&lt;script&gt;&amp;", body)
        self.assertNotIn("<script>", body)


class LinkValidationTests(unittest.TestCase):
    def resolve(self, target: str) -> str:
        pending = site.PendingLink(
            page="spec/page.html", source="spec/page.md", label="label", target=target
        )
        return site.resolve_link(
            pending,
            by_source={
                "spec/page.md": "spec/page.html",
                "spec/other.md": "spec/other.html",
            },
            index_by_dir={"schemas": "schemas/index.html"},
            anchors={"spec/page.html": {"here"}, "spec/other.html": {"there"}},
            notes=set(),
        )

    def test_relative_markdown_link_is_rewritten_to_a_page(self) -> None:
        self.assertEqual(
            self.resolve("other.md#there"), '<a href="other.html#there">label</a>'
        )

    def test_missing_anchor_fails(self) -> None:
        with self.assertRaises(site.BuildError):
            self.resolve("other.md#nowhere")

    def test_missing_target_fails(self) -> None:
        with self.assertRaises(site.BuildError):
            self.resolve("absent.md")

    def test_out_of_scope_target_is_text_not_a_dead_link(self) -> None:
        self.assertIn('class="unpublished"', self.resolve("../LICENSE"))


class FreshnessTests(unittest.TestCase):
    def test_generated_site_matches_the_normative_sources(self) -> None:
        documents, _ = site.render_site()
        self.assertEqual(site.check_site(documents, site.DEFAULT_OUTPUT), [])
        self.assertIn("index.html", documents)
        self.assertIn("build.json", documents)

    def test_check_reports_stale_and_unexpected_files(self) -> None:
        documents, _ = site.render_site()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            site.write_site(documents, output)
            first = next(path for path in sorted(output.rglob("*.html")))
            first.write_text("hand edited\n", encoding="utf-8")
            (output / "stray.html").write_text("not generated\n", encoding="utf-8")
            problems = site.check_site(documents, output)
            self.assertTrue(
                any(problem.startswith("stale:") for problem in problems), problems
            )
            self.assertTrue(
                any("unexpected" in problem for problem in problems), problems
            )

    def test_cli_check_exits_clean(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "build_spec_site.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

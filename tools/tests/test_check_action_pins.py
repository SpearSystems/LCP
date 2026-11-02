"""Action pin-truth checker tests: deterministic, network-free.

The GitHub API client is injected as a fake; the real tree is never
contacted. Coverage: parsing, the unpinned gate, exact-semver truth,
major-series drift acceptance, unresolvable-SHA failure, and the loud
known-unresolvable exemption.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from check_action_pins import (  # noqa: E402
    KNOWN_UNRESOLVABLE,
    EnvironmentError_,
    GitHubAPI,
    Pin,
    check_action_pins,
    check_pin,
    main,
    parse_lsremote_ref,
    parse_lsremote_tags,
    parse_pins,
)


SHA_A = "a" * 40
SHA_B = "b" * 40


class ExplodingAPI:
    """Any API call is a test failure (exempted pins must short-circuit)."""

    def resolve_ref(self, repo: str, kind: str, ref: str):
        raise AssertionError("exempted pin must not hit the API")

    def tags_by_commit(self, repo: str):
        raise AssertionError("exempted pin must not hit the API")


class FakeAPI:
    """Duck-typed GitHubAPI: refs as (repo, kind, ref) -> commit sha; tags as
    repo -> {commit_sha: [tag names]}."""

    def __init__(self, refs=None, tags=None):
        self.refs = refs or {}
        self.tags = tags or {}

    def resolve_ref(self, repo, kind, ref):
        sha = self.refs.get((repo, kind, ref))
        return ("commit", sha) if sha else None

    def tags_by_commit(self, repo):
        return self.tags.get(repo, {})


def make_root(files: dict[str, str]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for name, text in files.items():
        path = tmp / ".github" / "workflows" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp


class ParseTests(unittest.TestCase):
    def test_parses_pinned_lines_and_skips_local_and_docker(self):
        root = make_root(
            {
                "w.yml": (
                    "jobs:\n"
                    "  a:\n"
                    "    steps:\n"
                    "      - uses: actions/checkout@v4\n"
                    f"      - uses: actions/checkout@{SHA_A} # v7.0.1\n"
                    f"        uses: gh/codeql-action/init@{SHA_B} # v4\n"
                    "      - uses: ./local-action\n"
                    "      - uses: docker://alpine:3\n"
                )
            }
        )
        pins = parse_pins(root)
        self.assertEqual(len(pins), 3)
        self.assertEqual(pins[0].ref, "v4")
        self.assertEqual(pins[1].comment, "v7.0.1")
        self.assertEqual(pins[2].name, "gh/codeql-action/init")
        self.assertEqual(pins[2].repo, "gh/codeql-action")

    def test_missing_ref_reports_empty_ref(self):
        pins = parse_pins(make_root({"w.yml": "uses: owner/repo\n"}))
        self.assertEqual(pins[0].ref, "")


class CheckPinTests(unittest.TestCase):
    def pin(self, ref, comment=None):
        return Pin("f.yml", 1, "acme/tool", ref, comment)

    def test_unpinned_tag_ref_is_violation(self):
        status, msg = check_pin(self.pin("v4", "v4"), FakeAPI())
        self.assertEqual(status, "fail")
        self.assertIn("not pinned", msg)

    def test_short_sha_is_violation(self):
        status, _ = check_pin(self.pin("abc123", "v1.0.0"), FakeAPI())
        self.assertEqual(status, "fail")

    def test_exact_semver_tag_passes(self):
        api = FakeAPI(refs={("acme/tool", "tags", "v1.2.3"): SHA_A})
        status, msg = check_pin(self.pin(SHA_A, "v1.2.3"), api)
        self.assertEqual(status, "ok")
        self.assertIn("exact", msg)

    def test_semver_drift_fails_even_if_sha_is_another_tag(self):
        api = FakeAPI(
            refs={("acme/tool", "tags", "v1.2.4"): SHA_B},
            tags={"acme/tool": {SHA_A: ["v1.2.3"]}},
        )
        status, msg = check_pin(self.pin(SHA_A, "v1.2.4"), api)
        self.assertEqual(status, "fail")
        self.assertIn("immutable", msg)

    def test_major_tip_passes(self):
        api = FakeAPI(refs={("acme/tool", "tags", "v4"): SHA_A})
        status, msg = check_pin(self.pin(SHA_A, "v4"), api)
        self.assertEqual(status, "ok")
        self.assertIn("tip", msg)

    def test_major_series_drift_passes_when_sha_is_released_tag(self):
        api = FakeAPI(
            refs={("acme/tool", "tags", "v4"): SHA_B},
            tags={"acme/tool": {SHA_A: ["v4.38.2"]}},
        )
        status, msg = check_pin(self.pin(SHA_A, "v4"), api)
        self.assertEqual(status, "ok")
        self.assertIn("v4.38.2", msg)

    def test_series_drift_outside_declared_major_fails(self):
        api = FakeAPI(
            refs={("acme/tool", "tags", "v4"): SHA_B},
            tags={"acme/tool": {SHA_A: ["v5.0.1"]}},
        )
        status, _ = check_pin(self.pin(SHA_A, "v4"), api)
        self.assertEqual(status, "fail")

    def test_unresolvable_sha_without_comment_fails(self):
        status, msg = check_pin(self.pin(SHA_A), FakeAPI())
        self.assertEqual(status, "fail")
        self.assertIn("matches no tag", msg)

    def test_commentless_sha_resolving_to_tag_warns(self):
        api = FakeAPI(tags={"acme/tool": {SHA_A: ["v2.1.0"]}})
        status, msg = check_pin(self.pin(SHA_A), api)
        self.assertEqual(status, "warn")
        self.assertIn("v2.1.0", msg)

    def test_declared_ref_missing_fails(self):
        api = FakeAPI(tags={"acme/tool": {SHA_A: ["v1.9.0"]}})
        status, _ = check_pin(self.pin(SHA_A, "v1.9.9"), api)
        self.assertEqual(status, "fail")

    def test_exemption_list_stays_empty_without_evidence(self):
        # The trivy-action #127 exemption was disproven on 2026-11-02:
        # ed142fd… is the peeled target of upstream tag v0.36.0 and now
        # carries a '# v0.36.0' comment; the workflow pins pass normally.
        self.assertNotIn(
            "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25",
            KNOWN_UNRESOLVABLE,
        )

    def test_known_unresolvable_pin_warns_loudly_but_never_calls_api(self):
        import check_action_pins

        pin = Pin("s.yml", 9, "acme/tool", SHA_A, None)
        check_action_pins.KNOWN_UNRESOLVABLE = {
            pin.key: "synthetic: dated evidence here"
        }
        try:
            status, msg = check_pin(pin, ExplodingAPI())
        finally:
            check_action_pins.KNOWN_UNRESOLVABLE = {}
        self.assertEqual(status, "warn")
        self.assertIn("synthetic", msg)


class LsRemoteFallbackTests(unittest.TestCase):
    def test_annotated_ref_prefers_peeled_line(self):
        lines = [
            f"a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8\trefs/tags/v0.36.0",
            f"{SHA_A}\trefs/tags/v0.36.0^{{}}",
        ]
        self.assertEqual(
            parse_lsremote_ref(lines, "tags", "v0.36.0"), ("commit", SHA_A)
        )

    def test_lightweight_ref_direct_line(self):
        lines = [f"{SHA_B}\trefs/heads/release/v1"]
        self.assertEqual(
            parse_lsremote_ref(lines, "heads", "release/v1"), ("commit", SHA_B)
        )

    def test_absent_ref_returns_none(self):
        self.assertIsNone(parse_lsremote_ref([], "tags", "v9"))

    def test_tag_map_peels_annotated_only(self):
        lines = [
            f"tagobj\trefs/tags/v1.0.0",
            f"{SHA_A}\trefs/tags/v1.0.0^{{}}",
            f"{SHA_B}\trefs/tags/v2.0.0",  # lightweight: line IS the commit
        ]
        mapping = parse_lsremote_tags(lines)
        self.assertEqual(mapping.get(SHA_A), ["v1.0.0"])
        self.assertEqual(mapping.get(SHA_B), ["v2.0.0"])
        self.assertNotIn("tagobj", mapping)

    def test_api_refusal_degrades_repo_to_lsremote(self):
        api = GitHubAPI()

        def refuse(url):
            raise EnvironmentError_(f"403 for {url}")

        api._get = refuse
        seen = []

        def fake_lsremote(repo, patterns):
            seen.append((repo, tuple(patterns)))
            return [f"tagobj\trefs/tags/v0.1.0", f"{SHA_A}\trefs/tags/v0.1.0^{{}}"]

        api._lsremote = fake_lsremote
        self.assertEqual(
            api.resolve_ref("acme/tool", "tags", "v0.1.0"), ("commit", SHA_A)
        )
        # repo now degraded: further resolutions skip REST entirely
        self.assertEqual(api.resolve_ref("acme/tool", "tags", "v0.2.0"), None)
        self.assertEqual(len(seen), 2)  # both calls were ls-remote, no REST retry
        self.assertIn("acme/tool", api._degraded)


class ReportAndMainTests(unittest.TestCase):
    def test_unpinned_workflow_fails_loud_via_main(self):
        root = make_root(
            {"w.yml": "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"}
        )
        code = main(["--root", str(root), "--token", "unused"])
        self.assertEqual(code, 1)

    def test_mixed_repo_reports_counts(self):
        root = make_root(
            {
                "ok.yml": f"steps:\n  - uses: acme/tool@{SHA_A} # v1.2.3\n",
                "bad.yml": "steps:\n  - uses: acme/tool@v1\n",
            }
        )
        api = FakeAPI(refs={("acme/tool", "tags", "v1.2.3"): SHA_A})
        report = check_action_pins(root, api)
        self.assertEqual(len(report.passes), 1)
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.emit(print_fn=lambda _line: None), 1)


if __name__ == "__main__":
    unittest.main()

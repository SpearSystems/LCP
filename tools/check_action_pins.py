#!/usr/bin/env python3
"""Action pin-truth checker (ARB plan §3.4, lane W1-CI-Checkers).

Every ``uses:`` in ``.github/workflows/*.yml`` must be pinned to a full
40-hex commit SHA and the declared tag/branch in the trailing ``# comment``
must be *true*: the SHA must actually resolve to what the comment says.
The verification waves checked this by hand 19x; this script never sleeps.

Rules
-----
1. SHA pin format: ``owner/repo[/sub]@<40-hex>``. Anything else (branch or
   ``@vN`` tags) is an UNPINNED violation. The repo convention is SHA pin +
   ``# <tag>`` comment, so today's tree must already be clean.
2. Pin truth via the GitHub API (``GITHUB_TOKEN``/``GH_TOKEN`` if present,
   unauthenticated otherwise):
   - declared ``vN.N.N`` (or ``N.N.N``) semver tag: the pinned SHA must equal
     the tag's commit target exactly (annotated tags are dereferenced).
     Drift off an immutable patch tag means either the pin or the comment is
     wrong, so it FAILS.
   - declared major/series ref (``v4``, ``release/v1``, a branch): the pinned
     SHA may be the ref's current tip (PASS) or a *released* commit in the
     same version series while the moving ref has drifted ahead. That drift
     is allowed only when the SHA corresponds to one concrete full-semver
     tag of the series (the checker resolves and prints it, e.g.
     ``2892aa5e…# v4`` -> ``v4.38.2``), which keeps every pin auditable to a
     real release. A SHA that belongs to no tag at all FAILS.
   - pins with no comment are checked against the repo's tag list: any real
     released tag resolves them (WARN asking for a comment); an SHA that
     resolves nowhere FAILS.
3. Pins that genuinely cannot be resolved upstream are *exempted loudly*,
   never silently: entries in ``KNOWN_UNRESOLVABLE`` print a WARN with their
   evidence each run until re-pinned. The list is currently empty: the
   ``aquasecurity/trivy-action`` commit from issue #127 was re-verified
   2026-11-02 and DOES resolve — ``v0.36.0`` is an annotated tag whose
   peeled target is ``ed142fd0673e97e23eac54620cfb913e5ce36c25`` (verified
   via ``git ls-remote`` ``refs/tags/v0.36.0^{}`` and the REST
   ``/commits/`` endpoint). The original "unresolvable" claim rested on a
   wrong tag name (missing ``v`` prefix) and a 404 from the
   ``/git/objects/`` REST quirk; the pin is retained unchanged and now
   carries its ``# v0.36.0`` comment.

Exit codes: 0 = clean (warnings allowed), 1 = violations found,
2 = usage/environment error (API unreachable, bad arguments).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

ROOT = Path(__file__).resolve().parents[1]

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
USES_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<pin>[^\s#]+)(?:\s*#\s*(?P<comment>.*))?\s*$"
)
FULL_SEMVER_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-.+0-9A-Za-z-]*)$")
SERIES_RE = re.compile(r"v?(\d+)")
REF_CHARS_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class EnvironmentError_(RuntimeError):
    """API unreachable / rate limited / unexpected response — exit 2."""


@dataclass(frozen=True)
class Pin:
    file: str
    line: int
    name: str  # owner/repo[/sub]
    ref: str  # text after the last '@'
    comment: str | None

    @property
    def repo(self) -> str:
        parts = self.name.split("/")
        return "/".join(parts[:2])

    @property
    def key(self) -> str:
        return f"{self.name}@{self.ref}"

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class Report:
    passes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def emit(self, print_fn: Callable[[str], None] = print) -> int:
        for line in self.passes:
            print_fn(f"  ok    {line}")
        for line in self.warnings:
            print_fn(f"  warn  {line}")
        for line in self.violations:
            print_fn(f"  FAIL  {line}")
        print_fn(
            f"action-pin-truth: {len(self.passes)} verified, "
            f"{len(self.warnings)} warning(s), {len(self.violations)} violation(s)"
        )
        return 1 if self.violations else 0


# Pins retained from history that no longer resolve upstream. Format:
# "<owner/repo@sha>" -> reason printed in every run (with evidence link).
# This is a disclosure list, not a mute button: each entry is re-verified
# against upstream tags every run and stays a visible WARN. Entries MUST
# carry dated re-verification evidence; the 2026-11-02 trivy-action review
# (see module docstring, rule 3) is the cautionary tale for exempting on a
# single endpoint 404.
KNOWN_UNRESOLVABLE: dict[str, str] = {}


def parse_pins(root: Path) -> list[Pin]:
    """Parse every ``uses:`` line under root/.github/workflows."""
    pins: list[Pin] = []
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        raise EnvironmentError_(f"no workflows directory at {workflows}")
    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_RE.match(raw)
            if not match:
                continue
            token = match.group("pin")
            if token.startswith(("./", "docker://")):
                continue  # local and container steps have no upstream tags
            if "@" not in token:
                # Malformed for this repo's convention; report as unpinned.
                pins.append(Pin(rel, lineno, token, "", match.group("comment") or None))
                continue
            name, _, ref = token.partition("@")
            comment = (match.group("comment") or "").strip() or None
            pins.append(Pin(rel, lineno, name, ref, comment))
    return pins


class GitHubAPI:
    """Minimal REST client: ref resolution + tag enumeration, with cache."""

    def __init__(self, token: str | None = None):
        self._token = token
        self._ref_cache: dict[str, tuple[str, str] | None] = {}
        self._tags_cache: dict[str, dict[str, list[str]]] = {}

    def _get(self, url: str) -> tuple[object, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lcp-check-action-pins",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(
                    response.read().decode("utf-8")
                ), response.headers.get("Link", "")
        except urllib.error.HTTPError as err:
            if err.code in (403, 429):
                raise EnvironmentError_(
                    f"GitHub API access refused for {url} (HTTP {err.code}): "
                    "rate limited? Export GITHUB_TOKEN."
                ) from err
            if err.code == 404:
                return None, ""
            raise EnvironmentError_(
                f"GitHub API error for {url}: HTTP {err.code}"
            ) from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise EnvironmentError_(f"GitHub API unreachable for {url}: {err}") from err

    def resolve_ref(self, repo: str, kind: str, ref: str) -> tuple[str, str] | None:
        """Return ('commit'|'tag-object', commit_sha) or None if the ref is absent."""
        cache_key = f"{repo}|{kind}|{ref}"
        if cache_key in self._ref_cache:
            return self._ref_cache[cache_key]
        quoted = urllib.parse.quote(ref, safe="")
        result = self._get(
            f"https://api.github.com/repos/{repo}/git/ref/{kind}/{quoted}"
        )[0]
        target: tuple[str, str] | None = None
        if isinstance(result, dict):
            obj = result.get("object", {})
            sha, typ = obj.get("sha", ""), obj.get("type", "commit")
            if typ == "tag":
                tag_obj = self._get(
                    f"https://api.github.com/repos/{repo}/git/tags/{sha}"
                )[0]
                if isinstance(tag_obj, dict) and tag_obj.get("object"):
                    typ, sha = (
                        tag_obj["object"].get("type", "commit"),
                        tag_obj["object"]["sha"],
                    )
            if typ == "commit":
                target = ("commit", sha)
        self._ref_cache[cache_key] = target
        return target

    def tags_by_commit(self, repo: str) -> dict[str, list[str]]:
        """Map every tag's (dereferenced) commit SHA to its tag names."""
        if repo in self._tags_cache:
            return self._tags_cache[repo]
        mapping: dict[str, list[str]] = {}
        url = f"https://api.github.com/repos/{repo}/tags?per_page=100"
        while url:
            payload, link = self._get(url)
            if not isinstance(payload, list):
                raise EnvironmentError_(f"unexpected tags response for {repo}")
            for tag in payload:  # /tags already peels annotated tags to commits
                mapping.setdefault(tag.get("commit", {}).get("sha", ""), []).append(
                    tag.get("name", "")
                )
            url = ""
            for chunk in link.split(","):
                if 'rel="next"' in chunk:
                    url = chunk.split("<")[1].split(">")[0]
        self._tags_cache[repo] = mapping
        return mapping


class RefResolver(Protocol):
    """The two API operations check_pin needs; tests inject a fake."""

    def resolve_ref(self, repo: str, kind: str, ref: str) -> tuple[str, str] | None: ...

    def tags_by_commit(self, repo: str) -> dict[str, list[str]]: ...


def _series(ref: str) -> int | None:
    """Major series of a ref or tag name: 'v4'->4, 'release/v1'->1, 'v4.38.2'->4.
    The major is the first digit group after an optional 'v'."""
    numbers = SERIES_RE.findall(ref)
    return int(numbers[0]) if numbers else None


def _is_full_semver(ref: str) -> bool:
    return bool(FULL_SEMVER_RE.match(ref))


def check_pin(pin: Pin, api: RefResolver) -> tuple[str, str]:
    """Return (status in {ok,warn,fail}, human message) for one parsed pin."""
    if not SHA40_RE.match(pin.ref):
        shown = f"@{pin.ref}" if pin.ref else "(no @ref)"
        return (
            "fail",
            f"{pin.location}: {pin.name}{shown} is not pinned to a full 40-hex commit",
        )
    if pin.key in KNOWN_UNRESOLVABLE:
        return (
            "warn",
            f"{pin.location}: {pin.name}@{pin.ref[:8]}… known-unresolvable exemption: {KNOWN_UNRESOLVABLE[pin.key]}",
        )

    declared = pin.comment if pin.comment and REF_CHARS_RE.match(pin.comment) else None
    tip: tuple[str, str] | None = None
    if declared:
        tip = api.resolve_ref(pin.repo, "tags", declared) or api.resolve_ref(
            pin.repo, "heads", declared
        )

    if tip and tip[1] == pin.ref:
        if declared and _is_full_semver(declared):
            return (
                "ok",
                f"{pin.location}: {pin.name}@{pin.ref[:8]}… == {declared} (exact)",
            )
        return "ok", f"{pin.location}: {pin.name}@{pin.ref[:8]}… == tip of {declared}"

    # Drift: the SHA must correspond to a concrete released tag.
    released = api.tags_by_commit(pin.repo).get(pin.ref, [])
    if not released:
        detail = (
            f"is not the tip of declared ref {declared!r} (tip {tip[1][:8]}…)"
            if declared and tip
            else f"declared ref {declared!r} does not exist upstream"
            if declared
            else "(no # tag comment to check)"
        )
        return (
            "fail",
            f"{pin.location}: {pin.name}@{pin.ref} matches no tag of {pin.repo} and {detail}",
        )
    if pin.comment is None:
        return "warn", (
            f"{pin.location}: {pin.name}@{pin.ref[:8]}… resolves to {', '.join(released)} "
            "but carries no '# tag' comment — add one so intent is auditable"
        )
    if declared and _is_full_semver(declared):
        return "fail", (
            f"{pin.location}: comment declares {declared} but {pin.name}@{pin.ref[:8]}… "
            f"is {', '.join(released)} — immutable semver tags must match exactly"
        )
    if declared and not tip:
        return "fail", (
            f"{pin.location}: declared ref {declared!r} of {pin.repo} not found; "
            f"pin resolves to {', '.join(released)} — fix the comment"
        )
    declared_series = _series(declared) if declared else None
    same_series = (
        [t for t in released if _series(t) == declared_series]
        if declared_series is not None
        else []
    )
    if declared_series is None:
        return (
            "ok",
            f"{pin.location}: {pin.name}@{pin.ref[:8]}… == {', '.join(released)} (no series in comment {declared!r} to match)",
        )
    if not same_series:
        return "fail", (
            f"{pin.location}: {pin.name}@{pin.ref[:8]}… is {', '.join(released)}, "
            f"outside declared series {declared}"
        )
    note = (
        f"(declared {declared}; consider commenting the full tag)"
        if declared and not _is_full_semver(declared)
        else ""
    )
    return (
        "ok",
        f"{pin.location}: {pin.name}@{pin.ref[:8]}… == {', '.join(same_series)} {note}".rstrip(),
    )


def check_action_pins(root: Path = ROOT, api: RefResolver | None = None) -> Report:
    api = api or GitHubAPI()
    report = Report()
    for pin in parse_pins(root):
        status, message = check_pin(pin, api)
        getattr(
            report, {"ok": "passes", "warn": "warnings", "fail": "violations"}[status]
        ).append(message)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify every workflow action pin is a full SHA whose declared tag resolves"
    )
    parser.add_argument(
        "--root", default=str(ROOT), help="repository root (default: this checkout)"
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub API token (default: $GITHUB_TOKEN/$GH_TOKEN)",
    )
    args = parser.parse_args(argv)
    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        report = check_action_pins(Path(args.root), GitHubAPI(token))
    except EnvironmentError_ as err:
        print(f"action-pin-truth: environment error: {err}", file=sys.stderr)
        return 2
    return report.emit()


if __name__ == "__main__":
    sys.exit(main())

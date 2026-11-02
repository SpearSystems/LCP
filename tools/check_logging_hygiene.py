#!/usr/bin/env python3
"""Logging hygiene lint for the LCP repository (V1.1 roadmap, Phase 5).

Lead envelopes carry consumer PII. Runtime code that prints to stdout or
console-dumps a payload turns every operator terminal, CI log, and container
log stream into a leak surface. This checker enforces three minimal,
line-based rules.

Rule scope is by directory pattern, not a blanket ban on ``print``:

- ``tools/``, ``test-vectors/``, and ``.github/`` are maintainer/operator
  CLIs where stdout output is the product. They are never scanned here.
- ``implementations/reference-platform/lcp_platform/admin.py`` is the
  operator CLI (``lcp-platform`` entry point); its table/JSON output to
  stdout is by design and is documented in the operator docs.
- Test harnesses (path segments ``test``/``tests``, and Go ``_test.go``
  files, which sit beside their package instead of in a tests directory) may
  print runner status.
- The top-level ``examples/`` tree is demo code whose prints are part of
  the lesson; example files are guarded by the header rule instead
  (Rule 3). Any ``examples/`` segment is also exempt from Rules 1-2.

Rules:

1. OUTSIDE_STDOUT_PRINT — Python files under ``implementations/`` must not
   call ``print(`` without the documented operational-message allowance
   ``file=sys.stderr``. JavaScript/TypeScript files under
   ``implementations/`` must not call ``console.log(`` or ``console.error(``
   (there is no stderr allowance for console calls). Go files under
   ``implementations/`` must not call ``fmt.Print``/``Printf``/``Println``,
   ``log.Print``/``Fatal``/``Panic`` (any suffix variant), write to
   ``os.Stdout``, or target ``fmt.Fprint*`` at ``os.Stdout``;
   ``fmt.Fprint*`` to ``os.Stderr`` keeps the Python-style operational
   allowance and ``fmt.Sprintf``/``fmt.Errorf`` are string building, not
   output.
2. PAYLOAD_DUMP — no output call (``print(``, ``console.*``, ``logging.``)
   may take a ``json.dumps(...)``/``JSON.stringify(...)`` argument on the
   same line, in any scanned file, even to stderr: dumping a serialized
   envelope/payload into any stream is a leak surface. Serialization for
   wire bodies (``return json.dumps(...)``) is unaffected.
3. MISSING_EXAMPLE_HEADER — every tracked-style source file (py, sh, js,
   mjs, cjs, ts, md) under ``examples/integrations/`` must carry an
   ``example — not for production`` header line within its first 12 lines,
   in the file type's comment style (``#`` for py/sh, ``//`` or ``*`` for
   js-family, any line for markdown, which may use a blockquote or an HTML
   comment). JSON files under ``examples/`` are schema payload samples
   with no comment syntax and are exempt.

Exit codes: 0 = clean, 1 = violations found, 2 = usage/environment error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Vendored/build directories that never contain first-party source.
SKIP_DIRS = {
    ".build",
    ".git",
    ".gradle",
    ".mypy_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "graphify-out",
    "node_modules",
    "target",
}

PY_EXT = {".py"}
JS_EXT = {".cjs", ".js", ".mjs", ".ts"}
GO_EXT = {".go"}
HEADER_EXT = PY_EXT | JS_EXT | {".md", ".sh"}

PRINT_PY = re.compile(r"(?<![\w.])print\(")
STDERR_ALLOWANCE = re.compile(r"file\s*=\s*sys\.stderr\b")
CONSOLE_JS = re.compile(r"console\.(?:log|error)\s*\(")
LOGGING_CALL = re.compile(r"(?:logging\.|logger\.|getLogger)")
SERIALIZED_DUMP = re.compile(r"(?:json\.dumps\(|JSON\.stringify\()")

# Rule 1 also covers the Go SDK, which the static gate previously skipped,
# leaving ``fmt.Println``-class leaks in ``implementations/sdk/go`` uncaught
# (invariant 7 had only a runtime half for Go). Matches only calls whose
# destination is stdout or the stdlib log package (which writes to stderr by
# default but is a library smell for a PII-handling SDK):
#   fmt.Print|Printf|Println      -> stdout
#   log.Print|Fatal|Panic(+ln/f)  -> stdlib logger
#   fmt.Fprint*(os.Stdout, ...)   -> explicit stdout writer
#   os.Stdout.Write(...)          -> explicit stdout writer
# fmt.Fprint*(os.Stderr, ...)/Sprintf/Eferror and byte-building are NOT output
# calls and stay allowed, mirroring the Python stderr operational allowance.
GO_OUTPUT_CALL = re.compile(
    r"(?<![\w.])(?:fmt\.Print(?:ln|f)?\s*\(|log\.(?:Print|Fatal|Panic)(?:ln|f)?\s*\(|fmt\.Fprint(?:ln|f)?\s*\(\s*os\.Stdout\b|os\.Stdout\.Write\s*\()"
)

# Header line: "example" ... "not for production" (case-insensitive) with a
# comment marker appropriate to the file type.
_HEADER_PHRASE = r"example\b[^\n]*\bnot for production"
HEADER_PATTERNS = {
    ".py": re.compile(rf"^\s*#\s*{_HEADER_PHRASE}", re.IGNORECASE),
    ".sh": re.compile(rf"^\s*#\s*{_HEADER_PHRASE}", re.IGNORECASE),
    ".md": re.compile(_HEADER_PHRASE, re.IGNORECASE),
}
HEADER_PATTERNS.update(
    dict.fromkeys(
        JS_EXT, re.compile(rf"^\s*(?://|/\*|\*)\s*{_HEADER_PHRASE}", re.IGNORECASE)
    )
)
HEADER_WINDOW = 12


def _is_rule12_scope(relative: Path) -> bool:
    """Rules 1-2 apply to first-party runtime code under implementations/."""
    parts = relative.parts
    if parts[0] != "implementations":
        return False
    if any(part in {"test", "tests", "examples"} for part in parts[1:]):
        return False
    if relative == Path("implementations/reference-platform/lcp_platform/admin.py"):
        return False
    return True


def _is_rule3_scope(relative: Path) -> bool:
    return relative.parts[:2] == ("examples", "integrations")


def check_logging_hygiene(repo_root: str | Path = ROOT) -> list[str]:
    """Return logging-hygiene problems for a repository checkout."""
    root = Path(repo_root)
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        base = Path(dirpath)
        for name in sorted(filenames):
            path = base / name
            relative = path.relative_to(root)
            suffix = path.suffix.lower()
            if _is_rule3_scope(relative):
                if suffix in HEADER_EXT and not _has_example_header(path, suffix):
                    problems.append(
                        f"{relative}:1: MISSING_EXAMPLE_HEADER: files under "
                        "examples/integrations/ must open with an "
                        "'example — not for production' header (first "
                        f"{HEADER_WINDOW} lines, comment style for .{suffix.lstrip('.')})"
                    )
                continue
            if not _is_rule12_scope(relative):
                continue
            if suffix not in PY_EXT and suffix not in JS_EXT and suffix not in GO_EXT:
                continue
            # Go has no tests/ directory convention: _test.go files sit beside
            # the package source and are test harnesses that legitimately
            # echo captured output (the log-capture suite writes to redirected
            # os.Stdout/os.Stderr). Exempt them exactly as test dirs are.
            if suffix in GO_EXT and name.endswith("_test.go"):
                continue
            problems.extend(_check_output_lines(path, relative, suffix))
    return problems


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _has_example_header(path: Path, suffix: str) -> bool:
    pattern = HEADER_PATTERNS[suffix]
    return any(pattern.search(line) for line in _read_lines(path)[:HEADER_WINDOW])


def _check_output_lines(path: Path, relative: Path, suffix: str) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in PY_EXT:
        for lineno, call in _call_spans(text, PRINT_PY):
            if SERIALIZED_DUMP.search(call):
                problems.append(_payload_dump_problem(relative, lineno))
            elif not STDERR_ALLOWANCE.search(call):
                problems.append(
                    f"{relative}:{lineno}: OUTSIDE_STDOUT_PRINT: platform/SDK "
                    "code must not print to stdout (use file=sys.stderr for "
                    "operational messages)"
                )
    elif suffix in GO_EXT:
        for lineno, call in _call_spans(text, GO_OUTPUT_CALL):
            problems.append(
                f"{relative}:{lineno}: OUTSIDE_STDOUT_PRINT: Go platform/SDK "
                "code must not write to stdout or the log package; LCP "
                "envelopes are PII-bearing (fmt.Fprintf(os.Stderr, ...) is "
                "the operational-message allowance, fmt.Sprintf/Eferror are "
                "not output calls)"
            )
    else:
        for lineno, call in _call_spans(text, CONSOLE_JS):
            if SERIALIZED_DUMP.search(call):
                problems.append(_payload_dump_problem(relative, lineno))
            else:
                problems.append(
                    f"{relative}:{lineno}: OUTSIDE_STDOUT_PRINT: SDK JS/TS "
                    "code must not use console.log/console.error"
                )
    # Catch serializer output smuggled through logging calls or string
    # concatenation on a single line (no logging library exists in the
    # platform today; this keeps Rule 2 honest if one is added).
    for lineno, line in enumerate(text.splitlines(), start=1):
        if SERIALIZED_DUMP.search(line) and LOGGING_CALL.search(line):
            problems.append(_payload_dump_problem(relative, lineno))
    return problems


def _payload_dump_problem(relative: Path, lineno: int) -> str:
    return (
        f"{relative}:{lineno}: PAYLOAD_DUMP: serialized payloads "
        "(json.dumps/JSON.stringify) must not be passed to print/"
        "console/logging calls; envelope fields are PII-bearing"
    )


def _call_spans(text: str, opener: re.Pattern[str]):
    """Yield (line_number, call_text) for each balanced call of `opener`.

    String literals are stripped before paren counting so quotes and
    parentheses inside message text do not skew the span. The scan is
    deliberately coarse (line-oriented, no parser): good enough for the
    two regexes it feeds and never silent about a matched opener.
    """
    stripped = re.sub(r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')', '""', text)
    for match in opener.finditer(stripped):
        depth = 0
        idx = len(stripped) - 1
        for pos in range(match.start(), len(stripped)):
            char = stripped[pos]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    idx = pos
                    break
        # Unbalanced (open call at end of file): report the opened span as-is.
        yield (
            stripped.count("\n", 0, match.start()) + 1,
            stripped[match.start() : idx + 1],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="repository root to scan (default: the checkout containing tools/)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    problems = check_logging_hygiene(root)
    if problems:
        print(f"logging-hygiene violations ({len(problems)}):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("logging hygiene: no violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Cross-SDK signature-interop matrix (LEP-0002 rollout; ARB plan §3.3, post-implementation blocking).

Every SDK probe (see ``.github/workflows/sdk-interop.yml`` and the per-SDK
``interop_probe`` entry points under ``implementations/sdk/<lang>/``) does two
things with its own library code:

1. Signs the one fixed message in ``test-vectors/sdk/hmac.json`` through the
   SDK's canonical-signing-bytes and HMAC entry points.
2. Emits its own timestamp-generation format applied to a frozen instant with
   non-zero sub-second precision (epoch 1786789200.123456789, i.e.
   ``2026-08-15T10:20:00.123456789Z``).

Each probe prints one line to stdout::

    LCP_INTEROP {"sdk": "...", "canonical_sha256": "...", "signature_hex": "...", "emitted_timestamp": "..."}

Sub-commands:

- ``extract``   — read noisy probe stdout from stdin, validate, write clean
  JSON. A probe job that cannot produce a parseable line fails loudly here.
- ``aggregate`` — collect ``probe-<sdk>.json`` files, assert all-SDK signature
  identity against the published vector, and render the generation-shape
  matrix (job summary + ``interop-report.json`` artifact).

Blocking split (post-LEP-0002 implementation; the interop job is now a
merge gate):

- Signature identity is blocking: all ten SDKs must reproduce the published
  ``signature_hex`` over identical canonical bytes, so a break here is always
  a real defect. Exit 1.
- Missing or malformed probe output is a toolchain/pipeline break: exit 2.
  Nothing is ever silently skipped.
- Generation shape is **blocking after LEP-0002 acceptance and implementation**
  (SPEC.md §9 pins the whole-second UTC wire form; Java, Kotlin, Rust switched
  to it in the implementation PR; the LEP-0002 evidence table's ``Emitted
  form`` column is now a superseded historical record). Any shape FAIL —
  fractional, numeric-offset, unmarked, RFC-2822 emission, or the wrong
  instant under the frozen clock — exits 1. The
  ``KNOWN_FRACTIONAL_SDKS`` baseline is now the empty set: no SDK is
  permitted to emit fractional seconds.

Exit codes: 2 probe missing/unparsable, 1 signature identity broken OR
generation-shape FAIL, 0 otherwise. This is a change from the pre-acceptance
advisory contract where shape FAILs were reported but never fatal.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from typing import Any

SDK_LANGS = (
    "python",
    "typescript",
    "go",
    "java",
    "kotlin",
    "rust",
    "ruby",
    "php",
    "swift",
    "csharp",
)

# Post-LEP-0002 baseline: no SDK is permitted to emit fractional seconds.
# The LEP-0002 signer evidence table is a superseded historical record
# (state at draft time before the implementation PR); the sync test in
# ``tools/tests/test_sdk_interop_matrix.py`` asserts the caption marker is
# present whenever this baseline is empty, so a doc edit that silently
# removes the marker (or reintroduces a fractional emitter) goes red.
# Widen this set only together with an LEP reopening and a caption
# removal — never silently.
KNOWN_FRACTIONAL_SDKS: frozenset = frozenset()

# Whole-second canonical form required by LEP-0002 once accepted.
CANONICAL_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# The frozen instant every probe formats (vector timestamp + .123456789s).
FROZEN_CANONICAL = "2026-08-15T10:20:00Z"

PROBE_LINE_PREFIX = "LCP_INTEROP "
REQUIRED_FIELDS = ("sdk", "canonical_sha256", "signature_hex", "emitted_timestamp")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ProbeError(ValueError):
    """Raised when probe output is missing, unparsable, or malformed."""


def extract_probe(text: str, expected_sdk: str | None = None) -> dict[str, Any]:
    """Parse one probe result from (possibly noisy) probe stdout.

    Accepts either a line starting with ``LCP_INTEROP `` (tolerating test
    runners that add build logs around it) or a bare JSON document. Returns the
    validated probe dict; raises ``ProbeError`` otherwise.
    """
    candidates: list[str] = []
    for line in text.splitlines():
        if line.startswith(PROBE_LINE_PREFIX):
            candidates.append(line[len(PROBE_LINE_PREFIX) :])
    if not candidates:
        candidates.append(text)
    parsed: dict[str, Any] | None = None
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(loaded, dict):
            parsed = loaded
            break
    if parsed is None:
        raise ProbeError(
            f"no parseable {PROBE_LINE_PREFIX.strip()} line in probe output: {last_error}"
        )
    missing = [
        field for field in REQUIRED_FIELDS if not isinstance(parsed.get(field), str)
    ]
    if missing:
        raise ProbeError(
            f"probe output missing non-string fields: {', '.join(missing)}"
        )
    sdk = parsed["sdk"]
    if expected_sdk is not None and sdk != expected_sdk:
        raise ProbeError(f"probe claims sdk={sdk!r}, matrix expected {expected_sdk!r}")
    if sdk not in SDK_LANGS:
        raise ProbeError(
            f"probe sdk={sdk!r} is not a known SDK ({', '.join(SDK_LANGS)})"
        )
    for field in ("canonical_sha256", "signature_hex"):
        if not _HEX64.match(parsed[field]):
            raise ProbeError(f"probe {sdk}: {field} is not 64-char lowercase hex")
    return parsed


def load_vector(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        vector = json.load(handle)
    for field in (
        "id",
        "secret",
        "timestamp",
        "idempotency_key",
        "body_utf8",
        "signature_hex",
    ):
        if not isinstance(vector.get(field), str):
            raise ProbeError(f"vector {path} missing string field {field!r}")
    return vector


def canonical_sha256(vector: dict[str, Any]) -> str:
    """Reference canonical-bytes digest computed independently from the vector.

    The probes claim this value; an SDK whose claim diverges from the other
    nine diverges in canonical bytes — identity is asserted across SDKs here,
    and against the published ``signature_hex`` in ``build_matrix``.
    """
    key = vector["idempotency_key"] or ""
    prefix = f"{vector['timestamp']}\n{key}\n".encode()
    canonical = prefix + vector["body_utf8"].encode()
    return hashlib.sha256(canonical).hexdigest()


def shape_result(sdk: str, emitted: str) -> tuple[str, str]:
    """Return (status, note) for the blocking generation-shape check (LEP-0002)."""
    if CANONICAL_TIMESTAMP_RE.match(emitted):
        if emitted != FROZEN_CANONICAL:
            return (
                "FAIL",
                f"whole-second shape but wrong instant: expected {FROZEN_CANONICAL}, emitted {emitted}",
            )
        if sdk in KNOWN_FRACTIONAL_SDKS:
            aligned_note = (
                "ALIGNED: listed as fractional in the current baseline but emits "
                "canonical now — the baseline is stale, widen/remove it explicitly"
            )
            return "PASS", aligned_note
        return "PASS", ""
    note = f"fractional/non-canonical shape: {emitted!r}"
    if sdk in KNOWN_FRACTIONAL_SDKS:
        # Post-acceptance the baseline is empty; the branch only fires again
        # if an adopter reopens LEP-0002 with a widened fractional set.
        note += " (listed fractional in the current baseline)"
    else:
        note += " REGRESSION vs LEP-0002 canonical baseline (blocking)"
    return "FAIL", note


def build_matrix(
    vector: dict[str, Any], probes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Assemble the interop matrix from probe results keyed by sdk name.

    ``probes`` may omit SDKs; omitted SDKs appear as ``missing`` rows and make
    the run exit 2 — an absent probe never reads as a pass.
    """
    rows: list[dict[str, Any]] = []
    blocking: list[str] = []
    regressions: list[str] = []
    alignments: list[str] = []
    for sdk in SDK_LANGS:
        probe = probes.get(sdk)
        if probe is None:
            rows.append(
                {
                    "sdk": sdk,
                    "present": False,
                    "canonical_sha256": None,
                    "signature_hex": None,
                    "signature_matches_vector": False,
                    "emitted_timestamp": None,
                    "shape_status": None,
                    "shape_note": "",
                }
            )
            blocking.append(f"{sdk}: no probe result")
            continue
        expected_canonical = canonical_sha256(vector)
        if probe["canonical_sha256"] != expected_canonical:
            signature_matches = False
        else:
            signature_matches = probe["signature_hex"] == vector["signature_hex"]
        if not signature_matches:
            blocking.append(
                f"{sdk}: signature/canonical bytes diverge from the published vector"
            )
        status, note = shape_result(sdk, probe["emitted_timestamp"])
        if status == "FAIL" and "REGRESSION" in note:
            regressions.append(sdk)
        if status == "PASS" and sdk in KNOWN_FRACTIONAL_SDKS:
            alignments.append(sdk)
        rows.append(
            {
                "sdk": sdk,
                "present": True,
                "canonical_sha256": probe["canonical_sha256"],
                "signature_hex": probe["signature_hex"],
                "signature_matches_vector": signature_matches,
                "emitted_timestamp": probe["emitted_timestamp"],
                "shape_status": status,
                "shape_note": note,
            }
        )
    shape_counts = {
        "pass": sum(1 for row in rows if row["shape_status"] == "PASS"),
        "fail": sum(1 for row in rows if row["shape_status"] == "FAIL"),
        "missing": sum(1 for row in rows if row["shape_status"] is None),
    }
    signature_identity = not blocking
    return {
        "vector_id": vector["id"],
        "mode": "signature-identity + generation-shape blocking (LEP-0002 implementation)",
        "signature_identity": signature_identity,
        "blocking": blocking,
        "rows": rows,
        "shape_counts": shape_counts,
        "known_fractional": sorted(KNOWN_FRACTIONAL_SDKS),
        "shape_regressions": regressions,
        "shape_alignments": alignments,
        "exit_code": 2
        if any(row["shape_status"] is None for row in rows)
        else (
            1
            if (not signature_identity
                or any(row["shape_status"] == "FAIL" for row in rows))
            else 0
        ),
    }


def render_summary(matrix: dict[str, Any]) -> str:
    """Markdown for the CI job summary ($GITHUB_STEP_SUMMARY)."""
    lines = [
        f"# LCP cross-SDK signature interop — {matrix['vector_id']}",
        "",
        (
            f"**Signature identity (blocking): {'PASS' if matrix['signature_identity'] else 'FAIL'}** — "
            "all SDKs must reproduce identical canonical bytes and the published signature over the fixed vector."
        ),
        "",
        (
            f"**Timestamp generation shape (blocking after LEP-0002 implementation): "
            f"{matrix['shape_counts']['pass']} PASS / {matrix['shape_counts']['fail']} FAIL** — "
            + (
                "known fractional emitters: "
                + ", ".join("`" + s + "`" for s in matrix["known_fractional"])
                + ". "
                if matrix["known_fractional"]
                else "no SDK is listed fractional in the post-LEP-0002 baseline. "
            )
            + "Any shape FAIL exits 1 (SPEC.md §9 canonical generation is a MUST)."
        ),
        "",
        "| SDK | Signature | Canonical SHA-256 | Shape (frozen clock) | Note |",
        "|---|---|---|---|---|",
    ]
    for row in matrix["rows"]:
        if not row["present"]:
            lines.append(
                f"| {row['sdk']} | MISSING | — | — | probe produced no output (exit 2) |"
            )
            continue
        signature = "PASS" if row["signature_matches_vector"] else "FAIL"
        shape = row["shape_status"] or "—"
        note = row["shape_note"]
        lines.append(
            f"| {row['sdk']} | {signature} | `{row['canonical_sha256'][:12]}…` | {shape} | {note} |"
        )
    if matrix["shape_regressions"]:
        lines += [
            "",
            f"⚠ Shape regressions beyond the known set: {', '.join(matrix['shape_regressions'])}.",
        ]
    if matrix["shape_alignments"]:
        lines += [
            "",
            f"✔ LEP-0002 progress: previously-fractional SDKs now canonical: {', '.join(matrix['shape_alignments'])}.",
        ]
    if matrix["blocking"]:
        lines += [
            "",
            "Blocking failures:",
            *[f"- {item}" for item in matrix["blocking"]],
        ]
    return "\n".join(lines) + "\n"


def cmd_extract(args: argparse.Namespace) -> int:
    text = sys.stdin.read()
    try:
        probe = extract_probe(text, expected_sdk=args.sdk)
    except ProbeError as exc:
        print(f"interop probe extract failed: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(probe, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    else:
        print(payload)
    return 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    vector = load_vector(args.vector)
    probes: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(args.probes, "probe-*.json"))):
        sdk = os.path.basename(path)[len("probe-") : -len(".json")]
        try:
            with open(path, encoding="utf-8") as handle:
                probes[sdk] = extract_probe(handle.read(), expected_sdk=sdk)
        except ProbeError as exc:
            print(f"interop probe file {path} invalid: {exc}", file=sys.stderr)
            return 2
    unknown = sorted(set(probes) - set(SDK_LANGS))
    if unknown:
        print(
            f"unexpected probe files for unknown SDKs: {', '.join(unknown)}",
            file=sys.stderr,
        )
        return 2
    matrix = build_matrix(vector, probes)
    summary = render_summary(matrix)
    if args.summary == "-":
        print(summary)
    else:
        with open(args.summary, "w", encoding="utf-8") as handle:
            handle.write(summary)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump(matrix, handle, indent=2, sort_keys=True)
            handle.write("\n")
    for item in matrix["blocking"]:
        print(
            f"::error::{item}"
            if os.environ.get("GITHUB_ACTIONS")
            else f"BLOCKING: {item}",
            file=sys.stderr,
        )
    shape_fail_sdks = [row["sdk"] for row in matrix["rows"] if row["shape_status"] == "FAIL"]
    if shape_fail_sdks:
        print(
            "blocking: generation-shape FAIL for "
            + ", ".join(shape_fail_sdks)
            + " — SPEC.md §9 canonical generation is a MUST; see the matrix summary",
            file=sys.stderr,
        )
    return matrix["exit_code"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser(
        "extract", help="validate one probe stdout stream; write clean JSON"
    )
    extract.add_argument("--sdk", help="reject output claiming a different sdk")
    extract.add_argument("--out", help="write JSON here instead of stdout")
    extract.set_defaults(func=cmd_extract)
    aggregate = sub.add_parser(
        "aggregate", help="build signature-identity + shape matrix"
    )
    aggregate.add_argument("--vector", required=True)
    aggregate.add_argument(
        "--probes", required=True, help="directory containing probe-<sdk>.json"
    )
    aggregate.add_argument(
        "--summary", default="-", help="markdown summary path, or - for stdout"
    )
    aggregate.add_argument("--report", help="write interop-report.json here")
    aggregate.set_defaults(func=cmd_aggregate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

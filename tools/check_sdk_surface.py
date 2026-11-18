#!/usr/bin/env python3
"""SDK public-surface drift gate.

Follows the batch-helper rollout failure mode: an operation landed by hand in
ten SDKs, so the next one can miss a language and nothing notices. The manifest
(``tools/sdk_surface.json``) declares, per operation and per language, the exact
public member name and the file that must define it.

For every declared member this gate asserts the name occurs in the declared
file, case-sensitively, and prints a per-operation x per-SDK matrix.

Method names alone cannot show that an SDK retries, bounds its requests, or
verifies in constant time, so the optional ``behaviours`` section adds one row
per cross-SDK behaviour. Each SDK lists ``evidence``: files and the exact
substrings each must contain (the primitive in the source and the test that
exercises it). Every substring must be present; a missing one fails the run
just like a missing operation member.

Members declared as ``null`` are *recorded gaps*: the protocol operation exists
but that SDK does not expose it yet. Gaps are counted and listed but do not fail
the run (``--fail-on-gap`` flips that, like ``check_coverage_matrix.py``), so the
honest backlog is visible instead of silently forgotten. A declared member that
is absent from a present file, or a missing file, is a hard failure.

Exit codes: 0 clean, 1 a declared member is missing, 2 the manifest is unusable.

Only the standard library is used; the run is deterministic and network-free.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path("tools") / "sdk_surface.json"

STATUS_PRESENT = "present"
STATUS_GAP = "gap"
STATUS_MISSING = "missing"

# Matrix cell text; kept short so ten columns fit a 100-column terminal.
CELL = {STATUS_PRESENT: "ok", STATUS_GAP: "GAP", STATUS_MISSING: "MISS"}


class SurfaceError(Exception):
    """The manifest itself is malformed; exit 2."""


class Entry(NamedTuple):
    operation: str
    sdk: str
    member: str | None
    file: str
    status: str
    note: str


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SurfaceError(f"cannot read manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SurfaceError(f"manifest {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SurfaceError(f"manifest {path} must be a JSON object")
    if data.get("schema_version") != 1:
        raise SurfaceError(
            f"manifest {path} has schema_version {data.get('schema_version')!r}, expected 1"
        )
    operations = data.get("operations")
    if not isinstance(operations, list) or not operations:
        raise SurfaceError(f"manifest {path} has no 'operations' list")
    return data


def sdk_directories(root: Path, sdk_root: str) -> list[str]:
    base = root / sdk_root
    if not base.is_dir():
        raise SurfaceError(f"SDK root {base} is not a directory")
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())


def _require_coverage(
    kind: str, item_id: str, members: Any, known_sdks: list[str], sdk_root: str
) -> None:
    if not isinstance(members, dict):
        raise SurfaceError(f"{kind} {item_id!r} has no 'members' object")
    declared = sorted(members)
    if declared != known_sdks:
        missing = sorted(set(known_sdks) - set(members))
        extra = sorted(set(members) - set(known_sdks))
        detail = []
        if missing:
            detail.append(f"no entry for {', '.join(missing)}")
        if extra:
            detail.append(f"unknown SDK {', '.join(extra)}")
        raise SurfaceError(
            f"{kind} {item_id!r} does not cover the SDK directories "
            f"under {sdk_root}: {'; '.join(detail)}"
        )


def _note(item_id: str, sdk: str, spec: dict[str, Any]) -> str:
    note = spec.get("note") or ""
    if not isinstance(note, str):
        raise SurfaceError(f"{item_id!r} / {sdk}: 'note' must be a string")
    return note


def _operation_entry(
    op_id: str, sdk: str, spec: Any, sdk_dir: Path
) -> Entry:
    if not isinstance(spec, dict):
        raise SurfaceError(f"operation {op_id!r} / {sdk}: entry must be an object")
    rel = spec.get("file")
    if not isinstance(rel, str) or not rel:
        raise SurfaceError(f"operation {op_id!r} / {sdk}: no 'file'")
    member = spec.get("name")
    if member is not None and (not isinstance(member, str) or not member):
        raise SurfaceError(
            f"operation {op_id!r} / {sdk}: 'name' must be a non-empty string or null"
        )
    note = _note(op_id, sdk, spec)
    if member is None:
        return Entry(op_id, sdk, member, rel, STATUS_GAP, note)
    try:
        text = (sdk_dir / rel).read_text(encoding="utf-8")
    except OSError:
        return Entry(op_id, sdk, member, rel, STATUS_MISSING, note)
    status = STATUS_PRESENT if member in text else STATUS_MISSING
    return Entry(op_id, sdk, member, rel, status, note)


def _behaviour_entry(
    behaviour_id: str, sdk: str, spec: Any, sdk_dir: Path
) -> Entry:
    """Resolve one SDK's evidence list; the first absent substring is reported."""
    if not isinstance(spec, dict):
        raise SurfaceError(
            f"behaviour {behaviour_id!r} / {sdk}: entry must be an object"
        )
    note = _note(behaviour_id, sdk, spec)
    evidence = spec.get("evidence")
    if evidence is None:
        return Entry(behaviour_id, sdk, None, "-", STATUS_GAP, note)
    if not isinstance(evidence, list) or not evidence:
        raise SurfaceError(
            f"behaviour {behaviour_id!r} / {sdk}: 'evidence' must be a non-empty "
            "list or null"
        )
    for item in evidence:
        rel = item.get("file") if isinstance(item, dict) else None
        needles = item.get("contains") if isinstance(item, dict) else None
        if (
            not isinstance(rel, str)
            or not rel
            or not isinstance(needles, list)
            or not needles
            or not all(isinstance(n, str) and n for n in needles)
        ):
            raise SurfaceError(
                f"behaviour {behaviour_id!r} / {sdk}: each evidence item needs a "
                "'file' and a non-empty 'contains' list of strings"
            )
        try:
            text = (sdk_dir / rel).read_text(encoding="utf-8")
        except OSError:
            return Entry(behaviour_id, sdk, needles[0], rel, STATUS_MISSING, note)
        for needle in needles:
            if needle not in text:
                return Entry(behaviour_id, sdk, needle, rel, STATUS_MISSING, note)
    first = evidence[0]
    return Entry(behaviour_id, sdk, first["contains"][0], first["file"], STATUS_PRESENT, note)


def build_entries(
    manifest: dict[str, Any], root: Path
) -> tuple[list[Entry], list[str], list[str]]:
    """Resolve every declared member and behaviour against the tree.

    Returns the entries, the operation ids and the behaviour ids, in manifest
    order. Raises SurfaceError when the manifest is structurally wrong or does
    not cover exactly the SDK directories that exist.
    """
    sdk_root = manifest.get("sdk_root", "implementations/sdk")
    if not isinstance(sdk_root, str):
        raise SurfaceError("manifest 'sdk_root' must be a string")
    known_sdks = sdk_directories(root, sdk_root)

    behaviours = manifest.get("behaviours", [])
    if not isinstance(behaviours, list):
        raise SurfaceError("manifest 'behaviours' must be a list")

    entries: list[Entry] = []
    operation_ids: list[str] = []
    behaviour_ids: list[str] = []
    for kind, items, ids, resolve in (
        ("operation", manifest["operations"], operation_ids, _operation_entry),
        ("behaviour", behaviours, behaviour_ids, _behaviour_entry),
    ):
        for item in items:
            if not isinstance(item, dict):
                raise SurfaceError(f"{kind} entry is not an object: {item!r}")
            item_id = item.get("id")
            if not isinstance(item_id, str) or not item_id:
                raise SurfaceError(f"{kind} entry has no 'id': {item!r}")
            if item_id in operation_ids or item_id in behaviour_ids:
                raise SurfaceError(f"duplicate {kind} id {item_id!r}")
            ids.append(item_id)
            members = item.get("members")
            _require_coverage(kind, item_id, members, known_sdks, sdk_root)
            for sdk in known_sdks:
                entries.append(resolve(item_id, sdk, members[sdk], root / sdk_root / sdk))

    return entries, operation_ids, behaviour_ids


def counts(entries: list[Entry]) -> dict[str, int]:
    tally = {STATUS_PRESENT: 0, STATUS_GAP: 0, STATUS_MISSING: 0}
    for entry in entries:
        tally[entry.status] += 1
    return tally


def cell_map(entries: list[Entry]) -> dict[str, dict[str, str]]:
    """operation -> sdk -> matrix cell, kept in manifest order."""
    cells: dict[str, dict[str, str]] = {}
    for entry in entries:
        cells.setdefault(entry.operation, {})[entry.sdk] = CELL[entry.status]
    return cells


def format_matrix(cells: dict[str, dict[str, str]], sdks: list[str]) -> str:
    width = max([len(operation) for operation in cells] + [len("operation")]) + 2
    col = max([len(sdk) for sdk in sdks] + [len(CELL[STATUS_PRESENT])]) + 2
    rows = ["operation".ljust(width) + "".join(sdk.rjust(col) for sdk in sdks).rstrip()]
    for operation, by_sdk in cells.items():
        rows.append(
            operation.ljust(width)
            + "".join(by_sdk.get(sdk, "-").rjust(col) for sdk in sdks)
        )
    return "\n".join(rows).rstrip()


def report(
    entries: list[Entry],
    cells: dict[str, dict[str, str]],
    operation_ids: list[str],
    behaviour_ids: list[str],
    sdks: list[str],
) -> dict[str, Any]:
    missing = [
        {"operation": e.operation, "sdk": e.sdk, "member": e.member, "file": e.file}
        for e in entries
        if e.status == STATUS_MISSING
    ]
    gaps = [
        {"operation": e.operation, "sdk": e.sdk, "file": e.file, "note": e.note}
        for e in entries
        if e.status == STATUS_GAP
    ]
    return {
        "operations": operation_ids,
        "behaviours": behaviour_ids,
        "sdks": sdks,
        "counts": {**counts(entries), "total": len(entries)},
        "per_sdk": {
            sdk: {
                status: sum(1 for e in entries if e.sdk == sdk and e.status == status)
                for status in (STATUS_PRESENT, STATUS_GAP, STATUS_MISSING)
            }
            for sdk in sdks
        },
        "cells": cells,
        "missing": missing,
        "gaps": gaps,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check each LCP SDK exposes its declared public surface."
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help=f"surface manifest (default: <root>/{DEFAULT_MANIFEST})",
    )
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="repository root the manifest paths resolve against",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    parser.add_argument(
        "--fail-on-gap",
        action="store_true",
        help="exit non-zero when any recorded gap exists",
    )
    options = parser.parse_args(argv)

    root = Path(options.root).resolve()
    manifest_path = (
        Path(options.manifest).resolve()
        if options.manifest
        else root / DEFAULT_MANIFEST
    )

    try:
        manifest = load_manifest(manifest_path)
        entries, operation_ids, behaviour_ids = build_entries(manifest, root)
    except SurfaceError as exc:
        if options.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"sdk surface check failed:\n- {exc}")
        return 2

    sdks = sdk_directories(root, manifest.get("sdk_root", "implementations/sdk"))
    cells = cell_map(entries)
    data = report(entries, cells, operation_ids, behaviour_ids, sdks)
    tally = data["counts"]

    if options.json:
        print(json.dumps(data, indent=2))
    else:
        print(format_matrix(cells, sdks))
        print()
        print(
            "sdks: "
            + ", ".join(
                f"{sdk} {by_status[STATUS_PRESENT]}/{sum(by_status.values())}"
                for sdk, by_status in data["per_sdk"].items()
            )
        )
        print(
            f"({len(operation_ids)} operations + {len(behaviour_ids)} behaviours) x {len(sdks)} SDKs = {tally['total']} cells — "
            f"{tally[STATUS_PRESENT]} present, {tally[STATUS_GAP]} gap, {tally[STATUS_MISSING]} missing"
        )
        if data["gaps"]:
            print("\nrecorded gaps (protocol operation or behaviour the SDK does not ship yet):")
            for gap in data["gaps"]:
                note = f" — {gap['note']}" if gap["note"] else ""
                print(f"- {gap['sdk']} {gap['operation']} ({gap['file']}){note}")
        if data["missing"]:
            print("\nmissing members or behaviour evidence:")
            for item in data["missing"]:
                print(
                    f"- {item['sdk']} {item['operation']}: {item['member']!r} not found in {item['file']}"
                )

    if tally[STATUS_MISSING]:
        return 1
    if options.fail_on_gap and tally[STATUS_GAP]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""SDK public-surface drift gate.

Follows the batch-helper rollout failure mode: an operation landed by hand in
ten SDKs, so the next one can miss a language and nothing notices. The manifest
(``tools/sdk_surface.json``) declares, per operation and per language, the exact
public member name and the file that must define it.

For every declared member this gate asserts the name occurs in the declared
file, case-sensitively, and prints a per-operation x per-SDK matrix.

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


def build_entries(
    manifest: dict[str, Any], root: Path
) -> tuple[list[Entry], list[str]]:
    """Resolve every declared member against the tree.

    Returns the entries and the operation ids, in manifest order. Raises
    SurfaceError when the manifest is structurally wrong or does not cover
    exactly the SDK directories that exist.
    """
    sdk_root = manifest.get("sdk_root", "implementations/sdk")
    if not isinstance(sdk_root, str):
        raise SurfaceError("manifest 'sdk_root' must be a string")
    known_sdks = sdk_directories(root, sdk_root)

    entries: list[Entry] = []
    operation_ids: list[str] = []
    for operation in manifest["operations"]:
        if not isinstance(operation, dict):
            raise SurfaceError(f"operation entry is not an object: {operation!r}")
        op_id = operation.get("id")
        if not isinstance(op_id, str) or not op_id:
            raise SurfaceError(f"operation entry has no 'id': {operation!r}")
        if op_id in operation_ids:
            raise SurfaceError(f"duplicate operation id {op_id!r}")
        operation_ids.append(op_id)

        members = operation.get("members")
        if not isinstance(members, dict):
            raise SurfaceError(f"operation {op_id!r} has no 'members' object")
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
                f"operation {op_id!r} does not cover the SDK directories "
                f"under {sdk_root}: {'; '.join(detail)}"
            )

        for sdk in known_sdks:
            spec = members[sdk]
            if not isinstance(spec, dict):
                raise SurfaceError(
                    f"operation {op_id!r} / {sdk}: entry must be an object"
                )
            rel = spec.get("file")
            if not isinstance(rel, str) or not rel:
                raise SurfaceError(f"operation {op_id!r} / {sdk}: no 'file'")
            member = spec.get("name")
            if member is not None and (not isinstance(member, str) or not member):
                raise SurfaceError(
                    f"operation {op_id!r} / {sdk}: 'name' must be a non-empty "
                    "string or null"
                )
            note = spec.get("note") or ""
            if not isinstance(note, str):
                raise SurfaceError(
                    f"operation {op_id!r} / {sdk}: 'note' must be a string"
                )

            path = root / sdk_root / sdk / rel
            if member is None:
                status = STATUS_GAP
            else:
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    status = STATUS_MISSING
                else:
                    status = STATUS_PRESENT if member in text else STATUS_MISSING
            entries.append(Entry(op_id, sdk, member, rel, status, note))

    return entries, operation_ids


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
        entries, operation_ids = build_entries(manifest, root)
    except SurfaceError as exc:
        if options.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"sdk surface check failed:\n- {exc}")
        return 2

    sdks = sdk_directories(root, manifest.get("sdk_root", "implementations/sdk"))
    cells = cell_map(entries)
    data = report(entries, cells, operation_ids, sdks)
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
            f"{len(operation_ids)} operations x {len(sdks)} SDKs = {tally['total']} cells — "
            f"{tally[STATUS_PRESENT]} present, {tally[STATUS_GAP]} gap, {tally[STATUS_MISSING]} missing"
        )
        if data["gaps"]:
            print("\nrecorded gaps (protocol operation, no SDK member yet):")
            for gap in data["gaps"]:
                note = f" — {gap['note']}" if gap["note"] else ""
                print(f"- {gap['sdk']} {gap['operation']} ({gap['file']}){note}")
        if data["missing"]:
            print("\nmissing members:")
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

#!/usr/bin/env python3
"""Offline retention archive: export rows to deterministic JSONL, restore them.

Standard library only, and deliberately independent of the platform package:
this tool runs on a backup host against a copied database, where importing the
service would drag in schema and crypto dependencies that have no business
being present.

Commands
--------
``export``
    Write one table (repeatable) older than ``--before`` to JSONL, sorted by
    primary key and serialized canonically, so the same database state always
    produces the same bytes. Alongside it, ``<out>.manifest.json`` records the
    SHA-256 of the JSONL, the per-table row counts, the table DDL needed to
    restore, and how many rows were refused because their subject is erased.

    Rows whose subject (``lead_id``) is in the erasure set are **never**
    archived: an erasure must not survive in a new export. The erasure set is
    read from the source database (``leads.status = 'ERASED'``) and may be
    extended with ``--erasure-set FILE``.

    Every successful export appends its digest to a ledger
    (``--ledger``, default ``<out>.ledger.jsonl``). That ledger is the
    operator's record of which archives exist and which window they cover.

``import``
    Restore an archive into a scratch database for dispute or recovery. The
    manifest's digest is verified first; a digest mismatch, a truncated file,
    a line-count mismatch, or a malformed line is refused before a single row
    is written.

``verify``
    Check an archive against its manifest without touching any database.

Erasure across archives
------------------------
Archives are files. An erasure therefore propagates in two steps, both of
which this tool supports deliberately rather than implicitly:

1. Before an erasure lands, exports refuse erased subjects (this tool), so new
   archives cannot contain them.
2. After an erasure lands, the operator must destroy every archive whose
   window covers the subject — the ledger names them — and re-export from the
   live database if the history is still needed under retention. Documents
   that step is `docs/OPERATIONS.md` ("Retention, archive, and restore").
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ARCHIVE_VERSION = 1

# Tables this tool knows how to archive, with the column that carries the
# consumer subject (``None`` for an audit row, whose subject depends on
# ``resource_type``) and the column that carries the row's age.
TABLE_SPECS: dict[str, dict[str, str]] = {
    "leads": {"subject": "lead_id", "time": "created_at"},
    "pings": {"subject": "lead_id", "time": "created_at"},
    "bids": {"subject": "lead_id", "time": "received_at"},
    "deliveries": {"subject": "lead_id", "time": "created_at"},
    "lifecycle_events": {"subject": "lead_id", "time": "timestamp"},
    "event_stream": {"subject": "lead_id", "time": "created_at"},
    "match_decisions": {"subject": "lead_id", "time": "created_at"},
    "match_audits": {"subject": "lead_id", "time": "created_at"},
    "audit_events": {"subject": "resource_id", "time": "created_at"},
}


class ArchiveError(RuntimeError):
    """A refusal: the archive was not produced, or was not trusted."""


def _canonical_time(value: str) -> str:
    """Normalize an ISO date or timestamp to the stored ``...Z`` text form."""
    text = value.strip()
    if not text:
        raise ArchiveError("--before must not be empty")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArchiveError(
            f"--before must be an ISO-8601 date or timestamp, got {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _encode_value(value: Any) -> Any:
    """JSON-safe value; blobs are tagged so they cannot be mistaken for text."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__blob__": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"__blob__"}:
        return base64.b64decode(value["__blob__"])
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_erasure_set(path: Path | None) -> set[str]:
    if path is None:
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ArchiveError(f"cannot read erasure set {path}: {exc}") from exc
    subjects = set()
    for line in lines:
        entry = line.strip()
        if entry and not entry.startswith("#"):
            subjects.add(entry)
    return subjects


def _erased_subjects(connection: sqlite3.Connection) -> set[str]:
    """Subjects the live database records as erased."""
    subjects: set[str] = set()
    try:
        rows = connection.execute(
            "SELECT lead_id FROM leads WHERE status = 'ERASED'"
        ).fetchall()
        subjects.update(str(row[0]) for row in rows if row[0])
    except sqlite3.Error:
        pass
    try:
        rows = connection.execute(
            "SELECT resource_id FROM audit_events "
            "WHERE action = 'lead.erased' AND resource_type = 'lead'"
        ).fetchall()
        subjects.update(str(row[0]) for row in rows if row[0])
    except sqlite3.Error:
        pass
    return subjects


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def _primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    keyed = sorted((row[5], row[1]) for row in rows if row[5])
    return [name for _position, name in keyed] or _columns(connection, table)


def _table_ddl(connection: sqlite3.Connection, table: str) -> str | None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _subject_of(table: str, row: dict[str, Any]) -> str | None:
    subject_column = TABLE_SPECS[table]["subject"]
    if table == "audit_events" and row.get("resource_type") != "lead":
        return None
    value = row.get(subject_column)
    return str(value) if value is not None else None


def export_table(
    connection: sqlite3.Connection,
    table: str,
    before: str,
    erasures: set[str],
) -> tuple[list[str], dict[str, Any], int]:
    """Return the JSONL lines for one table, its manifest entry, and refusals."""
    spec = TABLE_SPECS[table]
    columns = _columns(connection, table)
    if not columns:
        raise ArchiveError(f"source database has no table {table!r}")
    key = _primary_key(connection, table)
    order = ", ".join(key)
    rows = connection.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {spec['time']} < ? "
        f"ORDER BY {order}",
        (before,),
    ).fetchall()
    lines: list[str] = []
    refused = 0
    for raw in rows:
        row = {
            column: _encode_value(raw[index]) for index, column in enumerate(columns)
        }
        subject = _subject_of(table, row)
        if subject is not None and subject in erasures:
            # Refuse, never redact-on-the-fly: a partially rewritten archive row
            # would be indistinguishable from one that never held the subject.
            refused += 1
            continue
        key_value = "|".join(str(row[column]) for column in key)
        lines.append(
            json.dumps(
                {"table": table, "key": key_value, "row": row},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
    entry = {
        "table": table,
        "columns": columns,
        "rows": len(lines),
        "refused_erased": refused,
        "sha256": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest(),
        "ddl": _table_ddl(connection, table),
    }
    return lines, entry, refused


def _connect_readonly(database: Path) -> sqlite3.Connection:
    uri = f"file:{database}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ArchiveError(f"cannot open {database} read-only: {exc}") from exc
    connection.text_factory = str
    return connection


def command_export(args: argparse.Namespace) -> int:
    before = _canonical_time(args.before)
    database = Path(args.database)
    if not database.exists():
        raise ArchiveError(f"source database {database} does not exist")
    erasures = _read_erasure_set(Path(args.erasure_set) if args.erasure_set else None)
    connection = _connect_readonly(database)
    try:
        erasures |= _erased_subjects(connection)
        lines: list[str] = []
        entries = []
        refused_total = 0
        for table in args.table:
            if table not in TABLE_SPECS:
                raise ArchiveError(
                    f"unsupported table {table!r}; known tables: "
                    + ", ".join(sorted(TABLE_SPECS))
                )
            table_lines, entry, refused = export_table(
                connection, table, before, erasures
            )
            lines.extend(table_lines)
            entries.append(entry)
            refused_total += refused
    finally:
        connection.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{line}\n" for line in lines)
    out.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest = {
        "archive_version": ARCHIVE_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(database),
        "before": before,
        "tables": entries,
        "rows": len(lines),
        "refused_erased": refused_total,
        "erasure_set_size": len(erasures),
        "sha256": digest,
    }
    manifest_path = Path(f"{out}.manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _append_ledger(args, out, manifest)
    print(
        json.dumps(
            {
                "out": str(out),
                "manifest": str(manifest_path),
                "rows": len(lines),
                "refused_erased": refused_total,
                "sha256": digest,
            },
            sort_keys=True,
        )
    )
    return 0


def _append_ledger(
    args: argparse.Namespace, out: Path, manifest: dict[str, Any]
) -> None:
    """Record the archive digest so an erasure can find the archives it covers."""
    ledger = (
        Path(args.ledger)
        if getattr(args, "ledger", None)
        else Path(f"{out}.ledger.jsonl")
    )
    ledger.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "archive": str(out.resolve()),
        "manifest": str(Path(f"{out}.manifest.json").resolve()),
        "created_at": manifest["created_at"],
        "before": manifest["before"],
        "tables": [table["table"] for table in manifest["tables"]],
        "rows": manifest["rows"],
        "refused_erased": manifest["refused_erased"],
        "sha256": manifest["sha256"],
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _load_manifest(path: Path) -> tuple[Path, dict[str, Any]]:
    """Resolve the archive/manifest pair and require it to be intact."""
    if path.suffix == ".json" or path.name.endswith(".manifest.json"):
        manifest_path = path
        archive_path = Path(str(path)[: -len(".manifest.json")])
        if archive_path == path:
            raise ArchiveError(f"{path} is not a manifest file")
    else:
        archive_path = path
        manifest_path = Path(f"{path}.manifest.json")
    if not manifest_path.exists():
        raise ArchiveError(
            f"missing manifest {manifest_path}: refusing an unverifiable archive"
        )
    if not archive_path.exists():
        raise ArchiveError(f"missing archive file {archive_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"unreadable manifest {manifest_path}: {exc}") from exc
    return archive_path, manifest


def _verified_records(
    archive_path: Path, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    """Verify the archive against its manifest before returning its rows."""
    actual = _sha256_file(archive_path)
    if actual != manifest.get("sha256"):
        raise ArchiveError(
            f"refusing a partial or modified archive: digest mismatch for "
            f"{archive_path} (manifest {manifest.get('sha256')} != file {actual})"
        )
    text = archive_path.read_text(encoding="utf-8")
    if text and not text.endswith("\n"):
        raise ArchiveError(f"{archive_path} is truncated (no terminating newline)")
    records = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArchiveError(
                f"malformed line {lineno} in {archive_path}: refusing a partial file"
            ) from exc
        if not {"table", "row"} <= set(record):
            raise ArchiveError(
                f"line {lineno} in {archive_path} is not an archive record"
            )
        records.append(record)
    if len(records) != manifest.get("rows"):
        raise ArchiveError(
            f"{archive_path} holds {len(records)} rows, manifest says "
            f"{manifest.get('rows')}: refusing a partial file"
        )
    per_table: dict[str, int] = {}
    for record in records:
        per_table[record["table"]] = per_table.get(record["table"], 0) + 1
    for entry in manifest.get("tables", []):
        if per_table.get(entry["table"], 0) != entry["rows"]:
            raise ArchiveError(
                f"table {entry['table']} holds {per_table.get(entry['table'], 0)} rows, "
                f"manifest says {entry['rows']}: refusing a partial file"
            )
    return records


def command_verify(args: argparse.Namespace) -> int:
    archive_path, manifest = _load_manifest(Path(args.file))
    records = _verified_records(archive_path, manifest)
    print(
        json.dumps(
            {
                "archive": str(archive_path),
                "verified": True,
                "rows": len(records),
                "sha256": manifest.get("sha256"),
                "before": manifest.get("before"),
            },
            sort_keys=True,
        )
    )
    return 0


def command_import(args: argparse.Namespace) -> int:
    archive_path, manifest = _load_manifest(Path(args.file))
    records = _verified_records(archive_path, manifest)
    destination = Path(args.database)
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(destination))
    try:
        for entry in manifest.get("tables", []):
            ddl = entry.get("ddl")
            if not ddl:
                continue
            connection.execute(
                ddl.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1)
            )
        for record in records:
            table = record["table"]
            if table not in TABLE_SPECS:
                raise ArchiveError(f"archive names unknown table {table!r}")
            row = {
                column: _decode_value(value) for column, value in record["row"].items()
            }
            columns = list(row)
            placeholders = ", ".join("?" for _ in columns)
            connection.execute(
                f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                [row[column] for column in columns],
            )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise ArchiveError(f"restore into {destination} failed: {exc}") from exc
    finally:
        connection.close()
    print(
        json.dumps(
            {
                "database": str(destination),
                "rows": len(records),
                "sha256": manifest.get("sha256"),
                "tables": sorted({record["table"] for record in records}),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archive_store.py",
        description=(
            "Export retention-bounded LCP platform rows to deterministic JSONL "
            "with a SHA-256 manifest, restore them into a scratch database, or "
            "verify an existing archive. Standard library only."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="write rows older than --before to a JSONL archive"
    )
    export_parser.add_argument(
        "--database",
        default="./data/lcp.sqlite3",
        help="source SQLite database (opened read-only)",
    )
    export_parser.add_argument(
        "--table",
        action="append",
        required=True,
        help="table to archive; repeat for several (leads, pings, bids, deliveries, "
        "lifecycle_events, event_stream, match_decisions, match_audits, audit_events)",
    )
    export_parser.add_argument(
        "--before", required=True, help="ISO-8601 date or timestamp window end"
    )
    export_parser.add_argument("--out", required=True, help="JSONL archive path")
    export_parser.add_argument(
        "--erasure-set",
        default=None,
        help="file of lead ids whose rows must never be archived (one per line)",
    )
    export_parser.add_argument(
        "--ledger",
        default=None,
        help="append-only archive ledger path (default: <out>.ledger.jsonl)",
    )
    export_parser.set_defaults(handler=command_export)

    import_parser = subparsers.add_parser(
        "import", help="verify an archive and restore it into a scratch database"
    )
    import_parser.add_argument("--file", required=True, help="JSONL archive path")
    import_parser.add_argument(
        "--database", required=True, help="scratch SQLite database to restore into"
    )
    import_parser.set_defaults(handler=command_import)

    verify_parser = subparsers.add_parser(
        "verify", help="check an archive against its manifest without restoring"
    )
    verify_parser.add_argument("--file", required=True, help="JSONL archive path")
    verify_parser.set_defaults(handler=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ArchiveError as exc:
        print(f"archive_store: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

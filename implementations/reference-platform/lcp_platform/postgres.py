"""Postgres backend for production deployments.

The platform deliberately keeps persistence behind the Store method boundary so
operators can use SQLite locally and Postgres in multi-node production.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import RLock
from typing import Any, Iterator

from .crypto import EnvelopeCipher
from .storage import Store, now_iso


def _decode_text(value: Any) -> Any:
    """Normalize psycopg binary text results across psycopg/libpq builds."""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    return value


def _decoded_dict_row(cursor: Any):
    if cursor.description is None:
        return lambda values: values
    names = [column.name for column in cursor.description]

    def make_row(values: Any) -> dict[str, Any]:
        return {name: _decode_text(value) for name, value in zip(names, values)}

    return make_row


class _PostgresConnection:
    def __init__(self, connection: Any):
        self.raw = connection

    @staticmethod
    def _sql(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: Any = None):
        from psycopg.pq import TransactionStatus

        statement = self._sql(sql)
        # Reads execute in autocommit mode. Only create a savepoint when the
        # caller is inside an explicit transaction; otherwise PostgreSQL
        # rejects SAVEPOINT before the actual statement is run.
        in_transaction = self.raw.info.transaction_status == TransactionStatus.INTRANS
        if not in_transaction:
            return self.raw.execute(statement, params or ())

        # Savepoints keep a uniqueness error from poisoning the outer
        # transaction, matching the SQLite store's IntegrityError behavior.
        self.raw.execute("SAVEPOINT lcp_statement")
        try:
            cursor = self.raw.execute(statement, params or ())
        except Exception as exc:
            self.raw.execute("ROLLBACK TO SAVEPOINT lcp_statement")
            self.raw.execute("RELEASE SAVEPOINT lcp_statement")
            # Store methods intentionally catch sqlite3.IntegrityError for the
            # SQLite backend. Translate only PostgreSQL unique violations;
            # syntax, connection, and policy failures must remain visible.
            if getattr(exc, "sqlstate", None) == "23505":
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise
        else:
            self.raw.execute("RELEASE SAVEPOINT lcp_statement")
            return cursor

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)


class PostgresStore(Store):
    """Postgres implementation of the platform Store contract.

    A store instance is shared by the WSGI threads in one process and protects
    its connection with a lock. Run multiple API processes and worker
    processes, as shown in the Kubernetes example, for horizontal capacity.
    """

    def __init__(self, dsn: str, *, pii_encryption_key: str | bytes | None = None):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Postgres support requires the lcp-reference-platform[postgres] extra"
            ) from exc
        self.path = dsn
        self._cipher = EnvelopeCipher(pii_encryption_key)
        self._lock = RLock()
        self._raw = psycopg.connect(dsn, row_factory=_decoded_dict_row)
        # Store read methods use short implicit statements while write methods
        # open explicit transactions. Autocommit prevents a standalone SELECT
        # from poisoning the next explicit BEGIN on this shared connection.
        self._raw.autocommit = True
        self._connection = _PostgresConnection(self._raw)
        self.initialize()

    @contextmanager
    def transaction(self) -> Iterator[_PostgresConnection]:
        with self._lock:
            # Use psycopg's transaction manager rather than issuing a bare
            # BEGIN/COMMIT while autocommit is enabled. This keeps explicit
            # transactions reliable after standalone read statements and
            # preserves the savepoint behavior in _PostgresConnection.
            with self._raw.transaction():
                yield self._connection

    def _migrate_sqlite_columns(self, db: _PostgresConnection) -> None:
        db.execute(
            "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'"
        )
        db.execute(
            "ALTER TABLE credentials ADD COLUMN IF NOT EXISTS scopes_json TEXT NOT NULL DEFAULT '[\"*\"]'"
        )
        db.execute(
            "ALTER TABLE leads ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'"
        )
        db.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS expires_at TEXT")
        db.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS consent_expires_at TEXT")
        db.execute("ALTER TABLE leads ADD COLUMN IF NOT EXISTS suppressed INTEGER NOT NULL DEFAULT 0")
        db.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'default'")
        db.execute("ALTER TABLE offers ADD COLUMN IF NOT EXISTS vertical TEXT")
        for row in db.execute("SELECT offer_id, offer_json FROM offers").fetchall():
            stored_offer = json.loads(_decode_text(row["offer_json"]))
            db.execute(
                "UPDATE offers SET tenant_id = ?, vertical = ? WHERE offer_id = ?",
                (
                    str(stored_offer.get("tenant_id", "default")),
                    stored_offer.get("vertical"),
                    row["offer_id"],
                ),
            )
        db.execute("CREATE INDEX IF NOT EXISTS idx_offers_discovery ON offers(active, tenant_id, vertical, offer_id)")
        db.execute("ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS lease_owner TEXT")
        db.execute("ALTER TABLE deliveries ADD COLUMN IF NOT EXISTS lease_until TEXT")
        db.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS residency TEXT NOT NULL DEFAULT 'TEST'")
        db.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS scan_status TEXT NOT NULL DEFAULT 'not_scanned'")
        db.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS scan_engine TEXT NOT NULL DEFAULT 'unknown'")
        db.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS scanned_at TEXT NOT NULL DEFAULT ''")
        db.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS encryption TEXT NOT NULL DEFAULT 'application_encrypted'")
        db.execute("ALTER TABLE attachments ADD COLUMN IF NOT EXISTS expires_at TEXT")

    def close(self) -> None:
        with self._lock:
            self._raw.close()

    def list_offers(
        self,
        vertical: str | None = None,
        *,
        tenant_id: str | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        query = "SELECT offer_json FROM offers"
        clauses: list[str] = []
        values: list[Any] = []
        if active_only:
            clauses.append("active = 1")
        if vertical:
            clauses.append("vertical = ?")
            values.append(vertical)
        if tenant_id:
            clauses.append("tenant_id = ?")
            values.append(tenant_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY offer_id"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [json.loads(_decode_text(row["offer_json"])) for row in rows]

    def claim_routing_jobs(
        self,
        worker_id: str,
        *,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> list[Any]:
        current = datetime.now(timezone.utc)
        now_value = current.strftime("%Y-%m-%dT%H:%M:%SZ")
        lease_until = (current + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self.transaction() as db:
            rows = db.execute(
                """
                SELECT * FROM routing_jobs
                WHERE status = 'PENDING'
                   OR (status = 'PROCESSING' AND lease_until <= ?)
                ORDER BY created_at
                LIMIT ?
                FOR UPDATE SKIP LOCKED
                """,
                (now_value, limit),
            ).fetchall()
            for row in rows:
                db.execute(
                    """
                    UPDATE routing_jobs
                    SET status = 'PROCESSING', attempts = attempts + 1,
                        lease_owner = ?, lease_until = ?, updated_at = ?
                    WHERE lead_id = ?
                    """,
                    (worker_id, lease_until, now_value, row["lead_id"]),
                )
        return rows

    def claim_due_deliveries(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        at: str | None = None,
        limit: int = 50,
    ) -> list[Any]:
        current = datetime.now(timezone.utc)
        now_value = at or current.strftime("%Y-%m-%dT%H:%M:%SZ")
        lease_until = (current + timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self.transaction() as db:
            rows = db.execute(
                """
                SELECT * FROM deliveries
                WHERE (status IN ('PENDING', 'RETRY') AND next_attempt_at <= ?)
                   OR (status = 'PROCESSING' AND lease_until <= ?)
                ORDER BY next_attempt_at, created_at
                LIMIT ?
                FOR UPDATE SKIP LOCKED
                """,
                (now_value, now_value, limit),
            ).fetchall()
            for row in rows:
                db.execute(
                    """
                    UPDATE deliveries
                    SET status = 'PROCESSING', lease_owner = ?, lease_until = ?, updated_at = ?
                    WHERE delivery_id = ?
                    """,
                    (worker_id, lease_until, now_value, row["delivery_id"]),
                )
        return rows

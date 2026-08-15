"""SQLite persistence for the reference LCP platform."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4

from .api_keys import hash_api_key, verify_api_key
from .crypto import EnvelopeCipher


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """Thread-safe SQLite store with explicit transaction boundaries."""

    def __init__(self, path: str | Path, *, pii_encryption_key: str | bytes | None = None):
        self.path = Path(path)
        self._cipher = EnvelopeCipher(pii_encryption_key)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self.initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def healthcheck(self) -> None:
        with self._lock:
            self._connection.execute("SELECT 1").fetchone()

    def encode_envelope(self, envelope: dict[str, Any]) -> str:
        return self._cipher.encode(envelope)

    def decode_envelope(self, stored: str) -> dict[str, Any]:
        return self._cipher.decode(stored)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except Exception:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def initialize(self) -> None:
        with self.transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS credentials (
                    sender_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    scopes_json TEXT NOT NULL DEFAULT '["*"]',
                    hmac_secret TEXT,
                    previous_hmac_secret TEXT,
                    api_key_hash TEXT,
                    api_key_salt TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leads (
                    lead_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    message_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    receiver_id TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    test INTEGER NOT NULL DEFAULT 0,
                    vertical TEXT,
                    country_code TEXT,
                    envelope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(sender_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS offers (
                    offer_id TEXT PRIMARY KEY,
                    buyer_id TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    offer_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publisher_mappings (
                    mapping_id TEXT NOT NULL,
                    publisher_id TEXT NOT NULL,
                    form_key TEXT NOT NULL,
                    version TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    mapping_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (publisher_id, form_key, version)
                );
                CREATE TABLE IF NOT EXISTS mapping_applications (
                    application_id TEXT PRIMARY KEY,
                    mapping_id TEXT NOT NULL,
                    publisher_id TEXT NOT NULL,
                    form_key TEXT NOT NULL,
                    version TEXT NOT NULL,
                    source_record_id_hash TEXT,
                    lead_id TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    attachment_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_ref TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'AVAILABLE',
                    residency TEXT NOT NULL DEFAULT 'TEST',
                    scan_status TEXT NOT NULL DEFAULT 'not_scanned',
                    scan_engine TEXT NOT NULL DEFAULT 'unknown',
                    scanned_at TEXT NOT NULL DEFAULT '',
                    encryption TEXT NOT NULL DEFAULT 'application_encrypted',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS payable_records (
                    payable_id TEXT PRIMARY KEY,
                    offer_id TEXT NOT NULL,
                    lead_id TEXT NOT NULL,
                    buyer_id TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL,
                    price_cents INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT '',
                    reason TEXT,
                    call_seconds INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(offer_id, lead_id)
                );
                CREATE TABLE IF NOT EXISTS match_decisions (
                    decision_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    buyer_id TEXT NOT NULL,
                    matched INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pings (
                    ping_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    buyer_id TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bids (
                    bid_id TEXT PRIMARY KEY,
                    ping_id TEXT NOT NULL,
                    lead_id TEXT NOT NULL,
                    offer_id TEXT NOT NULL,
                    buyer_id TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    decision TEXT NOT NULL,
                    bid_price_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    estimated_contact_seconds INTEGER,
                    received_at TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    UNIQUE(ping_id, buyer_id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    ping_id TEXT,
                    offer_id TEXT NOT NULL,
                    buyer_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    lease_owner TEXT,
                    lease_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lifecycle_events (
                    event_id TEXT PRIMARY KEY,
                    lead_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    envelope_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS routing_jobs (
                    lead_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rate_limits (
                    sender_id TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY(sender_id, window_start)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    audit_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_resource
                    ON audit_events(resource_type, resource_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_match_decisions_lead
                    ON match_decisions(lead_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_deliveries_due
                    ON deliveries(status, next_attempt_at);
                CREATE INDEX IF NOT EXISTS idx_pings_expiry
                    ON pings(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_events_lead
                    ON lifecycle_events(lead_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_mapping_applications_lead
                    ON mapping_applications(lead_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_attachments_lead
                    ON attachments(lead_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_payable_offer_month
                    ON payable_records(offer_id, month_key, status);
                """
            )
            self._migrate_sqlite_columns(db)

    def _migrate_sqlite_columns(self, db: sqlite3.Connection) -> None:
        columns = {
            row[1] for row in db.execute("PRAGMA table_info(credentials)").fetchall()
        }
        if "tenant_id" not in columns:
            db.execute("ALTER TABLE credentials ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
        if "scopes_json" not in columns:
            db.execute("ALTER TABLE credentials ADD COLUMN scopes_json TEXT NOT NULL DEFAULT '[\"*\"]'")
        if "api_key_salt" not in columns:
            db.execute("ALTER TABLE credentials ADD COLUMN api_key_salt TEXT")
        lead_columns = {
            row[1] for row in db.execute("PRAGMA table_info(leads)").fetchall()
        }
        if "tenant_id" not in lead_columns:
            db.execute("ALTER TABLE leads ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
        delivery_columns = {
            row[1] for row in db.execute("PRAGMA table_info(deliveries)").fetchall()
        }
        if "lease_owner" not in delivery_columns:
            db.execute("ALTER TABLE deliveries ADD COLUMN lease_owner TEXT")
        if "lease_until" not in delivery_columns:
            db.execute("ALTER TABLE deliveries ADD COLUMN lease_until TEXT")
        attachment_columns = {
            row[1] for row in db.execute("PRAGMA table_info(attachments)").fetchall()
        }
        if "residency" not in attachment_columns:
            db.execute("ALTER TABLE attachments ADD COLUMN residency TEXT NOT NULL DEFAULT 'TEST'")
        if "scan_status" not in attachment_columns:
            db.execute("ALTER TABLE attachments ADD COLUMN scan_status TEXT NOT NULL DEFAULT 'not_scanned'")
        if "scan_engine" not in attachment_columns:
            db.execute("ALTER TABLE attachments ADD COLUMN scan_engine TEXT NOT NULL DEFAULT 'unknown'")
        if "scanned_at" not in attachment_columns:
            db.execute("ALTER TABLE attachments ADD COLUMN scanned_at TEXT NOT NULL DEFAULT ''")
        if "encryption" not in attachment_columns:
            db.execute("ALTER TABLE attachments ADD COLUMN encryption TEXT NOT NULL DEFAULT 'application_encrypted'")

    # ─── Credentials ──────────────────────────────────────────────────────

    def upsert_credential(
        self,
        sender_id: str,
        *,
        tenant_id: str = "default",
        scopes: list[str] | None = None,
        hmac_secret: str | None = None,
        previous_hmac_secret: str | None = None,
        api_key: str | None = None,
        active: bool = True,
    ) -> None:
        timestamp = now_iso()
        api_key_hash = api_key_salt = None
        if api_key:
            api_key_salt, api_key_hash = hash_api_key(api_key)
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO credentials
                    (sender_id, tenant_id, scopes_json, hmac_secret, previous_hmac_secret, api_key_hash,
                     api_key_salt, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sender_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    scopes_json=excluded.scopes_json,
                    hmac_secret=excluded.hmac_secret,
                    previous_hmac_secret=excluded.previous_hmac_secret,
                    api_key_hash=excluded.api_key_hash,
                    api_key_salt=excluded.api_key_salt,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (
                    sender_id,
                    tenant_id,
                    json.dumps(scopes if scopes is not None else ["*"]),
                    hmac_secret,
                    previous_hmac_secret,
                    api_key_hash,
                    api_key_salt,
                    int(active),
                    timestamp,
                    timestamp,
                ),
            )

    def get_credential(self, sender_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM credentials WHERE sender_id = ? AND active = 1",
                (sender_id,),
            ).fetchone()

    def find_sender_for_api_key(self, api_key: str) -> str | None:
        # Every credential has its own random salt, so the digest cannot be
        # matched with an indexed equality query; the credentials table is
        # small in the reference platform and each candidate is verified with
        # a constant-time comparison.
        with self._lock:
            rows = self._connection.execute(
                "SELECT sender_id, api_key_hash, api_key_salt FROM credentials WHERE active = 1"
            ).fetchall()
        for row in rows:
            if verify_api_key(api_key, row["api_key_salt"], row["api_key_hash"]):
                return row["sender_id"]
        return None

    # ─── Audit ──────────────────────────────────────────────────────────────

    def insert_audit(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist an audit record without storing consumer payloads or secrets."""
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO audit_events
                    (audit_id, tenant_id, actor_id, action, resource_type,
                     resource_id, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"audit_{uuid4().hex}",
                    tenant_id,
                    actor_id,
                    action,
                    resource_type,
                    resource_id,
                    json.dumps(metadata or {}, separators=(",", ":"), ensure_ascii=False),
                    now_iso(),
                ),
            )

    def list_audit_events(self, resource_type: str, resource_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM audit_events
                WHERE resource_type = ? AND resource_id = ?
                ORDER BY created_at, audit_id
                """,
                (resource_type, resource_id),
            ).fetchall()
        return [
            {
                "tenant_id": row["tenant_id"],
                "actor_id": row["actor_id"],
                "action": row["action"],
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # ─── Publisher mappings ────────────────────────────────────────────────

    def upsert_mapping(self, mapping: dict[str, Any]) -> None:
        timestamp = now_iso()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO publisher_mappings
                    (mapping_id, publisher_id, form_key, version, active, mapping_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(publisher_id, form_key, version) DO UPDATE SET
                    mapping_id=excluded.mapping_id,
                    active=excluded.active,
                    mapping_json=excluded.mapping_json,
                    updated_at=excluded.updated_at
                """,
                (
                    mapping["mapping_id"], mapping["publisher_id"], mapping["form_key"],
                    mapping["version"], int(mapping.get("active", True)),
                    json.dumps(mapping, separators=(",", ":"), ensure_ascii=False), timestamp, timestamp,
                ),
            )

    def list_mappings(self, publisher_id: str | None = None, *, active_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT mapping_json FROM publisher_mappings"
        clauses: list[str] = []
        values: list[Any] = []
        if publisher_id:
            clauses.append("publisher_id = ?")
            values.append(publisher_id)
        if active_only:
            clauses.append("active = 1")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY publisher_id, form_key, version DESC"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [json.loads(row["mapping_json"]) for row in rows]

    def insert_mapping_application(
        self,
        *,
        mapping: dict[str, Any],
        source_record_id: str | None,
        lead_id: str,
        source_digest: str,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO mapping_applications
                    (application_id, mapping_id, publisher_id, form_key, version,
                     source_record_id_hash, lead_id, source_digest, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"mapapp_{uuid4().hex}", mapping["mapping_id"], mapping["publisher_id"],
                    mapping["form_key"], mapping["version"],
                    hashlib.sha256(source_record_id.encode("utf-8")).hexdigest() if source_record_id else None,
                    lead_id, source_digest, now_iso(),
                ),
            )

    def list_mapping_applications(self, lead_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM mapping_applications WHERE lead_id = ? ORDER BY created_at",
                (lead_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ─── Rate limiting ─────────────────────────────────────────────────────

    def consume_rate_limit(self, sender_id: str, *, limit: int, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        window_start = current.strftime("%Y-%m-%dT%H:%M:00Z")
        with self.transaction() as db:
            row = db.execute(
                "SELECT request_count FROM rate_limits WHERE sender_id = ? AND window_start = ?",
                (sender_id, window_start),
            ).fetchone()
            count = int(row["request_count"]) if row else 0
            if count >= limit:
                return False
            if row:
                db.execute(
                    "UPDATE rate_limits SET request_count = request_count + 1 WHERE sender_id = ? AND window_start = ?",
                    (sender_id, window_start),
                )
            else:
                db.execute(
                    "INSERT INTO rate_limits(sender_id, window_start, request_count) VALUES (?, ?, 1)",
                    (sender_id, window_start),
                )
        return True

    # ─── Attachments ──────────────────────────────────────────────────────

    def get_attachment(self, attachment_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM attachments WHERE attachment_id = ?", (attachment_id,)
            ).fetchone()

    def get_attachment_by_idempotency(self, owner_id: str, idempotency_key: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM attachments WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, idempotency_key),
            ).fetchone()

    def insert_attachment(self, metadata: dict[str, Any]) -> bool:
        timestamp = now_iso()
        with self.transaction() as db:
            try:
                db.execute(
                    """
                    INSERT INTO attachments
                        (attachment_id, lead_id, owner_id, idempotency_key, purpose,
                         filename, content_type, size_bytes, sha256, storage_ref,
                         status, residency, scan_status, scan_engine, scanned_at, encryption,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata["attachment_id"], metadata["lead_id"], metadata["owner_id"],
                        metadata["idempotency_key"], metadata["purpose"], metadata["filename"],
                        metadata["content_type"], metadata["size_bytes"], metadata["sha256"],
                        metadata["storage_ref"], metadata.get("residency", "TEST"),
                        metadata.get("scan_status", "not_scanned"), metadata.get("scan_engine", "unknown"),
                        metadata.get("scanned_at", timestamp), metadata.get("encryption", "application_encrypted"),
                        timestamp, timestamp,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def list_attachments(self, lead_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM attachments WHERE lead_id = ? ORDER BY created_at, attachment_id",
                (lead_id,),
            ).fetchall()

    def mark_attachment_redacted(self, attachment_id: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE attachments SET status = 'REDACTED', updated_at = ? WHERE attachment_id = ?",
                (now_iso(), attachment_id),
            )

    # ─── Leads ─────────────────────────────────────────────────────────────

    def get_lead(self, lead_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM leads WHERE lead_id = ?", (lead_id,)
            ).fetchone()

    def get_lead_by_idempotency(self, sender_id: str, idempotency_key: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM leads WHERE sender_id = ? AND idempotency_key = ?",
                (sender_id, idempotency_key),
            ).fetchone()

    def insert_lead(self, envelope: dict[str, Any], *, status: str, tenant_id: str = "default") -> bool:
        message = envelope["lcp"]["message"]
        payload = envelope["lcp"]["payload"]
        lead_id = payload["lead_id"]
        timestamp = now_iso()
        with self.transaction() as db:
            try:
                db.execute(
                    """
                    INSERT INTO leads
                        (lead_id, tenant_id, message_id, idempotency_key, sender_id, receiver_id,
                         message_type, status, test, vertical, country_code,
                         envelope_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lead_id,
                        tenant_id,
                        message["id"],
                        message["idempotency_key"],
                        message["sender_id"],
                        message["receiver_id"],
                        message["type"],
                        status,
                        int(message.get("test", False)),
                        payload.get("attributes", {}).get("vertical"),
                        payload.get("location", {}).get("country_code"),
                        self.encode_envelope(envelope),
                        timestamp,
                        timestamp,
                    ),
                )
                db.execute(
                    """
                    INSERT INTO routing_jobs(lead_id, status, attempts, created_at, updated_at)
                    VALUES (?, 'PENDING', 0, ?, ?)
                    ON CONFLICT(lead_id) DO NOTHING
                    """,
                    (lead_id, timestamp, timestamp),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def update_lead_status(self, lead_id: str, status: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE leads SET status = ?, updated_at = ? WHERE lead_id = ?",
                (status, now_iso(), lead_id),
            )

    def routing_job_pending(self, lead_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM routing_jobs WHERE lead_id = ?", (lead_id,)
            ).fetchone()
        return bool(row and row["status"] != "DONE")

    def complete_routing_job(self, lead_id: str, worker_id: str | None = None) -> None:
        with self.transaction() as db:
            if worker_id:
                db.execute(
                    """
                    UPDATE routing_jobs
                    SET status = 'DONE', updated_at = ?, lease_owner = NULL, lease_until = NULL
                    WHERE lead_id = ? AND lease_owner = ?
                    """,
                    (now_iso(), lead_id, worker_id),
                )
            else:
                db.execute(
                    "UPDATE routing_jobs SET status = 'DONE', updated_at = ?, lease_owner = NULL, lease_until = NULL WHERE lead_id = ?",
                    (now_iso(), lead_id),
                )

    def claim_routing_jobs(self, worker_id: str, *, limit: int = 20, lease_seconds: int = 60) -> list[sqlite3.Row]:
        current = datetime.now(timezone.utc)
        now_value = current.strftime("%Y-%m-%dT%H:%M:%SZ")
        lease_until = (current + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.transaction() as db:
            rows = db.execute(
                """
                SELECT * FROM routing_jobs
                WHERE (status = 'PENDING') OR (status = 'PROCESSING' AND lease_until <= ?)
                ORDER BY created_at
                LIMIT ?
                """,
                (now_value, limit),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE routing_jobs SET status = 'PROCESSING', attempts = attempts + 1, lease_owner = ?, lease_until = ?, updated_at = ? WHERE lead_id = ?",
                    (worker_id, lease_until, now_value, row["lead_id"]),
                )
        return rows

    def release_routing_job(
        self, lead_id: str, error: str, worker_id: str | None = None
    ) -> None:
        with self.transaction() as db:
            if worker_id:
                db.execute(
                    """
                    UPDATE routing_jobs
                    SET status = 'PENDING', last_error = ?, lease_owner = NULL,
                        lease_until = NULL, updated_at = ?
                    WHERE lead_id = ? AND lease_owner = ?
                    """,
                    (error[:500], now_iso(), lead_id, worker_id),
                )
            else:
                db.execute(
                    "UPDATE routing_jobs SET status = 'PENDING', last_error = ?, lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE lead_id = ?",
                    (error[:500], now_iso(), lead_id),
                )

    # ─── Offers ────────────────────────────────────────────────────────────

    def upsert_offer(self, offer: dict[str, Any]) -> None:
        timestamp = now_iso()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO offers (offer_id, buyer_id, active, offer_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(offer_id) DO UPDATE SET
                    buyer_id=excluded.buyer_id,
                    active=excluded.active,
                    offer_json=excluded.offer_json,
                    updated_at=excluded.updated_at
                """,
                (
                    offer["offer_id"],
                    offer["buyer_id"],
                    int(offer.get("active", True)),
                    json.dumps(offer, separators=(",", ":"), ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )

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
            clauses.append("json_extract(offer_json, '$.vertical') = ?")
            values.append(vertical)
        if tenant_id:
            clauses.append("json_extract(offer_json, '$.tenant_id') = ?")
            values.append(tenant_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY offer_id"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [json.loads(row["offer_json"]) for row in rows]

    def get_offer(self, offer_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT offer_json FROM offers WHERE offer_id = ?", (offer_id,)
            ).fetchone()
        return json.loads(row["offer_json"]) if row else None

    # ─── Match audit ───────────────────────────────────────────────────────

    def insert_match_decision(
        self,
        *,
        lead_id: str,
        offer_id: str,
        buyer_id: str,
        matched: bool,
        reasons: list[str] | tuple[str, ...],
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO match_decisions
                    (decision_id, lead_id, offer_id, buyer_id, matched, reasons_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    matched=excluded.matched,
                    reasons_json=excluded.reasons_json,
                    created_at=excluded.created_at
                """,
                (
                    f"{lead_id}:{offer_id}",
                    lead_id,
                    offer_id,
                    buyer_id,
                    int(matched),
                    json.dumps(list(reasons), separators=(",", ":")),
                    now_iso(),
                ),
            )

    def list_match_decisions(self, lead_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM match_decisions WHERE lead_id = ? ORDER BY created_at, offer_id",
                (lead_id,),
            ).fetchall()
        return [
            {
                "offer_id": row["offer_id"],
                "buyer_id": row["buyer_id"],
                "matched": bool(row["matched"]),
                "reasons": json.loads(row["reasons_json"]),
            }
            for row in rows
        ]

    # ─── Pings and bids ────────────────────────────────────────────────────

    def has_ping_for_offer(self, lead_id: str, offer_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM pings WHERE lead_id = ? AND offer_id = ? LIMIT 1",
                (lead_id, offer_id),
            ).fetchone()
        return row is not None

    def insert_ping(self, ping: dict[str, Any], *, lead_id: str, offer_id: str, buyer_id: str, expires_at: str) -> bool:
        message = ping["lcp"]["message"]
        ping_id = ping["lcp"]["payload"]["ping_id"]
        timestamp = now_iso()
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO pings
                    (ping_id, lead_id, offer_id, buyer_id, message_id, expires_at,
                     status, envelope_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
                ON CONFLICT(message_id) DO NOTHING
                """,
                (
                    ping_id,
                    lead_id,
                    offer_id,
                    buyer_id,
                    message["id"],
                    expires_at,
                    self.encode_envelope(ping),
                    timestamp,
                    timestamp,
                ),
            )
            return cursor.rowcount == 1

    def get_ping(self, ping_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM pings WHERE ping_id = ?", (ping_id,)
            ).fetchone()

    def list_open_pings(self, lead_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM pings WHERE lead_id = ? AND status = 'OPEN' ORDER BY ping_id",
                (lead_id,),
            ).fetchall()

    def list_pings(self, lead_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM pings WHERE lead_id = ? ORDER BY created_at, ping_id",
                (lead_id,),
            ).fetchall()

    def list_expired_open_pings(self, at: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM pings
                WHERE status = 'OPEN' AND expires_at <= ?
                ORDER BY expires_at, ping_id
                """,
                (at or now_iso(),),
            ).fetchall()

    def count_offer_deliveries(self, offer_id: str, *, since: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM deliveries WHERE offer_id = ? AND kind = 'post' AND status NOT IN ('FAILED', 'REDACTED')"
        values: list[Any] = [offer_id]
        if since:
            query += " AND created_at >= ?"
            values.append(since)
        with self._lock:
            row = self._connection.execute(query, values).fetchone()
        return int(row["count"])

    def expire_ping(self, ping_id: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE pings SET status = 'EXPIRED', updated_at = ? WHERE ping_id = ? AND status = 'OPEN'",
                (now_iso(), ping_id),
            )

    def mark_ping_won(self, ping_id: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE pings SET status = 'WON', updated_at = ? WHERE ping_id = ?",
                (now_iso(), ping_id),
            )

    def insert_bid(self, envelope: dict[str, Any], ping: sqlite3.Row) -> bool:
        message = envelope["lcp"]["message"]
        payload = envelope["lcp"]["payload"]
        timestamp = now_iso()
        with self.transaction() as db:
            try:
                db.execute(
                    """
                    INSERT INTO bids
                        (bid_id, ping_id, lead_id, offer_id, buyer_id, message_id,
                         decision, bid_price_cents, currency, estimated_contact_seconds,
                         received_at, envelope_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message["id"],
                        payload["ping_id"],
                        ping["lead_id"],
                        ping["offer_id"],
                        message["sender_id"],
                        message["id"],
                        payload["decision"],
                        payload.get("bid_price_cents", 0),
                        payload.get("currency", ""),
                        payload.get("estimated_contact_seconds"),
                        timestamp,
                        self.encode_envelope(envelope),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def list_bids_for_lead(self, lead_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM bids WHERE lead_id = ? ORDER BY received_at, buyer_id",
                (lead_id,),
            ).fetchall()

    # ─── Payable outcomes and quota ───────────────────────────────────────

    def record_payable(
        self,
        *,
        offer_id: str,
        lead_id: str,
        buyer_id: str,
        month_key: str,
        channel: str,
        status: str,
        price_cents: int = 0,
        currency: str = "",
        reason: str | None = None,
        call_seconds: int | None = None,
    ) -> None:
        timestamp = now_iso()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO payable_records
                    (payable_id, offer_id, lead_id, buyer_id, month_key, channel,
                     status, price_cents, currency, reason, call_seconds, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(offer_id, lead_id) DO UPDATE SET
                    month_key=excluded.month_key,
                    status=excluded.status,
                    price_cents=excluded.price_cents,
                    currency=excluded.currency,
                    reason=excluded.reason,
                    call_seconds=excluded.call_seconds,
                    updated_at=excluded.updated_at
                """,
                (
                    f"pay_{offer_id}_{lead_id}", offer_id, lead_id, buyer_id, month_key, channel,
                    status, price_cents, currency, reason, call_seconds, timestamp, timestamp,
                ),
            )

    def payable_for_lead(self, lead_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM payable_records WHERE lead_id = ? ORDER BY offer_id",
                (lead_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def payable_summary(self, offer_id: str, month_key: str) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM payable_records WHERE offer_id = ? AND month_key = ? GROUP BY status",
                (offer_id, month_key),
            ).fetchall()
        summary = {"pending": 0, "payable": 0, "not_payable": 0, "disputed": 0, "refunded": 0}
        for row in rows:
            summary[str(row["status"])] = int(row["count"])
        summary["total"] = sum(summary.values())
        return summary

    def deliveries_for_lead(self, lead_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM deliveries WHERE lead_id = ? ORDER BY created_at, delivery_id",
                (lead_id,),
            ).fetchall()

    # ─── Delivery queue ────────────────────────────────────────────────────

    def insert_delivery(
        self,
        *,
        lead_id: str,
        ping_id: str | None,
        offer_id: str,
        buyer_id: str,
        kind: str,
        envelope: dict[str, Any],
        webhook_url: str,
    ) -> bool:
        message = envelope["lcp"]["message"]
        timestamp = now_iso()
        with self.transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO deliveries
                    (delivery_id, lead_id, ping_id, offer_id, buyer_id, kind,
                     message_id, idempotency_key, webhook_url, envelope_json,
                     status, attempts, next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?)
                ON CONFLICT(message_id) DO NOTHING
                """,
                (
                    message["id"],
                    lead_id,
                    ping_id,
                    offer_id,
                    buyer_id,
                    kind,
                    message["id"],
                    message["idempotency_key"],
                    webhook_url,
                    self.encode_envelope(envelope),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            return cursor.rowcount == 1

    def list_due_deliveries(self, at: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM deliveries
                WHERE (status IN ('PENDING', 'RETRY') AND next_attempt_at <= ?)
                   OR (status = 'PROCESSING' AND lease_until <= ?)
                ORDER BY next_attempt_at, created_at
                """,
                (at or now_iso(), at or now_iso()),
            ).fetchall()

    def claim_due_deliveries(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        at: str | None = None,
        limit: int = 50,
    ) -> list[sqlite3.Row]:
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

    def mark_delivery(
        self,
        delivery_id: str,
        *,
        status: str,
        attempts: int,
        next_attempt_at: str,
        last_error: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        with self.transaction() as db:
            if worker_id:
                db.execute(
                    """
                    UPDATE deliveries
                    SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?,
                        lease_owner = NULL, lease_until = NULL, updated_at = ?
                    WHERE delivery_id = ? AND lease_owner = ?
                    """,
                    (status, attempts, next_attempt_at, last_error, now_iso(), delivery_id, worker_id),
                )
            else:
                db.execute(
                    """
                    UPDATE deliveries
                    SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?
                    WHERE delivery_id = ?
                    """,
                    (status, attempts, next_attempt_at, last_error, now_iso(), delivery_id),
                )

    def has_delivery_for_offer(self, lead_id: str, offer_id: str, kind: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM deliveries WHERE lead_id = ? AND offer_id = ? AND kind = ? LIMIT 1",
                (lead_id, offer_id, kind),
            ).fetchone()
        return row is not None

    def has_delivery_for_buyer(self, lead_id: str, buyer_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM deliveries WHERE lead_id = ? AND buyer_id = ? LIMIT 1",
                (lead_id, buyer_id),
            ).fetchone()
        return row is not None

    # ─── Lifecycle ─────────────────────────────────────────────────────────

    def insert_event(self, lead_id: str, event_name: str, envelope: dict[str, Any]) -> None:
        payload = envelope["lcp"]["payload"]
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO lifecycle_events
                    (event_id, lead_id, event_name, timestamp, envelope_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (
                    envelope["lcp"]["message"]["id"],
                    lead_id,
                    event_name,
                    payload["timestamp"],
                    self.encode_envelope(envelope),
                ),
            )

    def list_events(self, lead_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT envelope_json FROM lifecycle_events WHERE lead_id = ? ORDER BY timestamp",
                (lead_id,),
            ).fetchall()
        return [self.decode_envelope(row["envelope_json"]) for row in rows]

    def erase_lead(self, lead_id: str, *, actor_id: str = "operator") -> bool:
        """Redact consumer data and cancel undelivered payloads for a lead.

        Immutable routing/audit identifiers remain so operators can prove that
        an erasure occurred without retaining the consumer payload.
        """
        with self.transaction() as db:
            row = db.execute(
                "SELECT tenant_id, envelope_json FROM leads WHERE lead_id = ?",
                (lead_id,),
            ).fetchone()
            if not row:
                return False
            redacted_lead = self._redact_envelope(row["envelope_json"])
            timestamp = now_iso()
            db.execute(
                """
                UPDATE leads
                SET envelope_json = ?, status = 'ERASED', updated_at = ?
                WHERE lead_id = ?
                """,
                (redacted_lead, timestamp, lead_id),
            )
            child_keys = {
                "pings": "ping_id",
                "bids": "bid_id",
                "deliveries": "delivery_id",
                "lifecycle_events": "event_id",
            }
            for table, child_key in child_keys.items():
                rows = db.execute(
                    f"SELECT {child_key}, envelope_json FROM {table} WHERE lead_id = ?",
                    (lead_id,),
                ).fetchall()
                for child in rows:
                    redacted = self._redact_envelope(child["envelope_json"])
                    if table == "deliveries":
                        db.execute(
                            """
                            UPDATE deliveries
                            SET envelope_json = ?, status = 'REDACTED',
                                lease_owner = NULL, lease_until = NULL,
                                last_error = 'Lead erased', updated_at = ?
                            WHERE delivery_id = ?
                            """,
                            (redacted, timestamp, child[child_key]),
                        )
                    elif table == "pings":
                        db.execute(
                            """
                            UPDATE pings
                            SET envelope_json = ?, status = 'REDACTED', updated_at = ?
                            WHERE ping_id = ?
                            """,
                            (redacted, timestamp, child[child_key]),
                        )
                    else:
                        db.execute(
                            f"UPDATE {table} SET envelope_json = ? WHERE {child_key} = ?",
                            (redacted, child[child_key]),
                        )
            db.execute(
                "UPDATE routing_jobs SET status = 'DONE', lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE lead_id = ?",
                (timestamp, lead_id),
            )
            db.execute(
                """
                INSERT INTO audit_events
                    (audit_id, tenant_id, actor_id, action, resource_type,
                     resource_id, metadata_json, created_at)
                VALUES (?, ?, ?, 'lead.erased', 'lead', ?, ?, ?)
                """,
                (
                    f"audit_{uuid4().hex}",
                    row["tenant_id"],
                    actor_id,
                    lead_id,
                    json.dumps({"retained_identifiers": True}, separators=(",", ":")),
                    timestamp,
                ),
            )
        return True

    def _redact_envelope(self, stored: str) -> str:
        envelope = self.decode_envelope(stored)
        message = dict(envelope["lcp"]["message"])
        message.pop("security", None)
        message_type = message.get("type")
        original_payload = envelope["lcp"].get("payload", {})
        if message_type in {"lead", "call", "post", "ping"}:
            payload = {
                key: original_payload[key]
                for key in ("lead_id", "ping_id", "event", "timestamp")
                if key in original_payload
            }
        elif message_type == "event":
            payload = {
                key: original_payload[key]
                for key in ("lead_id", "event", "timestamp")
                if key in original_payload
            }
        else:
            payload = {}
        return self.encode_envelope(
            {"lcp": {"version": envelope["lcp"]["version"], "message": message, "payload": payload}}
        )

    def all_pings_terminal(self, lead_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM pings WHERE lead_id = ? AND status = 'OPEN'",
                (lead_id,),
            ).fetchone()
        return row["count"] == 0

"""SQLite persistence for the reference LCP platform."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """Thread-safe SQLite store with explicit transaction boundaries."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
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
                    hmac_secret TEXT,
                    previous_hmac_secret TEXT,
                    api_key_hash TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS leads (
                    lead_id TEXT PRIMARY KEY,
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
                CREATE TABLE IF NOT EXISTS rate_limits (
                    sender_id TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY(sender_id, window_start)
                );
                CREATE INDEX IF NOT EXISTS idx_match_decisions_lead
                    ON match_decisions(lead_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_deliveries_due
                    ON deliveries(status, next_attempt_at);
                CREATE INDEX IF NOT EXISTS idx_pings_expiry
                    ON pings(status, expires_at);
                CREATE INDEX IF NOT EXISTS idx_events_lead
                    ON lifecycle_events(lead_id, timestamp);
                """
            )

    # ─── Credentials ──────────────────────────────────────────────────────

    def upsert_credential(
        self,
        sender_id: str,
        *,
        hmac_secret: str | None = None,
        previous_hmac_secret: str | None = None,
        api_key: str | None = None,
        active: bool = True,
    ) -> None:
        timestamp = now_iso()
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest() if api_key else None
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO credentials
                    (sender_id, hmac_secret, previous_hmac_secret, api_key_hash,
                     active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sender_id) DO UPDATE SET
                    hmac_secret=excluded.hmac_secret,
                    previous_hmac_secret=excluded.previous_hmac_secret,
                    api_key_hash=excluded.api_key_hash,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (
                    sender_id,
                    hmac_secret,
                    previous_hmac_secret,
                    api_key_hash,
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
        digest = hashlib.sha256(api_key.encode()).hexdigest()
        with self._lock:
            row = self._connection.execute(
                "SELECT sender_id FROM credentials WHERE api_key_hash = ? AND active = 1",
                (digest,),
            ).fetchone()
        return row["sender_id"] if row else None

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

    def insert_lead(self, envelope: dict[str, Any], *, status: str) -> bool:
        message = envelope["lcp"]["message"]
        payload = envelope["lcp"]["payload"]
        lead_id = payload["lead_id"]
        timestamp = now_iso()
        with self.transaction() as db:
            try:
                db.execute(
                    """
                    INSERT INTO leads
                        (lead_id, message_id, idempotency_key, sender_id, receiver_id,
                         message_type, status, test, vertical, country_code,
                         envelope_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lead_id,
                        message["id"],
                        message["idempotency_key"],
                        message["sender_id"],
                        message["receiver_id"],
                        message["type"],
                        status,
                        int(message.get("test", False)),
                        payload.get("attributes", {}).get("vertical"),
                        payload.get("location", {}).get("country_code"),
                        json.dumps(envelope, separators=(",", ":"), ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
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

    def list_offers(self, vertical: str | None = None, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT offer_json FROM offers"
        clauses: list[str] = []
        values: list[Any] = []
        if active_only:
            clauses.append("active = 1")
        if vertical:
            clauses.append("json_extract(offer_json, '$.vertical') = ?")
            values.append(vertical)
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

    def insert_ping(self, ping: dict[str, Any], *, lead_id: str, offer_id: str, buyer_id: str, expires_at: str) -> None:
        message = ping["lcp"]["message"]
        ping_id = ping["lcp"]["payload"]["ping_id"]
        timestamp = now_iso()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO pings
                    (ping_id, lead_id, offer_id, buyer_id, message_id, expires_at,
                     status, envelope_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
                """,
                (
                    ping_id,
                    lead_id,
                    offer_id,
                    buyer_id,
                    message["id"],
                    expires_at,
                    json.dumps(ping, separators=(",", ":"), ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )

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
        query = "SELECT COUNT(*) AS count FROM deliveries WHERE offer_id = ? AND kind = 'post' AND status != 'FAILED'"
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
                        json.dumps(envelope, separators=(",", ":"), ensure_ascii=False),
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
    ) -> None:
        message = envelope["lcp"]["message"]
        timestamp = now_iso()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO deliveries
                    (delivery_id, lead_id, ping_id, offer_id, buyer_id, kind,
                     message_id, idempotency_key, webhook_url, envelope_json,
                     status, attempts, next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?)
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
                    json.dumps(envelope, separators=(",", ":"), ensure_ascii=False),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    def list_due_deliveries(self, at: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM deliveries
                WHERE status IN ('PENDING', 'RETRY')
                  AND next_attempt_at <= ?
                ORDER BY next_attempt_at, created_at
                """,
                (at or now_iso(),),
            ).fetchall()

    def mark_delivery(
        self,
        delivery_id: str,
        *,
        status: str,
        attempts: int,
        next_attempt_at: str,
        last_error: str | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """
                UPDATE deliveries
                SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                WHERE delivery_id = ?
                """,
                (status, attempts, next_attempt_at, last_error, now_iso(), delivery_id),
            )

    # ─── Lifecycle ─────────────────────────────────────────────────────────

    def insert_event(self, lead_id: str, event_name: str, envelope: dict[str, Any]) -> None:
        payload = envelope["lcp"]["payload"]
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO lifecycle_events
                    (event_id, lead_id, event_name, timestamp, envelope_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    envelope["lcp"]["message"]["id"],
                    lead_id,
                    event_name,
                    payload["timestamp"],
                    json.dumps(envelope, separators=(",", ":"), ensure_ascii=False),
                ),
            )

    def list_events(self, lead_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT envelope_json FROM lifecycle_events WHERE lead_id = ? ORDER BY timestamp",
                (lead_id,),
            ).fetchall()
        return [json.loads(row["envelope_json"]) for row in rows]

    def all_pings_terminal(self, lead_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM pings WHERE lead_id = ? AND status = 'OPEN'",
                (lead_id,),
            ).fetchone()
        return row["count"] == 0

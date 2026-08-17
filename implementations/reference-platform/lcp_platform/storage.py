"""SQLite persistence for the reference LCP platform."""

from __future__ import annotations

from collections.abc import Mapping
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
from .observability import current_request_id


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an RFC 3339 value into an aware UTC datetime.

    Schemas reject malformed values before they reach the router. The runtime
    still fails closed for legacy rows or extension data that cannot be parsed.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_iso_datetime(value: datetime) -> str:
    """Format an aware datetime using the canonical second-precision UTC form."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope_expiry(envelope: dict[str, Any]) -> datetime | None:
    """Return the effective lead/call expiry from absolute or relative syntax."""
    payload = envelope.get("lcp", {}).get("payload", {})
    expiry = payload.get("expiry")
    if not isinstance(expiry, Mapping):
        expiry = {}
    absolute = expiry.get("expires_at", payload.get("expires_at"))
    parsed_absolute = parse_iso_datetime(absolute)
    if parsed_absolute is not None:
        return parsed_absolute
    ttl = expiry.get("ttl_seconds", payload.get("ttl_seconds"))
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
        return None
    message = envelope.get("lcp", {}).get("message", {})
    base = (
        parse_iso_datetime(payload.get("submitted_at"))
        or parse_iso_datetime(payload.get("provenance", {}).get("received_at"))
        or parse_iso_datetime(message.get("timestamp"))
    )
    return base + timedelta(seconds=int(ttl)) if base else None


def envelope_consent_expiry(envelope: dict[str, Any]) -> datetime | None:
    """Return the consumer consent validity boundary, if one is supplied."""
    payload = envelope.get("lcp", {}).get("payload", {})
    compliance = payload.get("compliance", {})
    if not isinstance(compliance, Mapping):
        return None
    return parse_iso_datetime(compliance.get("consent_expires_at"))


def is_expired(value: datetime | None, *, at: datetime | None = None) -> bool:
    return value is not None and value <= (at or datetime.now(timezone.utc))


_CANDIDATE_REQUIREMENTS_NAMESPACE = "lcp.platform.requirements"
_CANDIDATE_SERVICE_AREA_NAMESPACE = "lcp.platform.service_area"
_CANDIDATE_ALLOWED_PATH_ROOTS = {"attributes", "location", "provenance"}
_CANDIDATE_ALLOWED_OPERATORS = {"equals", "in", "exists", "between", "prefix"}

# Keep the runtime graph aligned with test-vectors/conformance.py and SPEC.md.
# Direct offers use the implementation-only NEW -> POSTED path explicitly
# marked by update_lead_status(reason="direct_delivery").
LEGAL_LEAD_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"PINGED", "REJECTED", "EXPIRED", "DUPLICATE"},
    "PINGED": {"POSTED", "EXPIRED", "DUPLICATE"},
    "POSTED": {"ACCEPTED", "REJECTED", "EXPIRED", "DUPLICATE"},
    "ACCEPTED": {"CONVERTED", "DISPUTED", "ARCHIVED"},
    "DISPUTED": {"REFUNDED", "ACCEPTED"},
    "CONVERTED": set(),
    "REFUNDED": set(),
    "REJECTED": set(),
    "ARCHIVED": set(),
    "EXPIRED": set(),
    "DUPLICATE": set(),
}


class InvalidStatusTransition(ValueError):
    """Raised when a lead status leaves the published lifecycle graph."""


class EventIdempotencyConflict(ValueError):
    """Raised when an event message ID is reused with different content."""


def _candidate_string_values(value: Any) -> set[str] | None:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        return None
    return set(value)


def _candidate_optional_values(
    container: Mapping[str, Any], key: str
) -> tuple[set[str] | None, bool]:
    """Return values, plus whether a present value is too uncertain to index."""
    if key not in container or container[key] is None:
        return None, False
    if container[key] == []:
        return None, True
    values = _candidate_string_values(container[key])
    return values, values is None


def _candidate_profile_values(
    profile: Any,
) -> tuple[dict[str, set[str]], bool]:
    """Read valid service-area dimensions without making them sufficient."""
    if not isinstance(profile, Mapping):
        return {}, True
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
        return {}, True
    if not isinstance(profile.get("version"), str) or not profile["version"]:
        return {}, True
    values: dict[str, set[str]] = {}
    uncertain = False
    for profile_field, index_dimension in (
        ("countries", "country_code"),
        ("state_regions", "state_region"),
        ("postal_codes", "postal_code"),
    ):
        if profile_field not in profile or profile[profile_field] is None:
            continue
        candidate, invalid = _candidate_optional_values(profile, profile_field)
        uncertain = uncertain or invalid
        if candidate is not None:
            values[index_dimension] = candidate
    return values, uncertain


def _candidate_requirement_values(
    profile: Any,
) -> tuple[set[str] | None, bool]:
    """Extract only service-type equality constraints from a valid profile."""
    if not isinstance(profile, Mapping):
        return None, True
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
        return None, True
    if not isinstance(profile.get("version"), str) or not profile["version"]:
        return None, True
    predicates = profile.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        return None, True

    service_values: set[str] | None = None
    uncertain = False
    for predicate in predicates:
        if not isinstance(predicate, Mapping):
            uncertain = True
            continue
        path = predicate.get("path")
        operator = predicate.get("operator")
        if not isinstance(path, str) or not isinstance(operator, str):
            uncertain = True
            continue
        if path != "channel":
            parts = path.split(".")
            if len(parts) != 2 or parts[0] not in _CANDIDATE_ALLOWED_PATH_ROOTS or not parts[1]:
                uncertain = True
                continue
        if operator not in _CANDIDATE_ALLOWED_OPERATORS:
            uncertain = True
            continue

        if operator == "exists":
            if not isinstance(predicate.get("value"), bool):
                uncertain = True
        elif operator == "equals":
            if "value" not in predicate:
                uncertain = True
            elif path == "attributes.service_type":
                value = predicate["value"]
                if not isinstance(value, str) or not value:
                    uncertain = True
                else:
                    service_values = {value} if service_values is None else service_values & {value}
        elif operator == "in":
            values = _candidate_string_values(predicate.get("values"))
            if values is None:
                uncertain = True
            elif path == "attributes.service_type":
                service_values = values if service_values is None else service_values & values
        elif operator == "between":
            minimum = predicate.get("min")
            maximum = predicate.get("max")
            if (
                isinstance(minimum, bool)
                or not isinstance(minimum, (int, float))
                or isinstance(maximum, bool)
                or not isinstance(maximum, (int, float))
                or minimum > maximum
            ):
                uncertain = True
        elif operator == "prefix" and not isinstance(predicate.get("value"), str):
            uncertain = True
    return service_values, uncertain


def _candidate_intersection(
    first: set[str] | None, second: set[str] | None
) -> set[str] | None:
    if first is None:
        return second
    if second is None:
        return first
    return first & second


def _offer_candidate_dimensions(offer: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Build necessary-condition index rows, falling back when uncertain.

    Candidate indexing is deliberately a prefilter, never an alternate
    matcher. Any malformed or unrecognized configuration gets a fallback row
    so the existing full matcher can preserve its fail-closed semantics.
    """
    uncertain = False
    vertical = offer.get("vertical")
    if not isinstance(vertical, str) or not vertical:
        uncertain = True

    countries, invalid_countries = _candidate_optional_values(offer, "countries")
    uncertain = uncertain or invalid_countries or "countries" not in offer

    state_regions, invalid_regions = _candidate_optional_values(offer, "state_regions")
    postal_codes, invalid_postal_codes = _candidate_optional_values(offer, "postal_codes")
    uncertain = uncertain or invalid_regions or invalid_postal_codes

    attribute_equals = offer.get("attribute_equals", {})
    attribute_in = offer.get("attribute_in", {})
    if not isinstance(attribute_equals, Mapping) or not isinstance(attribute_in, Mapping):
        uncertain = True
        attribute_equals = {}
        attribute_in = {}
    service_type: set[str] | None = None
    if "service_type" in attribute_equals:
        value = attribute_equals["service_type"]
        if not isinstance(value, str) or not value:
            uncertain = True
        else:
            service_type = {value}
    if "service_type" in attribute_in:
        values = _candidate_string_values(attribute_in["service_type"])
        if values is None:
            uncertain = True
        else:
            service_type = values if service_type is None else service_type & values

    extensions = offer.get("extensions", {})
    if not isinstance(extensions, Mapping):
        return [("fallback", "1")]
    area_values: dict[str, set[str]] = {}
    if _CANDIDATE_SERVICE_AREA_NAMESPACE in extensions:
        area_values, area_uncertain = _candidate_profile_values(
            extensions[_CANDIDATE_SERVICE_AREA_NAMESPACE]
        )
        uncertain = uncertain or area_uncertain
    requirement_values: set[str] | None = None
    if _CANDIDATE_REQUIREMENTS_NAMESPACE in extensions:
        requirement_values, requirement_uncertain = _candidate_requirement_values(
            extensions[_CANDIDATE_REQUIREMENTS_NAMESPACE]
        )
        uncertain = uncertain or requirement_uncertain
    service_type = _candidate_intersection(service_type, requirement_values)

    countries = _candidate_intersection(countries, area_values.get("country_code"))
    state_regions = _candidate_intersection(state_regions, area_values.get("state_region"))
    postal_codes = _candidate_intersection(postal_codes, area_values.get("postal_code"))

    if uncertain:
        return [("fallback", "1")]
    if countries is None:
        return [("fallback", "1")]
    if not countries or not isinstance(vertical, str) or not vertical:
        return [("impossible", "1")]

    dimensions: list[tuple[str, str]] = [("vertical", vertical)]
    dimensions.extend(("country_code", value) for value in sorted(countries))
    for dimension, values in (
        ("state_region", state_regions),
        ("postal_code", postal_codes),
        ("service_type", service_type),
    ):
        if values is not None:
            if not values:
                return [("impossible", "1")]
            dimensions.extend((dimension, value) for value in sorted(values))
    return dimensions


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

    def metrics(self) -> dict[str, Any]:
        """Return aggregate queue and retention metrics without identifiers or payloads."""
        current = datetime.now(timezone.utc)
        current_text = format_iso_datetime(current)
        with self._lock:
            routing_rows = self._connection.execute(
                "SELECT status, lease_until, created_at FROM routing_jobs"
            ).fetchall()
            delivery_rows = self._connection.execute(
                "SELECT status, next_attempt_at, lease_until, created_at FROM deliveries"
            ).fetchall()
            deletion_rows = self._connection.execute(
                "SELECT status, next_attempt_at, created_at FROM attachment_deletion_jobs"
            ).fetchall()
            attachment_rows = self._connection.execute(
                "SELECT scan_status FROM attachments WHERE status = 'AVAILABLE'"
            ).fetchall()
            dead_letter_rows = self._connection.execute(
                "SELECT queue_type, status FROM dead_letter_jobs"
            ).fetchall()

        def age(value: Any) -> int | None:
            parsed = parse_iso_datetime(value)
            if parsed is None:
                return None
            return max(0, int((current - parsed).total_seconds()))

        routing_pending = [row for row in routing_rows if row["status"] == "PENDING"]
        routing_processing = [row for row in routing_rows if row["status"] == "PROCESSING"]
        routing_leases_expired = [
            row for row in routing_processing
            if row["lease_until"] is not None and str(row["lease_until"]) <= current_text
        ]
        delivery_pending = [row for row in delivery_rows if row["status"] == "PENDING"]
        delivery_retry = [row for row in delivery_rows if row["status"] == "RETRY"]
        delivery_processing = [row for row in delivery_rows if row["status"] == "PROCESSING"]
        delivery_leases_expired = [
            row for row in delivery_processing
            if row["lease_until"] is not None and str(row["lease_until"]) <= current_text
        ]
        deletion_pending = [row for row in deletion_rows if row["status"] == "PENDING"]
        deletion_retry = [row for row in deletion_rows if row["status"] == "RETRY"]
        deletion_failed = [row for row in deletion_rows if row["status"] == "FAILED"]
        dead_letter_open = [row for row in dead_letter_rows if row["status"] == "OPEN"]
        dead_letter_quarantined = [row for row in dead_letter_rows if row["status"] == "QUARANTINED"]

        return {
            "generated_at": current_text,
            "routing": {
                "pending": len(routing_pending),
                "processing": len(routing_processing),
                "dead_letter": sum(
                    1 for row in dead_letter_open if row["queue_type"] == "routing"
                ),
                "lease_expired": len(routing_leases_expired),
                "oldest_pending_age_seconds": age(min(
                    (row["created_at"] for row in routing_pending),
                    default=None,
                )),
            },
            "delivery": {
                "pending": len(delivery_pending),
                "retry": len(delivery_retry),
                "processing": len(delivery_processing),
                "failed": sum(1 for row in delivery_rows if row["status"] == "FAILED"),
                "dead_letter": sum(
                    1 for row in dead_letter_open if row["queue_type"] == "delivery"
                ),
                "lease_expired": len(delivery_leases_expired),
                "oldest_due_age_seconds": age(min(
                    (row["created_at"] for row in delivery_pending + delivery_retry),
                    default=None,
                )),
            },
            "attachments": {
                "scanner_backlog": sum(
                    1 for row in attachment_rows
                    if row["scan_status"] in {"not_scanned", "pending", "retry"}
                ),
                "deletion_pending": len(deletion_pending),
                "deletion_retry": len(deletion_retry),
                "deletion_failed": len(deletion_failed),
                "oldest_deletion_age_seconds": age(min(
                    (row["created_at"] for row in deletion_pending + deletion_retry + deletion_failed),
                    default=None,
                )),
            },
            "dead_letters": {
                "open": len(dead_letter_open),
                "quarantined": len(dead_letter_quarantined),
            },
        }

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
                    expires_at TEXT,
                    consent_expires_at TEXT,
                    suppressed INTEGER NOT NULL DEFAULT 0,
                    envelope_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(sender_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS offers (
                    offer_id TEXT PRIMARY KEY,
                    buyer_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    vertical TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    offer_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS offer_candidate_index (
                    offer_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (offer_id, dimension, value)
                );
                CREATE INDEX IF NOT EXISTS idx_offer_candidate_lookup
                    ON offer_candidate_index(tenant_id, dimension, value, offer_id);
                CREATE INDEX IF NOT EXISTS idx_offers_discovery
                    ON offers(active, tenant_id, vertical, offer_id);
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
                    expires_at TEXT,
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
                CREATE TABLE IF NOT EXISTS lead_suppressions (
                    lead_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachment_deletion_jobs (
                    job_id TEXT PRIMARY KEY,
                    attachment_id TEXT NOT NULL UNIQUE,
                    storage_ref TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dead_letter_jobs (
                    job_id TEXT PRIMARY KEY,
                    queue_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    lead_id TEXT,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    attempts INTEGER NOT NULL,
                    last_error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    quarantined_at TEXT,
                    replayed_at TEXT,
                    UNIQUE(queue_type, resource_id)
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
                CREATE INDEX IF NOT EXISTS idx_attachment_deletion_jobs_due
                    ON attachment_deletion_jobs(status, next_attempt_at);
                CREATE INDEX IF NOT EXISTS idx_payable_offer_month
                    ON payable_records(offer_id, month_key, status);
                """
            )
            self._migrate_sqlite_columns(db)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_leads_expiry ON leads(status, expires_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_leads_consent_expiry ON leads(suppressed, consent_expires_at)"
            )
            self._backfill_offer_candidate_index(db)

    def _backfill_offer_candidate_index(self, db: Any) -> None:
        """Backfill indexes created before candidate selection was introduced."""
        missing = db.execute(
            """
            SELECT o.offer_id
            FROM offers AS o
            LEFT JOIN offer_candidate_index AS i ON i.offer_id = o.offer_id
            WHERE i.offer_id IS NULL
            LIMIT 1
            """
        ).fetchone()
        if missing is None:
            return
        db.execute("DELETE FROM offer_candidate_index")
        for row in db.execute("SELECT offer_json FROM offers").fetchall():
            self._replace_offer_candidate_index(db, json.loads(row["offer_json"]))

    def _replace_offer_candidate_index(self, db: Any, offer: Mapping[str, Any]) -> None:
        db.execute(
            "DELETE FROM offer_candidate_index WHERE offer_id = ?",
            (offer["offer_id"],),
        )
        timestamp = now_iso()
        tenant_id = str(offer.get("tenant_id", "default"))
        for dimension, value in _offer_candidate_dimensions(offer):
            db.execute(
                """
                INSERT INTO offer_candidate_index
                    (offer_id, tenant_id, dimension, value, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(offer_id, dimension, value) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    updated_at=excluded.updated_at
                """,
                (offer["offer_id"], tenant_id, dimension, value, timestamp),
            )

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
        if "expires_at" not in lead_columns:
            db.execute("ALTER TABLE leads ADD COLUMN expires_at TEXT")
        if "consent_expires_at" not in lead_columns:
            db.execute("ALTER TABLE leads ADD COLUMN consent_expires_at TEXT")
        if "suppressed" not in lead_columns:
            db.execute("ALTER TABLE leads ADD COLUMN suppressed INTEGER NOT NULL DEFAULT 0")
        for row in db.execute("SELECT lead_id, envelope_json FROM leads").fetchall():
            try:
                stored_envelope = self.decode_envelope(row["envelope_json"])
            except (TypeError, ValueError, KeyError):
                continue
            expiry = envelope_expiry(stored_envelope)
            consent_expiry = envelope_consent_expiry(stored_envelope)
            db.execute(
                "UPDATE leads SET expires_at = ?, consent_expires_at = ? WHERE lead_id = ?",
                (
                    format_iso_datetime(expiry) if expiry else None,
                    format_iso_datetime(consent_expiry) if consent_expiry else None,
                    row["lead_id"],
                ),
            )
        offer_columns = {
            row[1] for row in db.execute("PRAGMA table_info(offers)").fetchall()
        }
        if "tenant_id" not in offer_columns:
            db.execute("ALTER TABLE offers ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
        if "vertical" not in offer_columns:
            db.execute("ALTER TABLE offers ADD COLUMN vertical TEXT")
        for row in db.execute("SELECT offer_id, offer_json FROM offers").fetchall():
            try:
                stored_offer = json.loads(row["offer_json"])
            except (TypeError, ValueError):
                continue
            db.execute(
                "UPDATE offers SET tenant_id = ?, vertical = ? WHERE offer_id = ?",
                (
                    str(stored_offer.get("tenant_id", "default")),
                    stored_offer.get("vertical"),
                    row["offer_id"],
                ),
            )
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
        if "expires_at" not in attachment_columns:
            db.execute("ALTER TABLE attachments ADD COLUMN expires_at TEXT")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_suppressions (
                lead_id TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS attachment_deletion_jobs (
                job_id TEXT PRIMARY KEY,
                attachment_id TEXT NOT NULL UNIQUE,
                storage_ref TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS dead_letter_jobs (
                job_id TEXT PRIMARY KEY,
                queue_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                lead_id TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                attempts INTEGER NOT NULL,
                last_error TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                quarantined_at TEXT,
                replayed_at TEXT,
                UNIQUE(queue_type, resource_id)
            )
            """
        )

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
        audit_metadata = dict(metadata or {})
        request_id = current_request_id()
        if request_id is not None:
            audit_metadata.setdefault("request_id", request_id)
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
                    json.dumps(audit_metadata, separators=(",", ":"), ensure_ascii=False),
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
                         expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'AVAILABLE', ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata["attachment_id"], metadata["lead_id"], metadata["owner_id"],
                        metadata["idempotency_key"], metadata["purpose"], metadata["filename"],
                        metadata["content_type"], metadata["size_bytes"], metadata["sha256"],
                        metadata["storage_ref"], metadata.get("residency", "TEST"),
                        metadata.get("scan_status", "not_scanned"), metadata.get("scan_engine", "unknown"),
                        metadata.get("scanned_at", timestamp), metadata.get("encryption", "application_encrypted"),
                        metadata.get("expires_at"), timestamp, timestamp,
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

    def expire_attachment(self, attachment_id: str) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT attachment_id, storage_ref, status FROM attachments WHERE attachment_id = ?",
                (attachment_id,),
            ).fetchone()
            if not row or row["status"] != "AVAILABLE":
                return
            timestamp = now_iso()
            db.execute(
                "UPDATE attachments SET status = 'EXPIRED', updated_at = ? WHERE attachment_id = ?",
                (timestamp, attachment_id),
            )
            self._enqueue_attachment_deletion(db, row["attachment_id"], row["storage_ref"], timestamp)

    def list_expired_attachments(self, at: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM attachments
                WHERE status = 'AVAILABLE' AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY expires_at, attachment_id
                """,
                (at or now_iso(),),
            ).fetchall()

    def _enqueue_attachment_deletion(self, db: Any, attachment_id: str, storage_ref: str, timestamp: str) -> None:
        db.execute(
            """
            INSERT INTO attachment_deletion_jobs
                (job_id, attachment_id, storage_ref, status, attempts, next_attempt_at, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', 0, ?, ?, ?)
            ON CONFLICT(attachment_id) DO UPDATE SET
                storage_ref=excluded.storage_ref,
                status=CASE WHEN attachment_deletion_jobs.status = 'DONE' THEN 'DONE' ELSE 'PENDING' END,
                next_attempt_at=excluded.next_attempt_at,
                updated_at=excluded.updated_at
            """,
            (f"delete_{uuid4().hex}", attachment_id, storage_ref, timestamp, timestamp, timestamp),
        )

    def enqueue_attachment_deletion(self, attachment_id: str, storage_ref: str) -> None:
        with self.transaction() as db:
            self._enqueue_attachment_deletion(db, attachment_id, storage_ref, now_iso())

    def list_due_attachment_deletions(self, at: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM attachment_deletion_jobs
                WHERE status IN ('PENDING', 'RETRY') AND next_attempt_at <= ?
                ORDER BY next_attempt_at, created_at
                """,
                (at or now_iso(),),
            ).fetchall()

    def mark_attachment_deletion(
        self,
        job_id: str,
        *,
        status: str,
        attempts: int,
        next_attempt_at: str,
        last_error: str | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                """
                UPDATE attachment_deletion_jobs
                SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, attempts, next_attempt_at, last_error, now_iso(), job_id),
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
                         expires_at, consent_expires_at, suppressed, envelope_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        format_iso_datetime(expiry) if (expiry := envelope_expiry(envelope)) else None,
                        format_iso_datetime(consent_expiry) if (consent_expiry := envelope_consent_expiry(envelope)) else None,
                        0,
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

    def is_lead_suppressed(self, lead_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT suppressed FROM leads WHERE lead_id = ?",
                (lead_id,),
            ).fetchone()
        return bool(row and row["suppressed"])

    def list_expired_leads(self, at: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM leads
                WHERE status IN ('NEW', 'PINGED', 'POSTED')
                  AND expires_at IS NOT NULL AND expires_at <= ?
                ORDER BY expires_at, lead_id
                """,
                (at or now_iso(),),
            ).fetchall()

    def list_consent_expired_leads(self, at: str | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(
                """
                SELECT * FROM leads
                WHERE suppressed = 0
                  AND status IN ('NEW', 'PINGED', 'POSTED')
                  AND consent_expires_at IS NOT NULL AND consent_expires_at <= ?
                ORDER BY consent_expires_at, lead_id
                """,
                (at or now_iso(),),
            ).fetchall()

    def _redact_undelivered_for_lead(self, db: Any, lead_id: str, reason: str, timestamp: str) -> None:
        rows = db.execute(
            """
            SELECT delivery_id, envelope_json FROM deliveries
            WHERE lead_id = ? AND status IN ('PENDING', 'RETRY', 'PROCESSING')
            """,
            (lead_id,),
        ).fetchall()
        for row in rows:
            db.execute(
                """
                UPDATE deliveries
                SET envelope_json = ?, status = 'REDACTED', lease_owner = NULL,
                    lease_until = NULL, last_error = ?, updated_at = ?
                WHERE delivery_id = ?
                """,
                (self._redact_envelope(row["envelope_json"]), reason[:500], timestamp, row["delivery_id"]),
            )
        db.execute(
            "UPDATE routing_jobs SET status = 'DONE', lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE lead_id = ?",
            (timestamp, lead_id),
        )

    def suppress_lead(self, lead_id: str, *, reason: str, actor_id: str) -> bool:
        """Block future routing/contact without erasing already delivered data."""
        with self.transaction() as db:
            row = db.execute(
                "SELECT tenant_id FROM leads WHERE lead_id = ?",
                (lead_id,),
            ).fetchone()
            if not row:
                return False
            timestamp = now_iso()
            db.execute(
                """
                INSERT INTO lead_suppressions(lead_id, reason, actor_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(lead_id) DO UPDATE SET
                    reason=excluded.reason, actor_id=excluded.actor_id, updated_at=excluded.updated_at
                """,
                (lead_id, reason, actor_id, timestamp, timestamp),
            )
            db.execute(
                "UPDATE leads SET suppressed = 1, updated_at = ? WHERE lead_id = ?",
                (timestamp, lead_id),
            )
            db.execute(
                "UPDATE pings SET status = 'SUPPRESSED', updated_at = ? WHERE lead_id = ? AND status = 'OPEN'",
                (timestamp, lead_id),
            )
            self._redact_undelivered_for_lead(db, lead_id, reason, timestamp)
            db.execute(
                """
                INSERT INTO audit_events
                    (audit_id, tenant_id, actor_id, action, resource_type,
                     resource_id, metadata_json, created_at)
                VALUES (?, ?, ?, 'lead.suppressed', 'lead', ?, ?, ?)
                """,
                (
                    f"audit_{uuid4().hex}", row["tenant_id"], actor_id, lead_id,
                    json.dumps({"reason": reason}, separators=(",", ":")), timestamp,
                ),
            )
        return True

    def _update_lead_status_in_transaction(
        self,
        db: Any,
        lead_id: str,
        status: str,
        *,
        reason: str | None = None,
    ) -> str | None:
        """Apply one lifecycle transition using the shared legal graph."""
        row = db.execute(
            "SELECT status FROM leads WHERE lead_id = ?",
            (lead_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Lead not found: {lead_id}")
        current = str(row["status"])
        if current == status:
            return None
        direct_post = current == "NEW" and status == "POSTED" and reason == "direct_delivery"
        if not direct_post and status not in LEGAL_LEAD_TRANSITIONS.get(current, set()):
            raise InvalidStatusTransition(
                f"Invalid lead status transition {current} -> {status}"
            )
        db.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE lead_id = ?",
            (status, now_iso(), lead_id),
        )
        return current

    def expire_lead(self, lead_id: str) -> bool:
        """Move an unaccepted lead to EXPIRED and stop pending delivery."""
        with self.transaction() as db:
            row = db.execute(
                "SELECT status FROM leads WHERE lead_id = ?",
                (lead_id,),
            ).fetchone()
            if not row:
                return False
            current = str(row["status"])
            if "EXPIRED" not in LEGAL_LEAD_TRANSITIONS.get(current, set()):
                return False
            self._update_lead_status_in_transaction(db, lead_id, "EXPIRED")
            timestamp = now_iso()
            db.execute(
                "UPDATE pings SET status = 'EXPIRED', updated_at = ? WHERE lead_id = ? AND status = 'OPEN'",
                (timestamp, lead_id),
            )
            self._redact_undelivered_for_lead(db, lead_id, "Lead expired", timestamp)
        return True

    def update_lead_status(
        self,
        lead_id: str,
        status: str,
        *,
        reason: str | None = None,
    ) -> str | None:
        """Move a lead to a new status.

        Returns the previous status, or ``None`` when the lead was already in
        the requested status (no transition was applied).
        """
        with self.transaction() as db:
            return self._update_lead_status_in_transaction(
                db, lead_id, status, reason=reason
            )

    def routing_job_pending(self, lead_id: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT status FROM routing_jobs WHERE lead_id = ?", (lead_id,)
            ).fetchone()
        return bool(row and row["status"] in {"PENDING", "PROCESSING"})

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
        self,
        lead_id: str,
        error: str,
        worker_id: str | None = None,
        *,
        max_attempts: int = 5,
    ) -> None:
        with self.transaction() as db:
            row = db.execute(
                "SELECT attempts FROM routing_jobs WHERE lead_id = ?",
                (lead_id,),
            ).fetchone()
            attempts = int(row["attempts"]) if row else max_attempts
            status = "DEAD_LETTER" if attempts >= max_attempts else "PENDING"
            if worker_id:
                db.execute(
                    """
                    UPDATE routing_jobs
                    SET status = ?, last_error = ?, lease_owner = NULL,
                        lease_until = NULL, updated_at = ?
                    WHERE lead_id = ? AND lease_owner = ?
                    """,
                    (status, error[:500], now_iso(), lead_id, worker_id),
                )
            else:
                db.execute(
                    """
                    UPDATE routing_jobs
                    SET status = ?, last_error = ?, lease_owner = NULL,
                        lease_until = NULL, updated_at = ?
                    WHERE lead_id = ?
                    """,
                    (status, error[:500], now_iso(), lead_id),
                )
            if status == "DEAD_LETTER":
                self._record_dead_letter_in_transaction(
                    db,
                    queue_type="routing",
                    resource_id=lead_id,
                    lead_id=lead_id,
                    attempts=attempts,
                    last_error=error,
                )

    def _record_dead_letter_in_transaction(
        self,
        db: Any,
        *,
        queue_type: str,
        resource_id: str,
        lead_id: str | None,
        attempts: int,
        last_error: str,
    ) -> None:
        timestamp = now_iso()
        db.execute(
            """
            INSERT INTO dead_letter_jobs
                (job_id, queue_type, resource_id, lead_id, status, attempts,
                 last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
            ON CONFLICT(queue_type, resource_id) DO UPDATE SET
                lead_id = excluded.lead_id,
                status = 'OPEN',
                attempts = excluded.attempts,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at,
                quarantined_at = NULL,
                replayed_at = NULL
            """,
            (
                f"dlq_{queue_type}_{resource_id}",
                queue_type,
                resource_id,
                lead_id,
                max(1, int(attempts)),
                str(last_error)[:500],
                timestamp,
                timestamp,
            ),
        )

    def record_dead_letter(
        self,
        *,
        queue_type: str,
        resource_id: str,
        lead_id: str | None,
        attempts: int,
        last_error: str,
    ) -> None:
        if queue_type not in {"delivery", "routing"}:
            raise ValueError("Unsupported dead-letter queue type")
        with self.transaction() as db:
            self._record_dead_letter_in_transaction(
                db,
                queue_type=queue_type,
                resource_id=resource_id,
                lead_id=lead_id,
                attempts=attempts,
                last_error=last_error,
            )

    def list_dead_letters(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM dead_letter_jobs"
        values: list[Any] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY created_at, job_id"
        with self._lock:
            rows = self._connection.execute(query, values).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "queue_type": row["queue_type"],
                "resource_id": row["resource_id"],
                "lead_id": row["lead_id"],
                "status": row["status"],
                "attempts": row["attempts"],
                "last_error": row["last_error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "quarantined_at": row["quarantined_at"],
                "replayed_at": row["replayed_at"],
            }
            for row in rows
        ]

    def quarantine_dead_letter(self, job_id: str) -> bool:
        with self.transaction() as db:
            cursor = db.execute(
                """
                UPDATE dead_letter_jobs
                SET status = 'QUARANTINED', quarantined_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'OPEN'
                """,
                (now_iso(), now_iso(), job_id),
            )
        return cursor.rowcount == 1

    def replay_dead_letter(self, job_id: str) -> bool:
        with self.transaction() as db:
            row = db.execute(
                "SELECT queue_type, resource_id, status FROM dead_letter_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not row or row["status"] not in {"OPEN", "QUARANTINED"}:
                return False
            timestamp = now_iso()
            if row["queue_type"] == "delivery":
                cursor = db.execute(
                    """
                    UPDATE deliveries
                    SET status = 'RETRY', next_attempt_at = ?, last_error = NULL,
                        lease_owner = NULL, lease_until = NULL, updated_at = ?
                    WHERE delivery_id = ? AND status = 'FAILED'
                    """,
                    (timestamp, timestamp, row["resource_id"]),
                )
            else:
                cursor = db.execute(
                    """
                    UPDATE routing_jobs
                    SET status = 'PENDING', last_error = NULL,
                        lease_owner = NULL, lease_until = NULL, updated_at = ?
                    WHERE lead_id = ? AND status = 'DEAD_LETTER'
                    """,
                    (timestamp, row["resource_id"]),
                )
            if cursor.rowcount != 1:
                return False
            db.execute(
                """
                UPDATE dead_letter_jobs
                SET status = 'REPLAYED', replayed_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (timestamp, timestamp, job_id),
            )
        return True

    # ─── Offers ────────────────────────────────────────────────────────────

    def upsert_offer(self, offer: dict[str, Any]) -> None:
        timestamp = now_iso()
        with self.transaction() as db:
            db.execute(
                """
                INSERT INTO offers
                    (offer_id, buyer_id, tenant_id, vertical, active, offer_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(offer_id) DO UPDATE SET
                    buyer_id=excluded.buyer_id,
                    tenant_id=excluded.tenant_id,
                    vertical=excluded.vertical,
                    active=excluded.active,
                    offer_json=excluded.offer_json,
                    updated_at=excluded.updated_at
                """,
                (
                    offer["offer_id"],
                    offer["buyer_id"],
                    str(offer.get("tenant_id", "default")),
                    offer.get("vertical"),
                    int(offer.get("active", True)),
                    json.dumps(offer, separators=(",", ":"), ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
            self._replace_offer_candidate_index(db, offer)

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
        return [json.loads(row["offer_json"]) for row in rows]

    def list_offer_candidates(
        self,
        payload: dict[str, Any],
        *,
        tenant_id: str | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Return a conservative subset for the existing full matcher.

        The index contains only necessary conditions. Fallback and impossible
        rows preserve behavior for uncertain or contradictory configurations.
        """
        attributes = payload.get("attributes", {})
        location = payload.get("location", {})
        vertical = attributes.get("vertical") if isinstance(attributes, Mapping) else None
        country = location.get("country_code") if isinstance(location, Mapping) else None
        if not isinstance(vertical, str) or not isinstance(country, str):
            return []

        where = ["o.active = 1"] if active_only else []
        params: list[Any] = []
        if tenant_id is None:
            cte = "SELECT offer_id, dimension, value FROM offer_candidate_index"
        else:
            cte = "SELECT offer_id, dimension, value FROM offer_candidate_index WHERE tenant_id = ?"
            params.append(tenant_id)
        state = location.get("state_region") if isinstance(location, Mapping) else None
        postal = location.get("postal_code") if isinstance(location, Mapping) else None
        service_type = attributes.get("service_type") if isinstance(attributes, Mapping) else None

        def exists(dimension: str, value: Any | None = None) -> str:
            clause = ["i.offer_id = o.offer_id", "i.dimension = ?"]
            params.append(dimension)
            if value is not None:
                clause.append("i.value = ?")
                params.append(value)
            return "EXISTS (SELECT 1 FROM candidate_index AS i WHERE " + " AND ".join(clause) + ")"

        def optional_dimension(dimension: str, value: Any) -> str:
            return "(NOT " + exists(dimension) + " OR " + exists(dimension, value) + ")"

        where.append("NOT " + exists("impossible"))
        where.append("(" + exists("fallback") + " OR (" + " AND ".join(
            [
                exists("vertical", vertical),
                exists("country_code", country),
                optional_dimension("state_region", state),
                optional_dimension("postal_code", postal),
                optional_dimension("service_type", service_type),
            ]
        ) + "))")
        if not active_only:
            where = [condition for condition in where if condition != "o.active = 1"]
        query = (
            "WITH candidate_index AS (" + cte + ") "
            "SELECT o.offer_json FROM offers AS o WHERE " + " AND ".join(where) + " ORDER BY o.offer_id"
        )
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
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

    def has_delivered_post_for_buyer(self, lead_id: str, buyer_id: str) -> bool:
        """Return whether a buyer received a successfully delivered winning post.

        Ping, event, pending, retry, and failed delivery records must not grant
        access to full-PII attachment bytes.
        """
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM deliveries
                WHERE lead_id = ? AND buyer_id = ?
                  AND kind = 'post' AND status = 'DELIVERED'
                LIMIT 1
                """,
                (lead_id, buyer_id),
            ).fetchone()
        return row is not None

    # ─── Lifecycle ─────────────────────────────────────────────────────────

    def get_event(self, event_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(
                "SELECT * FROM lifecycle_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

    def insert_event(self, lead_id: str, event_name: str, envelope: dict[str, Any]) -> bool:
        payload = envelope["lcp"]["payload"]
        event_id = envelope["lcp"]["message"]["id"]
        encoded = self.encode_envelope(envelope)
        with self.transaction() as db:
            existing = db.execute(
                "SELECT lead_id, event_name, envelope_json FROM lifecycle_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing:
                same_event = (
                    existing["lead_id"] == lead_id
                    and existing["event_name"] == event_name
                    and self.decode_envelope(existing["envelope_json"]) == envelope
                )
                if not same_event:
                    raise EventIdempotencyConflict(
                        f"Event message ID was reused with different content: {event_id}"
                    )
                return False
            db.execute(
                """
                INSERT INTO lifecycle_events
                    (event_id, lead_id, event_name, timestamp, envelope_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, lead_id, event_name, payload["timestamp"], encoded),
            )
        return True

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
            attachments = db.execute(
                "SELECT attachment_id, storage_ref FROM attachments WHERE lead_id = ?",
                (lead_id,),
            ).fetchall()
            for attachment in attachments:
                db.execute(
                    "UPDATE attachments SET status = 'REDACTED', updated_at = ? WHERE attachment_id = ?",
                    (timestamp, attachment["attachment_id"]),
                )
                self._enqueue_attachment_deletion(
                    db, attachment["attachment_id"], attachment["storage_ref"], timestamp
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

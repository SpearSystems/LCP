"""Request-history projection surface (LEP-0005, accepted 2026-11-03).

This module is the **single decision point** the record's "Tenant binding at
query time" section requires: ``history_rows`` composes the union of the
projection tables (leads/pings/bids/deliveries/lifecycle_events) and the
tenant predicate ``leads.tenant_id = :caller_tenant`` appears **exactly
once**, in the ``tenant_leads`` CTE that every row source joins through.
The route, the service dispatch, and the MCP adapter consume this output
and add no second filter.

Guarantees pinned here, per the record:

* No raw envelope bodies and no plaintext contacts. The closed row shape
  (``history_row_violations``) mirrors the drafted
  ``schemas/history-request-row.json`` as a python shape guard: member-level
  ``additionalProperties: false``, patterns for every emitted field, and a
  zero-tolerance pre-serialization check (a violation raises — it never
  serves an unbounded or contact-shaped value).
* ``caller_phone_hash`` is a per-pair HMAC keyed to the REQUESTING
  credential's secret, computed at read time from plaintext the platform
  still holds (the LEP-0003 §3 / contacted-digest re-key precedent,
  ``router.py`` ``_contacted_hashes_for``), never stored. Absent — never
  blank — when the caller holds no pair secret or the lead's plaintext is
  gone (erasure); that is what makes erased consumers unreachable through
  the contact surface by construction.
* ``lead_id`` is pinned at the projection (rule 6, #202 F1): a stored
  publisher-minted id that fails the contact-shape guard is OMITTED from
  the row (never coerced, never truncated, never hashed); the row survives
  with ``request_id`` as its handle.
* ``ack`` mints no rows: no table stores acks (#202 F3), so ``ack`` is a
  legal filter value returning the empty set, and it is absent from the
  row-shape ``message_type`` enum.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode
from uuid import UUID

from .messages import pair_phone_hash

# §4 closed type set as it is legal in the ``message_type`` FILTER; ``ack``
# is a legal filter value that matches no stored record in v1 (row universe).
FILTER_MESSAGE_TYPES = ("lead", "call", "ping", "bid", "post", "ack", "event")
# Stored-record classes a row may carry (row schema enum; ``ack`` excluded).
ROW_MESSAGE_TYPES = ("lead", "call", "ping", "bid", "post", "event")

ALLOWED_PARAMS = frozenset(
    {
        "request_id",
        "lead_id",
        "message_type",
        "status",
        "vertical",
        "caller_phone_hash",
        "from",
        "to",
        "limit",
        "cursor",
    }
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Rule-6 pins (LEP-0005 revision 2026-11-03). The anchored identifier
# charset bounds every free-text identifier field; the contact-shape refusal
# rejects (i) a 10-15 digit phone run starting the value or not preceded by
# a letter/digit/hyphen, (ii) an all-digits-and-phone-punctuation value, and
# (iii) an email address. re.ASCII keeps ``\d`` the ECMA-262 ``\d`` the
# drafted JSON-Schema pattern is evaluated with (the record's battery ran
# both validator legs); non-ASCII input fails the charset pin regardless.
IDENTIFIER_CHARSET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$", re.ASCII)
CONTACT_SHAPES = re.compile(
    r"(?<![-\w])\d{10,15}|^[0-9 .()+-]{10,}$"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    re.ASCII,
)
PHONE_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$", re.ASCII)
CANONICAL_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$", re.ASCII)
LIMIT_PATTERN = re.compile(r"^\d+$", re.ASCII)

# Closed row shape, mirroring the drafted schemas/history-request-row.json.
# The eight message-type schema files are untouched (LEP-0005 Scope).
ROW_REQUIRED_MEMBERS = (
    "request_id",
    "message_type",
    "direction",
    "status",
    "created_at",
)
ROW_MEMBERS = {
    "request_id": "uuid",
    "lead_id": "identifier",
    "message_type": "message_type",
    "direction": "direction",
    "status": "identifier",
    "sender_id": "identifier",
    "receiver_id": "identifier",
    "vertical": "identifier",
    "test": "boolean",
    "price_cents": "nonneg_int",
    "currency": "currency",
    "caller_phone_hash": "phone_hash",
    "expires_at": "timestamp",
    "created_at": "timestamp",
    "updated_at": "timestamp",
}


def identifier_pinned(value: Any) -> bool:
    """True when a free-text identifier field may ride the surface (rule 6 bound)."""
    return isinstance(value, str) and bool(IDENTIFIER_CHARSET.fullmatch(value))


def lead_id_pinned(value: Any) -> bool:
    """True when a ``lead_id`` may ride the surface: charset bound + contact refusal."""
    return (
        identifier_pinned(value) and CONTACT_SHAPES.search(value) is None  # type: ignore[arg-type]
    )


def _is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def history_row_violations(row: dict[str, Any]) -> list[str]:
    """Shape guard for emitted history rows (the drafted row schema as python).

    Returns a list of violation strings; empty means the row conforms.
    Member-level ``additionalProperties: false``: any name outside the
    allowlist is a violation — a leaked envelope body or contact member
    fails here, loudly, before serialization.
    """
    violations: list[str] = []
    unexpected = sorted(set(row) - set(ROW_MEMBERS))
    if unexpected:
        violations.append(f"unexpected members: {unexpected}")
    for member in ROW_REQUIRED_MEMBERS:
        if row.get(member) is None:
            violations.append(f"required member missing: {member}")
    checks = {
        "uuid": _is_uuid,
        "identifier": identifier_pinned,
        "message_type": lambda value: value in ROW_MESSAGE_TYPES,
        "direction": lambda value: value in ("received", "sent"),
        "boolean": lambda value: isinstance(value, bool),
        "nonneg_int": lambda value: (
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
        ),
        "currency": lambda value: (
            isinstance(value, str) and bool(CURRENCY_PATTERN.fullmatch(value))
        ),
        "phone_hash": lambda value: (
            isinstance(value, str) and bool(PHONE_HASH_PATTERN.fullmatch(value))
        ),
        "timestamp": lambda value: (
            isinstance(value, str) and bool(CANONICAL_TIMESTAMP.fullmatch(value))
        ),
    }
    for member, kind in ROW_MEMBERS.items():
        if member not in row or row[member] is None:
            continue
        if not checks[kind](row[member]):
            violations.append(f"member fails shape: {member}")
    if "lead_id" in row and not lead_id_pinned(row["lead_id"]):
        violations.append("member fails shape: lead_id (rule 6 pin)")
    return violations


def _bad_param(message: str) -> Any:
    from .router import RequestError  # local: history is imported by router

    raise RequestError(message, "LCP-001", 400)


def encode_cursor(created_at: str, request_id: str) -> str:
    raw = f"{created_at},{request_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> tuple[str, str]:
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return _bad_param("cursor is not a valid keyset token")
    timestamp, separator, request_id = decoded.partition(",")
    if (
        not separator
        or not CANONICAL_TIMESTAMP.fullmatch(timestamp)
        or not _is_uuid(request_id)
    ):
        _bad_param("cursor is not a valid keyset token")
    return timestamp, request_id


def _single_values(query: str) -> dict[str, str]:
    collected: dict[str, str] = {}
    for name, value in parse_qsl(query, keep_blank_values=True):
        if name not in ALLOWED_PARAMS:
            _bad_param(f"Unknown parameter: {name}")
        if name in collected:
            _bad_param(f"Parameter provided more than once: {name}")
        collected[name] = value
    return collected


def parse_history_params(query: str) -> dict[str, Any]:
    """Validate the closed filter allowlist; malformed input fails loudly (400)."""
    from .router import RequestError  # local: history is imported by router

    collected = _single_values(query)
    filters: dict[str, Any] = {}

    if "request_id" in collected:
        if not _is_uuid(collected["request_id"]):
            _bad_param("request_id must be a UUID")
        filters["request_id"] = collected["request_id"].lower()
    if "lead_id" in collected:
        if not lead_id_pinned(collected["lead_id"]):
            _bad_param("lead_id is malformed or contact-shaped")
        filters["lead_id"] = collected["lead_id"]
    if "message_type" in collected:
        if collected["message_type"] not in FILTER_MESSAGE_TYPES:
            raise RequestError("message_type is not a §4 type", "LCP-001", 400)
        filters["message_type"] = collected["message_type"]
    if "status" in collected:
        # Open enum (§6): an unrecognized well-shaped value is a valid query.
        # Shape hygiene is not vocabulary closure: contact-shaped text 400s.
        if not lead_id_pinned(collected["status"]):
            _bad_param("status is malformed or contact-shaped")
        filters["status"] = collected["status"]
    if "vertical" in collected:
        if not collected["vertical"]:
            _bad_param("vertical must be a non-empty string")
        filters["vertical"] = collected["vertical"]
    if "caller_phone_hash" in collected:
        if not PHONE_HASH_PATTERN.fullmatch(collected["caller_phone_hash"]):
            _bad_param("caller_phone_hash must be 64 lowercase hex characters")
        filters["caller_phone_hash"] = collected["caller_phone_hash"]
    for name in ("from", "to"):
        if name in collected:
            if not CANONICAL_TIMESTAMP.fullmatch(collected[name]):
                _bad_param(f"{name} must be whole-second UTC (YYYY-MM-DDThh:mm:ssZ)")
            filters[name] = collected[name]
    if "to" not in filters:
        filters["to"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    limit = DEFAULT_LIMIT
    if "limit" in collected:
        if not LIMIT_PATTERN.fullmatch(collected["limit"]) or not (
            1 <= int(collected["limit"]) <= MAX_LIMIT
        ):
            # Explicit refusal, never a silent clamp: clamping hides caller bugs.
            _bad_param(f"limit must be an integer in 1..{MAX_LIMIT}")
        limit = int(collected["limit"])
    filters["limit"] = limit
    if "cursor" in collected:
        filters["cursor"] = decode_cursor(collected["cursor"])
    return filters


_SELECT_COLUMNS = """
       {rid} AS request_id, {lead} AS lead_id, {mtype} AS message_type,
       {direction} AS direction, {status} AS status,
       {sender} AS sender_id, {receiver} AS receiver_id,
       {vertical} AS vertical, {test} AS test,
       {expires} AS expires_at, {created} AS created_at,
       {updated} AS updated_at, {price} AS price_cents,
       {currency} AS currency, {lead_env} AS lead_envelope,
       {row_env} AS row_envelope
"""


def _branch(
    *,
    table: str,
    alias: str | None,
    request_id: str,
    lead_id: str,
    message_type: str,
    direction: str,
    status: str,
    sender: str,
    receiver: str,
    vertical: str,
    test: str,
    expires: str | None,
    created: str,
    updated: str | None,
    price: str | None,
    currency: str | None,
    lead_envelope: str,
    row_envelope: str | None,
    join: str,
    where: list[str],
    values: list[Any],
) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""

    def qualified(column: str) -> str:
        return (
            f"{prefix}{column}"
            if column.count("?") == 0 and not column.startswith(("'", "CAST("))
            else column
        )

    text_null = "CAST(NULL AS TEXT)"
    int_null = "CAST(NULL AS INTEGER)"
    sql = _SELECT_COLUMNS.format(
        rid=qualified(request_id),
        lead=qualified(lead_id),
        mtype=message_type
        if message_type.startswith(("'", "?"))
        else qualified(message_type),
        direction=direction if direction.startswith("'") else qualified(direction),
        status=qualified(status),
        sender=sender if sender.startswith(("'", "?")) else qualified(sender),
        receiver=qualified(receiver),
        vertical=qualified(vertical),
        test=qualified(test),
        expires=expires if (expires is None) else qualified(expires),
        created=qualified(created),
        updated=updated if (updated is None) else qualified(updated),
        price=price if (price is None) else qualified(price),
        currency=currency if (currency is None) else qualified(currency),
        lead_env=qualified(lead_envelope),
        row_env=row_envelope if row_envelope else text_null,
    )
    sql += f" FROM {table}{join}\n    WHERE " + " AND ".join(where)
    return sql, values


def history_rows(
    store: Any,
    *,
    platform_id: str,
    caller_id: str,
    caller_tenant: str | None,
    is_operator: bool,
    caller_secret: str | None,
    filters: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, str | None]:
    """THE single decision point (LEP-0005 "Tenant binding at query time").

    Composes the union of the projection tables behind one tenant predicate,
    applies the authorization-matrix row scoping, the rule-6 identifier pins,
    the read-time per-pair hash rules, and the keyset page cut. Returns
    ``(page_rows, total_in_scope, next_cursor)``. Callers add no second
    tenant check: per-row tenant checks downstream would be a bug, not
    defense-in-depth — by then the row set has crossed the boundary.
    """
    message_type = filters.get("message_type")
    # ``ack`` is a legal §4 filter value with no backing table: empty set.
    if message_type == "ack":
        return [], 0, None

    cte_where = ["tenant_id = ?"]
    cte_values: list[Any] = [caller_tenant]
    if "vertical" in filters:
        cte_where.append("vertical = ?")
        cte_values.append(filters["vertical"])
    if "lead_id" in filters:
        cte_where.append("lead_id = ?")
        cte_values.append(filters["lead_id"])

    branches: list[tuple[str, list[Any]]] = []

    def scoped(column: str) -> tuple[list[str], list[Any]]:
        where: list[str] = []
        values: list[Any] = []
        if "lead_id" not in filters:
            where.append(f"{column} = ?")
        return where, values

    def time_clause(column: str) -> tuple[list[str], list[Any]]:
        where: list[str] = []
        values: list[Any] = []
        if "from" in filters:
            where.append(f"{column} >= ?")
            values.append(filters["from"])
        if "to" in filters:
            where.append(f"{column} <= ?")
            values.append(filters["to"])
        return where, values

    def eq(where: list[str], values: list[Any], column: str, value: Any) -> None:
        where.append(f"{column} = ?")
        values.append(value)

    # leads: own columns; row message_type is the stored lead/call type.
    if message_type in (None, "lead", "call"):
        where: list[str] = ["1=1"]
        values: list[Any] = []
        if message_type in ("lead", "call"):
            eq(where, values, "message_type", message_type)
        if "status" in filters:
            eq(where, values, "status", filters["status"])
        if "request_id" in filters:
            eq(where, values, "message_id", filters["request_id"])
        extra, extra_values = time_clause("created_at")
        where += extra
        values += extra_values
        branches.append(
            (
                """
    SELECT message_id AS request_id, lead_id AS lead_id,
           message_type AS message_type, 'received' AS direction,
           status AS status, sender_id AS sender_id,
           receiver_id AS receiver_id, vertical AS vertical,
           test AS test, expires_at AS expires_at,
           created_at AS created_at, updated_at AS updated_at,
           CAST(NULL AS INTEGER) AS price_cents,
           CAST(NULL AS TEXT) AS currency,
           envelope_json AS lead_envelope,
           CAST(NULL AS TEXT) AS row_envelope
    FROM tenant_leads
    WHERE """
                + " AND ".join(where),
                values,
            )
        )

    # pings: platform -> buyer, sent.
    if message_type in (None, "ping"):
        where = ["1=1"]
        values = [platform_id]
        if "status" in filters:
            eq(where, values, "p.status", filters["status"])
        if "request_id" in filters:
            eq(where, values, "p.message_id", filters["request_id"])
        extra, extra_values = time_clause("p.created_at")
        where += extra
        values += extra_values
        branches.append(
            (
                """
    SELECT p.message_id AS request_id, p.lead_id AS lead_id,
           'ping' AS message_type, 'sent' AS direction,
           p.status AS status, ? AS sender_id,
           p.buyer_id AS receiver_id, tl.vertical AS vertical,
           tl.test AS test, p.expires_at AS expires_at,
           p.created_at AS created_at, p.updated_at AS updated_at,
           CAST(NULL AS INTEGER) AS price_cents,
           CAST(NULL AS TEXT) AS currency,
           tl.envelope_json AS lead_envelope,
           CAST(NULL AS TEXT) AS row_envelope
    FROM pings p JOIN tenant_leads tl ON tl.lead_id = p.lead_id
    WHERE """
                + " AND ".join(where),
                values,
            )
        )

    # bids: buyer -> platform, received; status is the decision column;
    # created_at is received_at; no expires_at/updated_at column exists.
    if message_type in (None, "bid"):
        where = ["1=1"]
        values = [platform_id]
        if "status" in filters:
            eq(where, values, "b.decision", filters["status"])
        if "request_id" in filters:
            eq(where, values, "b.message_id", filters["request_id"])
        extra, extra_values = time_clause("b.received_at")
        where += extra
        values += extra_values
        branches.append(
            (
                """
    SELECT b.message_id AS request_id, b.lead_id AS lead_id,
           'bid' AS message_type, 'received' AS direction,
           b.decision AS status, b.buyer_id AS sender_id,
           ? AS receiver_id, tl.vertical AS vertical,
           tl.test AS test, CAST(NULL AS TEXT) AS expires_at,
           b.received_at AS created_at,
           CAST(NULL AS TEXT) AS updated_at,
           b.bid_price_cents AS price_cents, b.currency AS currency,
           tl.envelope_json AS lead_envelope,
           CAST(NULL AS TEXT) AS row_envelope
    FROM bids b JOIN tenant_leads tl ON tl.lead_id = b.lead_id
    WHERE """
                + " AND ".join(where),
                values,
            )
        )

    # deliveries: platform -> buyer, sent. kind=ping attempts project onto the
    # pings row (no duplicate rows); kind=post/event mint rows. An outbound
    # event also stored in lifecycle_events is minted once, from deliveries.
    if message_type in (None, "post", "event"):
        where = ["kind IN ('post', 'event')"]
        if message_type in ("post", "event"):
            where.append("kind = ?")
        where.append(
            "(kind = 'post' OR NOT EXISTS ("
            "SELECT 1 FROM lifecycle_events le WHERE le.event_id = d.message_id))"
        )
        # The SELECT list's ``? AS sender_id`` binds before any WHERE value
        # (SQL placeholder order is textual), so the platform id leads here.
        values: list[Any] = [platform_id]
        if message_type in ("post", "event"):
            values.append(message_type)
        if "status" in filters:
            eq(where, values, "d.status", filters["status"])
        if "request_id" in filters:
            eq(where, values, "d.message_id", filters["request_id"])
        extra, extra_values = time_clause("d.created_at")
        where += extra
        values += extra_values
        branches.append(
            (
                """
    SELECT d.message_id AS request_id, d.lead_id AS lead_id,
           d.kind AS message_type, 'sent' AS direction,
           d.status AS status, ? AS sender_id,
           d.buyer_id AS receiver_id, tl.vertical AS vertical,
           tl.test AS test, CAST(NULL AS TEXT) AS expires_at,
           d.created_at AS created_at, d.updated_at AS updated_at,
           CAST(NULL AS INTEGER) AS price_cents,
           CAST(NULL AS TEXT) AS currency,
           tl.envelope_json AS lead_envelope,
           CAST(NULL AS TEXT) AS row_envelope
    FROM deliveries d JOIN tenant_leads tl ON tl.lead_id = d.lead_id
    WHERE """
                + " AND ".join(where),
                values,
            )
        )

    # lifecycle_events: persisted records; ids/direction from the stored
    # envelope (no columns exist); events also backed by a deliveries row
    # are minted there instead.
    if message_type in (None, "event"):
        where = [
            "NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.message_id = e.event_id)"
        ]
        values = []
        if "status" in filters:
            eq(where, values, "e.event_name", filters["status"])
        if "request_id" in filters:
            eq(where, values, "e.event_id", filters["request_id"])
        extra, extra_values = time_clause("e.timestamp")
        where += extra
        values += extra_values
        branches.append(
            (
                """
    SELECT e.event_id AS request_id, e.lead_id AS lead_id,
           'event' AS message_type,
           CAST(NULL AS TEXT) AS direction,
           e.event_name AS status,
           CAST(NULL AS TEXT) AS sender_id,
           CAST(NULL AS TEXT) AS receiver_id, tl.vertical AS vertical,
           tl.test AS test, CAST(NULL AS TEXT) AS expires_at,
           e.timestamp AS created_at,
           CAST(NULL AS TEXT) AS updated_at,
           CAST(NULL AS INTEGER) AS price_cents,
           CAST(NULL AS TEXT) AS currency,
           tl.envelope_json AS lead_envelope,
           e.envelope_json AS row_envelope
    FROM lifecycle_events e JOIN tenant_leads tl ON tl.lead_id = e.lead_id
    WHERE """
                + " AND ".join(where),
                values,
            )
        )

    if not branches:
        return [], 0, None

    sql = (
        "WITH tenant_leads AS (\n"
        "      SELECT lead_id, tenant_id, message_id, sender_id, receiver_id,\n"
        "             message_type, status, test, vertical, expires_at,\n"
        "             envelope_json, created_at, updated_at\n"
        "      FROM leads\n"
        "      WHERE " + " AND ".join(cte_where) + "\n"
        "    )\n"
        + "\n    UNION ALL\n".join(branch_sql for branch_sql, _ in branches)
        + "\n    ORDER BY created_at, request_id"
    )
    parameters = cte_values + [
        value for _, branch_values in branches for value in branch_values
    ]
    with store._lock:
        raw_rows = store._connection.execute(sql, parameters).fetchall()

    # Plaintext lives only in the lead envelope; cache per lead for the
    # O(rows-in-scope) HMAC the record prices in honestly.
    phone_by_lead: dict[str, str | None] = {}

    def phone_for(lead_id: Any) -> str | None:
        if caller_secret is None:
            return None
        if not isinstance(lead_id, str):
            return None
        if lead_id not in phone_by_lead:
            phone: str | None = None
            for raw in raw_rows:
                if raw["lead_id"] == lead_id and raw["lead_envelope"]:
                    try:
                        decoded = store.decode_envelope(raw["lead_envelope"])
                    except Exception:
                        decoded = {}
                    consumer = decoded.get("lcp", {}).get("payload", {}).get("consumer")
                    if isinstance(consumer, dict) and isinstance(
                        consumer.get("phone"), str
                    ):
                        phone = consumer["phone"]
                    break
            phone_by_lead[lead_id] = phone
        return phone_by_lead[lead_id]

    hash_filter = filters.get("caller_phone_hash")
    cursor = filters.get("cursor")
    limit = filters["limit"]
    kept: list[dict[str, Any]] = []
    for raw in raw_rows:
        message_type_value = raw["message_type"]
        sender = raw["sender_id"]
        receiver = raw["receiver_id"]
        direction = raw["direction"]
        if message_type_value == "event" and raw["row_envelope"]:
            try:
                envelope = store.decode_envelope(raw["row_envelope"])
            except Exception:
                envelope = {}
            message = envelope.get("lcp", {}).get("message", {})
            sender = message.get("sender_id")
            receiver = message.get("receiver_id")
            direction = "received" if receiver == platform_id else "sent"

        # Authorization scope matrix: counterparty equality inside every
        # non-operator scope; operators see the tenant, never more.
        if not is_operator and caller_id not in (sender, receiver):
            continue

        phone = phone_for(raw["lead_id"])
        if hash_filter is not None:
            # A non-pair caller's digest matches nothing; an erased lead has
            # no preimage, so the filter cannot resurrect it.
            if phone is None or caller_secret is None:
                continue
            if not hmac_equal_digest(caller_secret, phone, hash_filter):
                continue

        row: dict[str, Any] = {
            "request_id": raw["request_id"],
            "message_type": message_type_value,
            "direction": direction,
            "status": raw["status"],
            "created_at": raw["created_at"],
        }
        # Rule 6: nonconforming stored ids are omitted, never coerced.
        if lead_id_pinned(raw["lead_id"]):
            row["lead_id"] = raw["lead_id"]
        for member, value in (
            ("sender_id", sender),
            ("receiver_id", receiver),
            ("vertical", raw["vertical"]),
            ("expires_at", raw["expires_at"]),
            ("updated_at", raw["updated_at"]),
        ):
            if value is not None:
                row[member] = value
        if isinstance(raw["test"], (int, bool)):
            row["test"] = bool(raw["test"])

        if raw["price_cents"] is not None:
            own_bid = message_type_value == "bid" and sender == caller_id
            if is_operator or own_bid:
                row["price_cents"] = int(raw["price_cents"])
                if isinstance(raw["currency"], str) and CURRENCY_PATTERN.fullmatch(
                    raw["currency"]
                ):
                    row["currency"] = raw["currency"]

        if caller_secret is not None and phone is not None:
            # Omitted for an operator unless the operator credential is the
            # row's counterparty pair (the matrix's field restriction).
            if is_operator and caller_id not in (sender, receiver):
                pass
            else:
                import hmac as _hmac
                import hashlib as _hashlib

                row["caller_phone_hash"] = _hmac.new(
                    caller_secret.encode("utf-8"),
                    phone.encode("utf-8"),
                    _hashlib.sha256,
                ).hexdigest()

        violations = history_row_violations(row)
        if violations:
            # Zero-tolerance serialization check (rollout §3): a violation
            # means a code path tried to leak a field; refuse to serve.
            from .router import RequestError

            raise RequestError(
                "history row failed the closed-shape guard", "LCP-500", 500
            )
        kept.append(row)

    total = len(kept)
    if cursor is not None:
        cursor_timestamp, cursor_request = cursor
        kept = [
            row
            for row in kept
            if (row["created_at"], row["request_id"])
            > (cursor_timestamp, cursor_request)
        ]
    page = kept[:limit]
    next_cursor = None
    if len(kept) > len(page):
        last = page[-1]
        next_cursor = encode_cursor(last["created_at"], last["request_id"])
    return page, total, next_cursor


def hmac_equal_digest(secret: str, phone: str, digest: str) -> bool:
    """Membership test the record's filter rule: caller digest vs held plaintext."""
    return pair_phone_hash(secret, phone) == digest


HISTORY_ROUTE = "/v1/lcp/history/requests"


def serve(
    platform: Any,
    query: str,
    *,
    sender_id: str,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Dispatch-time handler for ``GET /v1/lcp/history/requests``.

    Consumed by the route without adding any second scope filter; the row
    set is already decided by ``history_rows``.
    """
    from .auth import header as _header

    filters = parse_history_params(query)
    caller_tenant = platform.auth.tenant_for(sender_id)
    is_operator = platform.auth.has_scope(sender_id, "platform:admin")
    caller_secret = platform.auth.secret_for(sender_id)

    rows, total, next_cursor = history_rows(
        platform.store,
        platform_id=platform.config.platform_id,
        caller_id=sender_id,
        caller_tenant=caller_tenant,
        is_operator=is_operator,
        caller_secret=caller_secret,
        filters=filters,
    )

    # Audit-before-serve: log the query itself with filter NAMES, the result
    # count, and the correlating X-LCP-Request-Id (folded in by insert_audit).
    # MUST NOT log caller_phone_hash values or returned rows.
    platform.store.insert_audit(
        tenant_id=caller_tenant or platform.config.routing_tenant_id,
        actor_id=sender_id,
        action="history.query",
        resource_type="history",
        resource_id=sender_id,
        metadata={
            "filters": sorted(name for name in filters if name != "limit"),
            "result_count": total,
        },
    )

    response_headers = {
        "X-LCP-Result-Count": str(len(rows)),
        "X-LCP-Total-Results": str(total),
    }
    if next_cursor is not None:
        pairs = [
            (name, value)
            for name, value in parse_qsl(query, keep_blank_values=True)
            if name != "cursor"
        ]
        pairs.append(("cursor", next_cursor))
        path = f"{HISTORY_ROUTE}?{urlencode(pairs)}"
        host = _header(headers or {}, "Host")
        url = f"https://{host}{path}" if host else path
        response_headers["Link"] = f'<{url}>; rel="next"'

    return {"requests": rows}, response_headers

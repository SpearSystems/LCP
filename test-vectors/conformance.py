#!/usr/bin/env python3
"""LCP Conformance Runner — validates examples and test vectors against schemas.

Implements three conformance tiers:
  L1: envelope + required fields + idempotency + ack + valid timestamps
  L2: full lifecycle + ping/post PII split (strict allowlist) + compliance
  L3: full + dedup + agent binding + status transitions + consent evidence

The runner also enforces the ping_safe rule: any ping whose attributes contain
a field tagged ping_safe: false in the vertical schema is rejected (LCP-008).

Usage:
  python3 conformance.py              # run all tiers
  python3 conformance.py --tier L1    # run only L1
  python3 conformance.py --tier L2
  python3 conformance.py --tier L3
  python3 conformance.py --verbose    # show per-vector details
"""

import argparse
from datetime import datetime
import json
import os
import re
import sys
from uuid import UUID
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import jsonschema
    from jsonschema import Draft202012Validator, FormatChecker
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012
except ImportError:
    print("ERROR: jsonschema + referencing packages required.")
    print("Install with: pip install jsonschema referencing")
    sys.exit(1)

# --- Paths -------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
VERTICALS_DIR = REPO_ROOT / "verticals"
EXAMPLES_DIR = REPO_ROOT / "examples"
VECTORS_DIR = REPO_ROOT / "test-vectors"

# --- Schema loading ----------------------------------------------------------


def load_json(path: Path) -> Any:
    with open(path) as f:
        return json.load(f)


def load_schema(name: str) -> dict:
    return load_json(SCHEMAS_DIR / f"{name}.json")


def load_vertical(name: str) -> dict:
    return load_json(VERTICALS_DIR / f"{name}.json")


# --- Referencing registry (resolves $ref: "core.json#/$defs/...") ------------

_core_schema = load_schema("core")
_core_resource = Resource(contents=_core_schema, specification=DRAFT202012)

# Register core.json under both its $id and bare filename for $ref resolution
_registry = (
    Registry()
    .with_resource(uri="https://lcp.dev/schemas/core.json", resource=_core_resource)
    .with_resource(uri="core.json", resource=_core_resource)
)


_format_checker = FormatChecker()


def make_validator(schema: dict) -> Draft202012Validator:
    """Create a validator that resolves refs and asserts declared formats."""
    return Draft202012Validator(
        schema,
        registry=_registry,
        format_checker=_format_checker,
    )


# --- Message schema registry -------------------------------------------------

_validators: Dict[str, Draft202012Validator] = {}
for _msg_type in ["lead", "call", "ping", "post", "ack", "event", "bid"]:
    _validators[_msg_type] = make_validator(load_schema(_msg_type))

_envelope_validator = make_validator(load_schema("envelope"))
_vertical_schemas: Dict[str, dict] = {
    path.stem: load_json(path) for path in VERTICALS_DIR.glob("*.json")
}
_vertical_validators: Dict[str, Draft202012Validator] = {
    name: make_validator(schema) for name, schema in _vertical_schemas.items()
}

# --- Ping-safe enforcement ---------------------------------------------------


def get_ping_safe_fields(vertical_name: str) -> Tuple[Set[str], Set[str]]:
    """Return top-level (safe_fields, unsafe_fields) from a loaded schema."""
    vschema = _vertical_schemas.get(vertical_name, {})
    props = vschema.get("properties", {})
    safe: Set[str] = set()
    unsafe: Set[str] = set()
    for name, prop in props.items():
        if name in ("vertical", "schema_version"):
            continue
        if prop.get("ping_safe") is True:
            safe.add(name)
        elif prop.get("ping_safe") is False:
            unsafe.add(name)
    return safe, unsafe


def _nested_ping_safe_errors(value: Any, schema: dict, path: str) -> List[str]:
    """Recursively enforce ping_safe tags, including nested objects."""
    if not isinstance(value, dict):
        return []
    violations: List[str] = []
    properties = schema.get("properties", {})
    for field_name, field_value in value.items():
        if field_name in ("vertical", "schema_version") and path == "attributes":
            continue
        definition = properties.get(field_name)
        field_path = f"{path}.{field_name}"
        if not isinstance(definition, dict) or definition.get("ping_safe") is not True:
            violations.append(
                f"PII_IN_PING: {field_path} is not tagged ping_safe: true (LCP-008)"
            )
            continue
        if isinstance(definition.get("properties"), dict):
            violations.extend(
                _nested_ping_safe_errors(field_value, definition, field_path)
            )
    return violations


def check_ping_safe(payload: dict) -> List[str]:
    """Check that ping attributes only contain recursively ping-safe fields."""
    attrs = payload.get("attributes", {})
    vertical = payload.get("vertical")
    if not isinstance(vertical, str) or not isinstance(attrs, dict):
        return []
    schema = _vertical_schemas.get(vertical)
    if schema is None:
        return [
            f"PII_IN_PING: vertical schema '{vertical}' not found — cannot validate ping_safe"
        ]
    return _nested_ping_safe_errors(attrs, schema, "attributes")


def check_compliance_flags(payload: dict) -> List[str]:
    """SPEC §4: ping compliance_flags are presence indicators only — never
    tokens or evidence. The object stays open (§6: new flags must pass
    without a schema change), so schema closure cannot catch a planted
    bearer token; every flag value MUST be a boolean."""
    flags = payload.get("compliance_flags")
    if not isinstance(flags, dict):
        return []
    return [
        f"PII_IN_PING: compliance_flags.{name} is not a boolean presence indicator — tokens/evidence forbidden (LCP-008)"
        for name, value in flags.items()
        if not isinstance(value, bool)
    ]


# --- Status transition graph -------------------------------------------------

# Terminal states (no outgoing transitions)
TERMINAL = {"CONVERTED", "REFUNDED", "REJECTED", "ARCHIVED", "EXPIRED", "DUPLICATE"}

# Legal transitions
LEGAL_TRANSITIONS: Dict[str, Set[str]] = {
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


def is_valid_transition(from_status: str, to_status: str) -> bool:
    return to_status in LEGAL_TRANSITIONS.get(from_status, set())


# --- Validation engine -------------------------------------------------------


class TestResult:
    def __init__(
        self,
        vector_id: str,
        name: str,
        passed: bool,
        errors: Optional[List[str]] = None,
    ):
        self.vector_id = vector_id
        self.name = name
        self.passed = passed
        self.errors: List[str] = errors or []

    def __repr__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"{status} {self.vector_id} {self.name}"


def _format_path(path: Any) -> str:
    parts = [str(p) for p in path]
    return "/".join(parts) if parts else "(root)"


def validate_message(envelope: dict) -> List[str]:
    """Validate a full LCP envelope + payload. Returns list of error strings."""
    errors: List[str] = []

    # 1. Validate envelope structure and the formats whose semantics must be
    # identical across language runtimes.
    for err in _envelope_validator.iter_errors(envelope):
        errors.append(f"envelope: {err.message} at {_format_path(err.path)}")
    message = envelope.get("lcp", {}).get("message", {})
    message_id = message.get("id")
    if isinstance(message_id, str):
        try:
            parsed_id = UUID(message_id)
            if parsed_id.version != 4:
                errors.append(
                    "envelope: message.id must be a UUID v4 at lcp/message/id"
                )
        except ValueError:
            errors.append("envelope: message.id must be a UUID at lcp/message/id")
    timestamp = message.get("timestamp")
    if isinstance(timestamp, str):
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed_timestamp.tzinfo is None or "T" not in timestamp:
                raise ValueError
        except ValueError:
            errors.append(
                "envelope: message.timestamp must be an RFC3339 date-time at lcp/message/timestamp"
            )

    if errors:
        return errors  # Can't validate payload if envelope is broken

    # 2. Extract message type and validate payload against the right schema
    msg_type = envelope.get("lcp", {}).get("message", {}).get("type")
    payload = envelope.get("lcp", {}).get("payload", {})

    if msg_type in _validators:
        for err in _validators[msg_type].iter_errors(payload):
            errors.append(f"{msg_type}: {err.message} at {_format_path(err.path)}")

    # 3. Validate the selected vertical for every message carrying attributes.
    if msg_type in {"lead", "call", "post", "ping"}:
        attributes = payload.get("attributes")
        vertical = (
            payload.get("vertical")
            if msg_type == "ping"
            else (attributes.get("vertical") if isinstance(attributes, dict) else None)
        )
        if isinstance(vertical, str) and isinstance(attributes, dict):
            vertical_validator = _vertical_validators.get(vertical)
            if vertical_validator is None:
                errors.append(f"vertical: unknown vertical schema '{vertical}'")
            else:
                vertical_attributes = dict(attributes)
                if msg_type == "ping":
                    version_definition = vertical_validator.schema.get(
                        "properties", {}
                    ).get("schema_version", {})
                    vertical_attributes.setdefault(
                        "vertical",
                        vertical,
                    )
                    vertical_attributes.setdefault(
                        "schema_version",
                        version_definition.get("const", "1.0.0"),
                    )
                errors.extend(
                    f"vertical: {error.message} at {_format_path(error.path)}"
                    for error in vertical_validator.iter_errors(vertical_attributes)
                )
        if msg_type == "ping":
            errors.extend(check_ping_safe(payload))
            errors.extend(check_compliance_flags(payload))

    return errors


# --- Named checks registry ---------------------------------------------------
#
# Each vector's `checks` array names the rules the runner must actually
# exercise for it (SPEC.md §7 tiers; #153 routed observation O3 — the
# labels were decorative because the runner never read the key). A check
# function returns the list of violations of its own rule (empty = rule
# holds). Polarity: expect=pass vectors require zero violations for every
# named check; expect=fail vectors require the named rule to have FIRED
# (schema-dimension checks fire when their dimension carries errors;
# semantic rules fire when they compute violations) so a negative vector
# passes only for the reason it claims.


def _payload_of(envelope: dict) -> dict:
    return envelope.get("lcp", {}).get("payload", {})


def _dim_errors(errors: List[str], prefix: str) -> List[str]:
    return [e for e in errors if e.startswith(f"{prefix}:")]


def _schema_check(prefix: str):
    def check(vector: dict, envelope: dict, errors: List[str]) -> List[str]:
        # The violation list IS the dimension's error list; run_checks applies
        # polarity (pass: must be empty; fail: must be non-empty to fire).
        return _dim_errors(errors, prefix)

    return check


_FULL_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# v1.1 wire fields introduced by LEP-0003; the capability gate (SPEC.md §4/§8)
# forbids sending any of them toward a receiver whose §8 lcp_versions
# advertisement does not parse to major == 1, minor >= 1.
_V11_FIELDS_BY_TYPE = {
    "ping": ["slots_available"],
    "bid": ["slots_claimed"],
    "post": ["contacted_hashes"],
    "event": ["details.contacted_hashes"],
}
_ALL_V11_FIELDS = ["slots_available", "slots_claimed", "contacted_hashes"]


def _advertised_supports_minor(
    advertised: List[str], minor: int
) -> tuple[bool, List[str]]:
    """SPEC.md §8 membership rule: parsed full-semver comparison only."""
    problems: List[str] = []
    supports = False
    for element in advertised:
        match = _FULL_SEMVER_RE.match(str(element))
        if not match:
            problems.append(
                f"CAPABILITY_GATE: lcp_versions element {element!r} is not a "
                "full major.minor.patch semantic version (SPEC.md §8)"
            )
            continue
        major, minor_n, _patch = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if major == 1 and minor_n >= minor:
            supports = True
    return supports, problems


def _v11_fields_present(msg_type: str, payload: dict) -> List[str]:
    found: List[str] = []
    for field in _V11_FIELDS_BY_TYPE.get(msg_type, []):
        if field == "details.contacted_hashes":
            details = payload.get("details")
            if isinstance(details, dict) and "contacted_hashes" in details:
                found.append(field)
        elif field in payload:
            found.append(field)
    return found


def _check_capability_gate(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    advertised = vector.get("receiver_lcp_versions")
    if not isinstance(advertised, list):
        return [
            "CAPABILITY_GATE: vector names capability_gate_slot_fields_v11 without declaring receiver_lcp_versions"
        ]
    payload = _payload_of(envelope)
    msg_type = envelope.get("lcp", {}).get("message", {}).get("type", "")
    present = _v11_fields_present(msg_type, payload)
    supports, problems = _advertised_supports_minor(advertised, 1)
    if present and not supports:
        problems.append(
            f"CAPABILITY_GATE: v1.1 field(s) {', '.join(present)} sent toward a "
            f"receiver advertising {advertised!r} — no parsed 1.x element with minor >= 1 "
            "(SPEC.md §8 membership rule; §4 capability gate; LEP-0003 invalid example D)"
        )
    return problems


def _contact_hash_entries(payload: dict, field: str) -> List[str]:
    value = payload.get(field)
    return value if isinstance(value, list) else []


def _bad_hash_entries(entries: List[str]) -> List[str]:
    return [
        f"digest entry {entry!r} fails the ^[a-f0-9]{{64}}$ per-pair HMAC form (SPEC.md §9)"
        for entry in entries
        if not (isinstance(entry, str) and re.fullmatch(r"[a-f0-9]{64}", entry))
    ]


def _check_contacted_hashes_shape(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    if "contacted_hashes" not in payload:
        return []
    entries = _contact_hash_entries(payload, "contacted_hashes")
    problems = _bad_hash_entries(entries)
    if len(entries) > 100:
        problems.append(
            f"contacted_hashes carries {len(entries)} entries, above the bounded post "
            "direction maxItems 100 (LEP-0003 Deferred questions; bulk sync is not a post)"
        )
    return problems


def _check_contacted_report_shape(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    details = payload.get("details")
    if not isinstance(details, dict) or "contacted_hashes" not in details:
        return []
    entries = _contact_hash_entries(details, "contacted_hashes")
    if entries == []:
        return [
            "an empty CONTACTED report contacted_hashes must not be sent ([] asserts nothing in the report direction; SPEC.md §4)"
        ]
    problems = _bad_hash_entries(entries)
    if len(entries) > 32:
        problems.append(
            f"CONTACTED report carries {len(entries)} entries, above the 32-per-report ingest bound (SPEC.md §4)"
        )
    return problems


def _check_slots_available_min(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    if "slots_available" not in payload:
        return []
    value = payload["slots_available"]
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return []
    return [
        f"slots_available {value!r} violates minimum 1 — zero remaining means stop pinging (SPEC.md §3/§4, LEP-0003 §1)"
    ]


def _check_exclusive_single_slot(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    if (
        payload.get("exclusivity") == "exclusive"
        and "slots_available" in payload
        and payload["slots_available"] != 1
    ):
        return [
            "exclusive lead advertising slots_available "
            f"{payload['slots_available']!r} contradicts the one-slot special case (LEP-0003 invalid example B; schema conditional)"
        ]
    return []


def _check_slots_claimed_single_award(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    if "slots_claimed" not in payload:
        return []
    value = payload["slots_claimed"]
    if value == 1:
        return []
    return [
        f"slots_claimed {value!r} outside the v1.1 single-slot range 1..1 (LEP-0003 §2, #153 T5 deferral)"
    ]


def _check_ping_allowlist_no_pii(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    allowed = set(load_schema("ping").get("properties", {}))
    extra = sorted(k for k in payload if k not in allowed)
    return [
        f"LCP-008 PII_IN_PING: ping payload field {k!r} outside the strict allowlist"
        for k in extra
    ]


def _check_event_open_enum(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    event = payload.get("event")
    if not isinstance(event, str) or not event:
        return ["event payload lacks an event name (open enum, SPEC.md §4/§6)"]
    return _dim_errors(errors, "event")


def _check_event_open_enum_pass_through(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    event = payload.get("event")
    classic = {
        "DELIVERED",
        "ACCEPTED",
        "REJECTED",
        "DISPUTED",
        "REFUNDED",
        "EXPIRED",
        "CONVERTED",
        "ARCHIVED",
    }
    if not isinstance(event, str) or not event:
        return ["event payload lacks an event name"]
    if event in classic:
        # still a valid message; the pass-through property is simply untested here
        return _dim_errors(errors, "event")
    return _dim_errors(errors, "event")


def _check_consumer_required_fields(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    consumer = _payload_of(envelope).get("consumer")
    if not isinstance(consumer, dict):
        return ["payload.consumer missing"]
    has_name = ("full_name" in consumer) or (
        "first_name" in consumer and "last_name" in consumer
    )
    if not has_name:
        return ["payload.consumer carries no resolvable name (first+last or full_name)"]
    return []


def _check_consumer_full_pii(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    consumer = _payload_of(envelope).get("consumer")
    if not isinstance(consumer, dict):
        return ["post payload.consumer missing"]
    if not ({"first_name", "last_name"} <= set(consumer) or "full_name" in consumer):
        return ["post consumer lacks name PII"]
    if not (consumer.get("phone") or consumer.get("email")):
        return ["post consumer lacks contact PII (phone/email)"]
    return []


def _check_compliance_full_evidence(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    compliance = _payload_of(envelope).get("compliance")
    if not isinstance(compliance, dict) or "consent_evidence" not in compliance:
        return [
            "post payload.compliance.consent_evidence missing (SPEC.md §7 L2 compliance records)"
        ]
    return []


def _check_compliance_evidence_array(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    compliance = _payload_of(envelope).get("compliance")
    evidence = (
        compliance.get("consent_evidence") if isinstance(compliance, dict) else None
    )
    if not isinstance(evidence, list):
        return ["compliance.consent_evidence is not an array"]
    return []


def _check_location_required(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    location = _payload_of(envelope).get("location")
    if not isinstance(location, dict) or not location.get("country_code"):
        return ["payload.location.country_code missing"]
    return []


def _check_location_state_or_postal(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    location = _payload_of(envelope).get("location")
    if not isinstance(location, dict):
        return ["payload.location missing"]
    if not (location.get("state_region") or location.get("postal_code")):
        return [
            "location carries neither state_region nor postal_code (US/AU conditional, SPEC.md §3)"
        ]
    return []


def _check_attributes_vertical_version(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    attributes = _payload_of(envelope).get("attributes")
    if not isinstance(attributes, dict):
        return ["payload.attributes missing"]
    if "vertical" not in attributes or "schema_version" not in attributes:
        return ["payload.attributes lacks vertical/schema_version envelope keys"]
    return []


def _check_call_block_required(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    call = _payload_of(envelope).get("call")
    if not isinstance(call, dict):
        return ["payload.call missing"]
    missing = [k for k in ("call_id", "status", "started_at") if k not in call]
    return [f"payload.call missing required {m}" for m in missing]


def _check_otp_closed_object(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    compliance = _payload_of(envelope).get("compliance")
    if not isinstance(compliance, dict):
        return []
    definition = load_schema("core").get("$defs", {}).get("compliance", {})

    def walk(node: Any, schema: dict, path: str) -> List[str]:
        problems: List[str] = []
        if not isinstance(node, dict):
            return problems
        props = schema.get("properties", {})
        for key, value in node.items():
            sub = props.get(key)
            if sub is None:
                problems.append(
                    f"{path}{key!r} is not a defined compliance field — compliance/otp "
                    "are closed objects (planted codes/evidence forbidden, SPEC.md §4)"
                )
                continue
            if isinstance(value, dict):
                problems.extend(walk(value, sub, f"{path}{key}."))
        return problems

    return walk(compliance, definition, "compliance.")


def _check_ping_compliance_flags_presence_only(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    return check_compliance_flags(_payload_of(envelope))


def _check_attestation_present(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    provenance = _payload_of(envelope).get("provenance") or {}
    agent = provenance.get("agent") if isinstance(provenance, dict) else None
    if not isinstance(agent, dict) or not agent.get("attestation"):
        return [
            "provenance.agent.attestation missing (SPEC.md §10 requires a verifiable token)"
        ]
    return []


def _check_agent_provenance(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    provenance = _payload_of(envelope).get("provenance") or {}
    if not isinstance(provenance.get("agent"), dict):
        return ["provenance.agent missing"]
    return []


def _check_no_vendor_fields_in_core(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    attributes = payload.get("attributes") or {}
    vertical = attributes.get("vertical") if isinstance(attributes, dict) else None
    schema = _vertical_schemas.get(vertical or "", {})
    vendor = set(schema.get("properties", {})) - {"vertical", "schema_version"}
    shadow = sorted(k for k in payload if k in vendor)
    return (
        [
            f"top-level payload key {k!r} duplicates a vertical attribute field — vertical data lives in attributes (SPEC.md §12)"
        ]
        if shadow
        else []
    )


def _check_errors_array_multiple(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    ack_errors = _payload_of(envelope).get("errors")
    if not isinstance(ack_errors, list) or len(ack_errors) < 2:
        return ["ack errors array must carry multiple error objects (SPEC.md §4/§5)"]
    return []


def _check_ack_no_errors_when_accepted(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    if payload.get("status") in {"RECEIVED", "VALIDATED", "ACCEPTED"} and payload.get(
        "errors"
    ):
        return ["ack accepted but carries a non-empty errors array"]
    return []


def _check_exclusivity_field(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    if not isinstance(_payload_of(envelope).get("exclusivity"), dict):
        return ["payload.exclusivity missing"]
    return []


def _check_expiry_field(vector: dict, envelope: dict, errors: List[str]) -> List[str]:
    payload = _payload_of(envelope)
    expiry = payload.get("expiry")
    if isinstance(expiry, dict) and (
        expiry.get("expires_at") or expiry.get("ttl_seconds")
    ):
        return []
    if payload.get("expires_at") or payload.get("ttl_seconds"):
        return []
    return [
        "payload carries no expiry (expiry{expires_at,ttl_seconds} or expires_at/ttl_seconds missing)"
    ]


def _check_required_fields(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    message = envelope.get("lcp", {}).get("message", {})
    missing = [
        k
        for k in (
            "id",
            "type",
            "timestamp",
            "sender_id",
            "receiver_id",
            "idempotency_key",
        )
        if k not in message
    ]
    if missing:
        return [f"message header missing required {m}" for m in missing]
    return []


def _check_idempotency_key(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    key = envelope.get("lcp", {}).get("message", {}).get("idempotency_key")
    if not isinstance(key, str) or not key:
        return ["message.idempotency_key missing or empty"]
    return []


def _check_uuid_message_id(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    raw = envelope.get("lcp", {}).get("message", {}).get("id")
    try:
        if UUID(str(raw)).version != 4:
            return [f"message.id {raw!r} is not UUID v4"]
    except ValueError:
        return [f"message.id {raw!r} is not a UUID"]
    return []


_CANON_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _check_iso_timestamp(vector: dict, envelope: dict, errors: List[str]) -> List[str]:
    ts = envelope.get("lcp", {}).get("message", {}).get("timestamp")
    if not isinstance(ts, str) or not _CANON_TS_RE.match(ts):
        return [
            "message.timestamp is not the canonical whole-second UTC form YYYY-MM-DDTHH:MM:SSZ (LEP-0002, SPEC.md §9)"
        ]
    return []


def _check_status_transition_graph(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    # Implemented by the transitions branch of run_vector; registered here so
    # the name resolves and is honored.
    return []


CHECKS = {
    "envelope_schema": _schema_check("envelope"),
    "lead_schema": _schema_check("lead"),
    "call_schema": _schema_check("call"),
    "ping_schema": _schema_check("ping"),
    "post_schema": _schema_check("post"),
    "ack_schema": _schema_check("ack"),
    "bid_schema": _schema_check("bid"),
    "event_schema": _schema_check("event"),
    "required_fields": _check_required_fields,
    "idempotency_key": _check_idempotency_key,
    "uuid_message_id": _check_uuid_message_id,
    "iso_timestamp": _check_iso_timestamp,
    "status_transition_graph": _check_status_transition_graph,
    "ping_allowlist_no_pii": _check_ping_allowlist_no_pii,
    "ping_safe_attributes_only": lambda v, e, errs: check_ping_safe(_payload_of(e)),
    "ping_compliance_flags_presence_only": _check_ping_compliance_flags_presence_only,
    "consumer_required_fields": _check_consumer_required_fields,
    "consumer_full_pii": _check_consumer_full_pii,
    "compliance_full_evidence": _check_compliance_full_evidence,
    "compliance_evidence_array": _check_compliance_evidence_array,
    "location_required": _check_location_required,
    "location_state_or_postal": _check_location_state_or_postal,
    "attributes_vertical_version": _check_attributes_vertical_version,
    "call_block_required": _check_call_block_required,
    "otp_closed_object": _check_otp_closed_object,
    "event_open_enum": _check_event_open_enum,
    "event_open_enum_pass_through": _check_event_open_enum_pass_through,
    "agent_provenance": _check_agent_provenance,
    "attestation_present": _check_attestation_present,
    "no_vendor_fields_in_core": _check_no_vendor_fields_in_core,
    "ack_no_errors_when_accepted": _check_ack_no_errors_when_accepted,
    "errors_array_multiple": _check_errors_array_multiple,
    "exclusivity_field": _check_exclusivity_field,
    "expiry_field": _check_expiry_field,
    # LEP-0003 (v1.1) surface:
    "capability_gate_slot_fields_v11": _check_capability_gate,
    "slots_available_min": _check_slots_available_min,
    "exclusive_single_slot_conditional": _check_exclusive_single_slot,
    "slots_claimed_single_award": _check_slots_claimed_single_award,
    "contacted_hashes_shape": _check_contacted_hashes_shape,
    "contacted_report_shape": _check_contacted_report_shape,
}


def run_checks(
    vector: dict, envelope: dict, errors: List[str]
) -> Tuple[List[str], List[str]]:
    """Resolve every named check against polarity (see registry header).

    Returns (violations, meta_failures). Violations are conformance errors
    contributed by the rules themselves (merged into the vector's error
    list so negative vectors can match their expected error text);
    meta_failures mark a negative vector whose specific named rule did
    NOT fire — those reject the vector outright so a negative can never
    pass by accident via some unrelated error.
    """
    expect = vector.get("expect", "pass")
    violations: List[str] = []
    meta_failures: List[str] = []
    for name in vector.get("checks", []):
        found = CHECKS[name](vector, envelope, errors)
        if expect == "pass":
            violations.extend(f"CHECK {name}: {v}" for v in found)
        elif found:
            violations.extend(f"CHECK {name}: {v}" for v in found)
        else:
            meta_failures.append(
                f"CHECK {name}: vector expects failure from this rule but it did not fire"
            )
    return violations, meta_failures


def run_vector(vector: dict) -> TestResult:
    """Run a single test vector."""
    vid = vector.get("id", "?")
    name = vector.get("name", "?")
    expect = vector.get("expect", "pass")
    checks = vector.get("checks", [])

    # Every named check must have a real implementation in the runner —
    # honoring the `checks` array per #153 (routed observation O3): a label
    # with no implementation fails the vector instead of decorating it.
    unknown = [c for c in checks if c not in CHECKS]
    if unknown:
        return TestResult(
            vid,
            name,
            False,
            [f"CHECK_UNREGISTERED: no implementation for check {c!r}" for c in unknown],
        )

    # Special case: status transition table test
    if "transitions" in vector:
        failures: List[str] = []
        for t in vector["transitions"]:
            from_s = t["from"]

            to_s = t["to"]
            expected_valid = t["valid"]
            actual_valid = is_valid_transition(from_s, to_s)
            if actual_valid != expected_valid:
                failures.append(
                    f"transition {from_s}->{to_s}: expected valid={expected_valid}, got {actual_valid}"
                )
        if failures:
            return TestResult(vid, name, False, failures)
        return TestResult(vid, name, True)

    # Load payload — either from a file reference or inline
    if "file" in vector:
        file_path = EXAMPLES_DIR / vector["file"]
        if not file_path.exists():
            return TestResult(vid, name, False, [f"file not found: {vector['file']}"])
        envelope = load_json(file_path)
    elif "payload" in vector:
        envelope = vector["payload"]
    else:
        return TestResult(vid, name, False, ["no file or payload in vector"])

    errors = validate_message(envelope)
    check_violations, check_meta = run_checks(vector, envelope, errors)
    errors = errors + check_violations
    if check_meta:
        return TestResult(vid, name, False, check_meta)

    if expect == "pass":
        if errors:
            return TestResult(vid, name, False, errors)
        return TestResult(vid, name, True)
    elif expect == "fail":
        if not errors:
            return TestResult(
                vid,
                name,
                False,
                ["expected failure but message validated successfully"],
            )
        expected_text = vector.get("error_contains")
        if expected_text:
            expected_values = (
                [expected_text] if isinstance(expected_text, str) else expected_text
            )
            combined = " ".join(errors)
            missing = [value for value in expected_values if value not in combined]
            if missing:
                return TestResult(
                    vid,
                    name,
                    False,
                    [f"expected error text not found: {value}" for value in missing]
                    + errors,
                )
        return TestResult(vid, name, True)
    else:
        return TestResult(vid, name, False, [f"unknown expect value: {expect}"])


def run_tier(tier_file: str) -> List[TestResult]:
    """Run all vectors in a tier file."""
    path = VECTORS_DIR / tier_file
    if not path.exists():
        print(f"  WARNING: {tier_file} not found, skipping")
        return []
    suite = load_json(path)
    results: List[TestResult] = []
    for vector in suite.get("vectors", []):
        results.append(run_vector(vector))
    return results


# --- Main --------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="LCP Conformance Runner")
    parser.add_argument("--tier", choices=["L1", "L2", "L3", "all"], default="all")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    tiers = {
        "L1": "l1-envelope.json",
        "L2": "l2-lifecycle.json",
        "L3": "l3-advanced.json",
    }

    if args.tier != "all":
        tiers = {args.tier: tiers[args.tier]}

    total_pass = 0
    total_fail = 0

    for tier_name, tier_file in sorted(tiers.items()):
        print(f"\n{'=' * 60}")
        print(f"  {tier_name} - {tier_file}")
        print(f"{'=' * 60}")

        results = run_tier(tier_file)
        tier_pass = sum(1 for r in results if r.passed)
        tier_fail = sum(1 for r in results if not r.passed)

        for r in results:
            if r.passed:
                if args.verbose:
                    print(f"  PASS  {r.vector_id} {r.name}")
            else:
                print(f"  FAIL  {r.vector_id} {r.name}")
                for err in r.errors:
                    print(f"        -> {err}")

        print(f"\n  {tier_name}: {tier_pass} passed, {tier_fail} failed")
        total_pass += tier_pass
        total_fail += tier_fail

    print(f"\n{'=' * 60}")
    print(f"  TOTAL: {total_pass} passed, {total_fail} failed")
    print(f"{'=' * 60}")

    if total_fail > 0:
        print(f"\n  {total_fail} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"\n  ALL {total_pass} tests PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()

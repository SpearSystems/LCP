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
  python3 conformance.py --tier NEG
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
OPENAPI_PATH = REPO_ROOT / "api" / "lcp-openapi.yaml"

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
for _msg_type in [
    "lead",
    "call",
    "ping",
    "post",
    "ack",
    "event",
    "bid",
    "batch",
    "quote",
    "counter",
    "subscription",
]:
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
    # identical across language runtimes. `batch` is part of the closed
    # `message.type` enum and schemas/batch.json validates its payload, so the
    # wrapper is checked like any other message; `batch_shape` adds the
    # per-item axis the schema deliberately leaves to the intake engine.
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


# --- LEP-0003 W6-07 (SPEC.md §9): email/form-relay entry emission ------------

# The provenance keys that prove a client capture exists. An email/form relay
# observes no client, so a platform-recorded entry carries none of them;
# mirrors ``lcp_platform.validation.CLIENT_CAPTURE_PROVENANCE_KEYS``.
_EMAIL_FORM_CLIENT_CAPTURE_KEYS = ("ip_address", "ip_hash", "user_agent_hash")


def _check_email_form_entry_shape(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    """LEP-0003 W6-07: a platform-recorded email/form entry is declared,
    consent-consistent, captureless, and emitted only where advertised.

    The vector states the receiver's advertised features (`receiver_features`)
    the way the capability vectors state `receiver_lcp_versions`: an entry with
    ``source_type: "email_form"`` directed at a receiver that does not
    advertise ``features.email_form_entry`` is the gate-off case, where the
    receiver MUST refuse the lead instead of emitting the entry (fail closed,
    never a fabricated ``source_type``).
    """
    provenance = _payload_of(envelope).get("provenance")
    if not isinstance(provenance, dict):
        return []
    if provenance.get("source_type") != "email_form":
        return []
    problems: List[str] = []
    captured = sorted(k for k in _EMAIL_FORM_CLIENT_CAPTURE_KEYS if k in provenance)
    if captured:
        problems.append(
            "EMAIL_FORM_ENTRY: captureless relay entry carries client-observed "
            + ", ".join(f"provenance.{k}" for k in captured)
            + " — an email/form relay observed no client, so the entry MUST NOT "
            "fabricate one (SPEC.md §9)"
        )
    if not provenance.get("acquisition_method"):
        problems.append(
            "EMAIL_FORM_ENTRY: provenance.acquisition_method is required on a "
            "platform-recorded entry (absent means nothing was recorded)"
        )
    advertised = vector.get("receiver_features")
    if not isinstance(advertised, list):
        problems.append(
            "EMAIL_FORM_ENTRY: vector names email_form_entry_shape without "
            "declaring receiver_features"
        )
    elif "email_form_entry" not in advertised:
        problems.append(
            "EMAIL_FORM_ENTRY: platform-recorded email_form entry emitted toward a "
            f"receiver advertising features={advertised!r} — the receiver does not "
            "advertise features.email_form_entry, so the lead MUST be refused rather "
            "than recorded under a source_type it did not observe (LEP-0003 W6-07 gate)"
        )
    return problems


# --- v1.2 surfaces (D2 PROCESSED, D7 multi-currency) -------------------------

_FX_RATE_RE = re.compile(r"^[0-9]+(\.[0-9]{6,})?$")
# docs/DECISIONS-v1.2.md D7: LCP_FX_MAX_AGE_SECONDS defaults to 300.
_FX_MAX_AGE_SECONDS = 300


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not _CANON_TS_RE.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fx_significant_digits(value: str) -> int:
    return len(value.replace(".", "").lstrip("0"))


def _check_ack_processed_post_only(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    """D2: PROCESSED is valid only on an ack for a post delivery, by the
    buyer that received that post. The delivery being acknowledged is not
    carried on the wire, so the vector states it
    (`acknowledged_message_type`), the same way capability vectors state
    `receiver_lcp_versions`."""
    payload = _payload_of(envelope)
    if payload.get("status") != "PROCESSED":
        return []
    acknowledged = vector.get("acknowledged_message_type")
    if acknowledged == "post":
        return []
    return [
        f"PROCESSED ack must acknowledge a post delivery, not {acknowledged!r} "
        "(D2: PROCESSED is valid only on an ack for a post)"
    ]


def _check_pricing_fx_comparability(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    """D7: a cross-currency offer is comparable only when its rate is
    present, well-formed, non-stale, and consistently sourced. Missing,
    stale, malformed, or mixed-source rates make the offer non-comparable;
    they are never silently coerced."""
    payload = _payload_of(envelope)
    pricing = payload.get("pricing")
    if not isinstance(pricing, dict):
        return []
    native_currency = payload.get("currency")
    settlement_currency = pricing.get("currency")
    if not (isinstance(native_currency, str) and isinstance(settlement_currency, str)):
        return []
    if settlement_currency == native_currency:
        return []

    problems: List[str] = []
    rate = pricing.get("fx_rate")
    if rate is None:
        problems.append(
            "missing fx_rate: cross-currency offer is non-comparable without a rate (D7)"
        )
    elif (
        not isinstance(rate, str)
        or not _FX_RATE_RE.match(rate)
        or _fx_significant_digits(rate) < 6
    ):
        problems.append(
            f"malformed fx_rate {rate!r}: decimal string with at least 6 "
            "significant digits and no exponent (D7)"
        )

    source = pricing.get("fx_source")
    comparison_source = vector.get("comparison_fx_source")
    if source is None:
        problems.append("missing fx_source: a comparable rate names its source (D7)")
    elif comparison_source is not None and source != comparison_source:
        problems.append(
            f"mixed fx_source: rate sourced from {source!r} cannot be compared "
            f"against {comparison_source!r} rates (D7)"
        )

    reference = _parse_utc_timestamp(
        payload.get("delivered_at")
    ) or _parse_utc_timestamp(
        envelope.get("lcp", {}).get("message", {}).get("timestamp")
    )
    stamp = _parse_utc_timestamp(pricing.get("fx_timestamp"))
    if stamp is None:
        problems.append(
            "missing or malformed fx_timestamp: a comparable rate carries an "
            "RFC 3339 UTC timestamp (D7)"
        )
    elif reference is not None:
        age = (reference - stamp).total_seconds()
        if age > _FX_MAX_AGE_SECONDS or age < 0:
            problems.append(
                f"stale fx_timestamp: rate age {int(age)}s is outside "
                f"LCP_FX_MAX_AGE_SECONDS={_FX_MAX_AGE_SECONDS} (D7)"
            )
    expires = _parse_utc_timestamp(pricing.get("fx_expires_at"))
    if expires is not None and reference is not None and expires <= reference:
        problems.append("stale fx_expires_at: rate expired before delivery (D7)")
    return problems


def _check_ping_allowlist_no_pii(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _payload_of(envelope)
    allowed = set(load_schema("ping").get("properties", {}))
    extra = sorted(k for k in payload if k not in allowed)
    problems = [
        f"LCP-008 PII_IN_PING: ping payload field {k!r} outside the strict allowlist"
        for k in extra
    ]
    # v1.2 D6/D9: ping admits an extension namespace only when its registration
    # declares ping safety. Kept in lockstep with
    # lcp_platform.validation.EXTENSION_REGISTRY (governance/EXTENSION-REGISTRY.md).
    extensions = payload.get("extensions")
    if isinstance(extensions, dict):
        problems.extend(
            "LCP-008 PII_IN_PING: ping payload extension namespace "
            f"{'extensions.' + ns!r} is not ping-safe"
            for ns in sorted(extensions)
            if ns not in PING_SAFE_EXTENSIONS
        )
    return problems


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


# --- OpenAPI <-> JSON Schema contract checks ---------------------------------
#
# LEP-0005 (request history) is described twice: the OpenAPI operation
# GET /history/requests declares the closed filter allowlist, and
# schemas/history-request-row.json declares the row the surface emits. A
# filter's accepted shapes MUST be the row field's accepted shapes, or one
# contract admits what the other rejects. The JSON Schema is normative.

HISTORY_FILTER_TO_ROW_FIELD = {
    "request_id": "request_id",
    "lead_id": "lead_id",
    "message_type": "message_type",
    "status": "status",
    "vertical": "vertical",
    "caller_phone_hash": "caller_phone_hash",
    # `from`/`to` bound the canonical stored created_at column, so they carry
    # its timestamp pattern rather than a field with their own name.
    "from": "created_at",
    "to": "created_at",
}
# Paging controls, not field filters: no row counterpart is expected.
HISTORY_PAGING_PARAMS = {"limit", "cursor"}


def _yaml_scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        try:
            return json.loads(raw)
        except ValueError:
            return raw[1:-1]
    return raw


def parse_history_query_params(path: Path) -> Dict[str, Optional[str]]:
    """Map GET /history/requests query params to their declared pattern.

    A targeted indentation walk rather than a YAML dependency: PyYAML is not
    pinned in requirements-ci.txt. Returns {name: pattern-or-None}; a name is
    present only when the endpoint declares it.
    """
    params: Dict[str, Optional[str]] = {}
    in_operation = False
    in_params = False
    current: Optional[str] = None
    section: Optional[str] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 2:
            if stripped == "/history/requests:":
                in_operation = True
                continue
            if in_operation:
                break
            continue
        if not in_operation:
            continue
        if indent == 4:
            if stripped == "get:":
                continue
            break  # only the GET carries the filter allowlist
        if indent == 6 and in_params and stripped.startswith("- name:"):
            current = _yaml_scalar(stripped[len("- name:") :])
            params[current] = None
            section = None
            continue
        if indent == 6:
            if stripped == "parameters:":
                in_params = True
                continue
            if in_params:
                break  # next operation key (responses:, ...)
        if not in_params:
            continue
        if current is None:
            continue
        if indent == 8:
            section = stripped.split(":", 1)[0].strip()
            continue
        if indent == 10 and section == "schema" and stripped.startswith("pattern:"):
            params[current] = _yaml_scalar(stripped[len("pattern:") :])
    return params


def _check_history_filter_pattern_equivalence(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    """Shared history filters carry the same pattern in both contracts."""
    try:
        openapi_params = parse_history_query_params(OPENAPI_PATH)
    except OSError as exc:
        return [f"cannot read {OPENAPI_PATH.relative_to(REPO_ROOT)}: {exc}"]
    row_props = load_schema("history-request-row").get("properties", {})
    violations: List[str] = []

    if not openapi_params:
        violations.append(
            "GET /history/requests declares no query parameters in "
            f"{OPENAPI_PATH.relative_to(REPO_ROOT)}"
        )
    for name in openapi_params:
        if name in HISTORY_PAGING_PARAMS:
            continue
        if name not in HISTORY_FILTER_TO_ROW_FIELD:
            violations.append(
                f"parameter {name!r} exists in the OpenAPI history filters but "
                "has no JSON Schema history-row counterpart"
            )
    for name, field in HISTORY_FILTER_TO_ROW_FIELD.items():
        if name not in openapi_params:
            violations.append(
                f"parameter {name!r} exists in the JSON Schema history contract "
                "but not in the OpenAPI history filters"
            )
            continue
        if field not in row_props:
            violations.append(
                f"parameter {name!r} maps to row field {field!r}, which the JSON "
                "Schema history contract does not declare"
            )
            continue
        api_pattern = openapi_params[name]
        schema_pattern = row_props[field].get("pattern")
        if api_pattern != schema_pattern:
            violations.append(
                f"parameter {name!r} pattern mismatch: OpenAPI {api_pattern!r} "
                f"!= JSON Schema {schema_pattern!r} "
                f"(history-request-row.{field})"
            )
    return violations


# v1.2 D6/D9: namespaces whose registration declares ping safety. Keep in
# lockstep with lcp_platform.validation.EXTENSION_REGISTRY and
# governance/EXTENSION-REGISTRY.md; `lcp.platform.assessment` is the only one.
PING_SAFE_EXTENSIONS = frozenset({"lcp.platform.assessment"})

# --- LEP-0001 (W2) batch submission ------------------------------------------

# W2-01 fixes both caps at acceptance: 100 items and a 1 MiB request body.
# Either excess is a whole-request refusal, never a per-item rejection.
BATCH_MAX_ITEMS = 100
BATCH_MAX_BODY_BYTES = 1024 * 1024
# LEP-0001 F5: the closed per-item receipt vocabulary. This is NOT ack.status.
BATCH_RECEIPT_STATUSES = ("accepted", "duplicate", "rejected", "error")


def _check_batch_shape(vector: dict, envelope: dict, errors: List[str]) -> List[str]:
    """LEP-0001 W2-01: a batch is ``request_id`` + 1..100 items.

    Each item is ``{item_id, idempotency_key, payload}`` where ``payload`` is a
    **lead payload** (the ``lead.json`` root object), never an envelope. The
    vector declares the receipts it expects (``expected_receipts``, one per
    item, in input order): every item carries its own ``item_id``, and an item
    expected ``rejected``/``error`` MUST fail the lead schema while an item
    expected ``accepted``/``duplicate`` MUST pass it, so "one bad item must not
    fail the batch" is never a licence to carry garbage.
    """
    message = envelope.get("lcp", {}).get("message", {})
    if message.get("type") != "batch":
        return []
    payload = _payload_of(envelope)
    items = payload.get("items")
    if not isinstance(items, list):
        return ["lcp.payload.items must be an array of batch items"]

    problems: List[str] = []
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        problems.append(
            "lcp.payload.request_id must be a non-empty string of at most 64 characters"
        )
    if not 1 <= len(items) <= BATCH_MAX_ITEMS:
        problems.append(
            f"batch carries {len(items)} items; the request cap is "
            f"1..{BATCH_MAX_ITEMS} (LEP-0001 W2-01)"
        )
    body_bytes = len(json.dumps(envelope, separators=(",", ":")).encode("utf-8"))
    if body_bytes > BATCH_MAX_BODY_BYTES:
        problems.append(
            f"batch body is {body_bytes} bytes, above the "
            f"{BATCH_MAX_BODY_BYTES}-byte request cap (LEP-0001 W2-01)"
        )

    receipts = vector.get("expected_receipts")
    receipts = receipts if isinstance(receipts, list) else None
    if receipts is not None and len(receipts) != len(items):
        problems.append(
            f"expected_receipts declares {len(receipts)} receipts for "
            f"{len(items)} items; a batch answers one receipt per item"
        )

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"item {index}: every batch item is an object")
            continue
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not 1 <= len(item_id) <= 64:
            problems.append(
                f"item {index}: item_id must be a non-empty string of at most "
                "64 characters (echoed in the item's receipt)"
            )
            item_id = None
        key = item.get("idempotency_key")
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            problems.append(
                f"item {index}: idempotency_key must be a non-empty string of "
                "at most 128 characters (per-item idempotency)"
            )
        item_payload = item.get("payload")
        if not isinstance(item_payload, dict):
            problems.append(
                f"item {index}: payload is a lead payload object (the "
                "lead.json root object), never an envelope"
            )
            continue
        lead_id = item_payload.get("lead_id")
        if not isinstance(lead_id, str) or not lead_id:
            problems.append(f"item {index}: lead payload carries lead_id")
            continue
        if receipts is None or index >= len(receipts):
            continue
        receipt = receipts[index]
        if not isinstance(receipt, dict):
            problems.append(f"item {index}: expected_receipts entry must be an object")
            continue
        if receipt.get("item_id") != item_id:
            problems.append(
                f"item {index}: receipt item_id {receipt.get('item_id')!r} does "
                f"not match the item's {item_id!r}"
            )
        status = receipt.get("status")
        if status not in BATCH_RECEIPT_STATUSES:
            problems.append(
                f"item {index}: receipt status {status!r} is outside the closed "
                f"vocabulary {list(BATCH_RECEIPT_STATUSES)} (LEP-0001 F5)"
            )
            continue
        if status in {"rejected", "error"} and not receipt.get("error"):
            problems.append(f"item {index}: a {status} receipt carries an error object")
        if status in {"accepted", "duplicate"} and "error" in receipt:
            problems.append(f"item {index}: a {status} receipt carries no error")
        if status == "accepted" and receipt.get("lead_id") != lead_id:
            problems.append(
                f"item {index}: receipt lead_id {receipt.get('lead_id')!r} does "
                f"not match the item's {lead_id!r}"
            )
        item_errors = list(_validators["lead"].iter_errors(item_payload))
        if status in {"accepted", "duplicate"} and item_errors:
            problems.append(
                f"item {index}: expected {status} but the lead payload fails the "
                f"schema: {item_errors[0].message}"
            )
        elif status in {"rejected", "error"} and not item_errors:
            problems.append(
                f"item {index}: expected {status} but the lead payload validates"
            )
    return problems


# --- LEP-0009 negotiation (v1.2, D8) ----------------------------------------
#
# The negotiation wire surface is emitted only toward a peer that advertises
# both a parsed 1.x semver element with minor >= 2 AND the `negotiation`
# feature key. Vectors state that advertisement (`receiver_lcp_versions`,
# `receiver_features`) the same way the v1.1 capability vectors do, plus the
# state facts a static payload cannot carry (`prior_sender_price`,
# `answered_price_cents`, `negotiation_state`, `negotiation_buyer_id`,
# `ping_expires_at`). Each named check computes the rule the record states.

_NEGOTIATION_TYPES = {"quote", "counter"}


def _check_negotiation_capability(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    message = envelope.get("lcp", {}).get("message", {})
    if message.get("type") not in _NEGOTIATION_TYPES:
        return []
    versions = vector.get("receiver_lcp_versions")
    features = vector.get("receiver_features")
    if not isinstance(versions, list):
        return [
            "CAPABILITY_GATE: vector names the negotiation capability check "
            "without declaring receiver_lcp_versions"
        ]
    supports, problems = _advertised_supports_minor(versions, 2)
    if not supports:
        problems.append(
            f"CAPABILITY_GATE: receiver advertises {versions!r} — no parsed 1.x "
            "element with minor >= 2; quote/counter MUST NOT be emitted (LEP-0009 C2)"
        )
    if not isinstance(features, dict) or "negotiation" not in features:
        problems.append(
            "CAPABILITY_GATE: receiver features do not carry `negotiation`; the "
            "feature key is the second, independent switch (LEP-0009 C2/C3)"
        )
    return problems


def _negotiation_payload(envelope: dict) -> dict:
    payload = _payload_of(envelope)
    return payload if isinstance(payload, dict) else {}


def _check_negotiation_floor(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _negotiation_payload(envelope)
    price = payload.get("price_cents")
    floor = payload.get("floor_price_cents")
    if not isinstance(price, int) or not isinstance(floor, int):
        return []
    if price < floor:
        return [
            f"below_floor_price: payload.price_cents {price} < floor_price_cents "
            f"{floor}; refused, never clamped (LEP-0009 C6)"
        ]
    return []


def _check_negotiation_rounds(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _negotiation_payload(envelope)
    problems: List[str] = []
    max_rounds = payload.get("max_rounds")
    if isinstance(max_rounds, int) and max_rounds > 5:
        problems.append(
            f"round_limit_exceeded: payload.max_rounds {max_rounds} is above the "
            "hard cap of 5 (LEP-0009 C5; LCP-100 at quote intake)"
        )
    if payload.get("decision") == "counter" and payload.get("rounds_remaining") == 0:
        problems.append(
            "round_limit_exceeded: a buyer counter at rounds_remaining 0 is "
            "refused and the id closes permanently (LEP-0009 C5)"
        )
    return problems


def _check_negotiation_expiry(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _negotiation_payload(envelope)
    message = envelope.get("lcp", {}).get("message", {})
    problems: List[str] = []
    expires = _parse_utc_timestamp(payload.get("expires_at"))
    stamp = _parse_utc_timestamp(message.get("timestamp"))
    if expires is not None and stamp is not None and stamp >= expires:
        problems.append(
            "negotiation_expired: message.timestamp is at/after the negotiation's "
            "absolute expires_at (LEP-0009 C8)"
        )
    ping_expires = _parse_utc_timestamp(vector.get("ping_expires_at"))
    if expires is not None and ping_expires is not None and expires > ping_expires:
        problems.append(
            "LCP-100: payload.expires_at exceeds the ping's expiry/TTL ceiling; "
            "no negotiation is created (LEP-0009 C8)"
        )
    return problems


def _check_negotiation_monotone(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _negotiation_payload(envelope)
    prior = vector.get("prior_sender_price")
    role = vector.get("sender_role", "buyer")
    problems: List[str] = []
    price = payload.get("price_cents")
    if isinstance(price, int) and isinstance(prior, int):
        if payload.get("decision") == "counter":
            if role == "buyer" and price <= prior:
                problems.append(
                    f"non_monotone_counter: buyer price {price} is not strictly "
                    f"above its previous {prior} (LEP-0009 C6)"
                )
            if role == "platform" and price < prior:
                problems.append(
                    f"non_monotone_counter: platform counter {price} is below its "
                    f"standing ask {prior}; a concession is an accept, never a "
                    "lower counter (LEP-0009 C6)"
                )
    answered = vector.get("answered_price_cents")
    if (
        payload.get("decision") == "accept"
        and isinstance(answered, int)
        and isinstance(price, int)
        and price != answered
    ):
        problems.append(
            f"LCP-100: accept price {price} does not equal the answered message's "
            f"{answered} (LEP-0009 §3)"
        )
    return problems


_PII_NAME_TOKENS = frozenset(
    {
        "consumer",
        "contact",
        "phone",
        "email",
        "name",
        "address",
        "postal",
        "zip",
        "dob",
        "birth",
        "ssn",
        "social",
        "note",
        "message",
        "comment",
        "description",
        "reference",
    }
)
_CONTACT_DIGEST = re.compile(r"^[a-f0-9]{48,64}$")


def _check_negotiation_no_pii(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _negotiation_payload(envelope)
    message_type = envelope.get("lcp", {}).get("message", {}).get("type")
    if message_type not in _NEGOTIATION_TYPES:
        return []
    allowed = set(load_schema(message_type).get("properties", {}))
    problems: List[str] = []
    for key, value in payload.items():
        if key not in allowed:
            problems.append(
                f"LCP-008 PII_IN_PING: payload.{key} is outside the strict "
                "negotiation allowlist and free text/PII is forbidden (LEP-0009 C4)"
            )
            if set(re.split(r"[^a-z]+", key.lower())) & _PII_NAME_TOKENS:
                problems.append(
                    f"LCP-008 PII_IN_PING: payload.{key} is consumer-shaped; "
                    "negotiation carries no consumer identifier (LEP-0009 C4)"
                )
        if isinstance(value, str) and _CONTACT_DIGEST.match(value):
            problems.append(
                f"LCP-008 PII_IN_PING: payload.{key} carries a contact digest; "
                "no consumer identifier is hashed into a negotiation (LEP-0009 C4)"
            )
    return problems


def _check_negotiation_token(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    payload = _negotiation_payload(envelope)
    token = payload.get("negotiation_binding_token")
    if not isinstance(token, str):
        return []
    problems: List[str] = []
    if not re.fullmatch(r"tok_[A-Za-z0-9_-]{32,128}", token):
        problems.append(
            "LCP-100: negotiation_binding_token fails ^tok_[A-Za-z0-9_-]{32,128}$ "
            "(LEP-0009 §5)"
        )
    state = vector.get("negotiation_state")
    if state is not None and state != "ACCEPTED":
        problems.append(
            f"negotiation_closed: token presented in state {state}; a token is "
            "redeemable once, only while ACCEPTED (LEP-0009 C7)"
        )
    return problems


def _check_negotiation_tenant(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    owner = vector.get("negotiation_buyer_id")
    if owner is None:
        return []
    sender = envelope.get("lcp", {}).get("message", {}).get("sender_id")
    if sender != owner:
        return [
            f"LCP-002: principal {sender!r} presented a negotiation owned by "
            f"{owner!r}; the id is not a bearer credential (LEP-0009 §4)"
        ]
    return []


# --- LEP-0001 (W3) event subscriptions --------------------------------------

# The receiver's advertised event vocabulary. Registration may name only these
# values; there is no wildcard and no second vocabulary.
SUBSCRIPTION_EVENT_NAMES = frozenset(
    {
        "DELIVERED",
        "ACCEPTED",
        "REJECTED",
        "DISPUTED",
        "REFUNDED",
        "EXPIRED",
        "CONVERTED",
        "ARCHIVED",
        "CALL_OFFERED",
        "CALL_CONNECTED",
        "CALL_ENDED",
        "CALL_OUTCOME",
        "CONTACTED",
        "FINAL_PRICING",
    }
)

# A filter is a closed key set drawn from core enums and identifiers. Consumer,
# provenance, attributes, details, contact, and security paths are never
# filters (LEP-0001 W3-04).
SUBSCRIPTION_FILTER_KEYS = frozenset(
    {"event_type", "message_type", "vertical", "status", "lead_id"}
)
_SUBSCRIPTION_PII_PREFIXES = (
    "consumer",
    "provenance",
    "attributes",
    "details",
    "contact",
    "security",
)


def _check_subscription_allowlist(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    """Every ``event_types`` value must be advertised; ``*`` is not a value."""
    message = envelope.get("lcp", {}).get("message", {})
    if message.get("type") != "subscription":
        return []
    event_types = _payload_of(envelope).get("event_types")
    if not isinstance(event_types, list):
        return ["subscription event_types must be an explicit array"]
    problems: List[str] = []
    for name in event_types:
        if name == "*":
            problems.append(
                "LCP-100: event_types must be explicit; a wildcard subscription "
                "is not allowed (LEP-0001 W3-01)"
            )
        elif name not in SUBSCRIPTION_EVENT_NAMES:
            problems.append(
                f"LCP-100: event type {name!r} is not advertised in "
                "capabilities().events (LEP-0001 W3-01)"
            )
    return problems


def _check_subscription_filter_closed(
    vector: dict, envelope: dict, errors: List[str]
) -> List[str]:
    """Refuse filter keys outside the closed core enum/id set, by name."""
    message = envelope.get("lcp", {}).get("message", {})
    if message.get("type") != "subscription":
        return []
    filter_values = _payload_of(envelope).get("filter")
    if filter_values is None:
        return []
    if not isinstance(filter_values, dict):
        return ["LCP-100: subscription filter must be an object"]
    problems: List[str] = []
    for key in filter_values:
        if key in SUBSCRIPTION_FILTER_KEYS:
            continue
        head = str(key).split(".", 1)[0]
        axis = "PII" if head in _SUBSCRIPTION_PII_PREFIXES else "unknown"
        problems.append(
            f"LCP-100: filter field {key!r} is refused ({axis}); the closed key "
            f"set is {sorted(SUBSCRIPTION_FILTER_KEYS)} (LEP-0001 W3-04)"
        )
    return problems


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
    # LEP-0003 W6-07 (§9-gated): platform-recorded email/form relay entries.
    "email_form_entry_shape": _check_email_form_entry_shape,
    # v1.2 (D2/D7):
    "ack_processed_post_only": _check_ack_processed_post_only,
    "pricing_fx_comparability": _check_pricing_fx_comparability,
    # LEP-0005 (R1): cross-artifact pattern equivalence, run once per run too.
    "history_filter_pattern_equivalence": _check_history_filter_pattern_equivalence,
    # LEP-0001 (W2-01): bounded batch submission shape, caps, and receipts.
    "batch_shape": _check_batch_shape,
    # LEP-0001 (W3): subscription registration allowlist and privacy-safe
    # filter keys.
    "subscription_schema": _schema_check("subscription"),
    "subscription_allowlist": _check_subscription_allowlist,
    "subscription_filter_closed": _check_subscription_filter_closed,
    # LEP-0009 (v1.2 D8): negotiation capability, floor, rounds, expiry,
    # monotonicity, PII, token, and tenant rules.
    "quote_schema": _schema_check("quote"),
    "counter_schema": _schema_check("counter"),
    "negotiation_capability": _check_negotiation_capability,
    "negotiation_floor_refused": _check_negotiation_floor,
    "negotiation_round_cap": _check_negotiation_rounds,
    "negotiation_expiry": _check_negotiation_expiry,
    "negotiation_monotone": _check_negotiation_monotone,
    "negotiation_no_pii": _check_negotiation_no_pii,
    "negotiation_binding_token": _check_negotiation_token,
    "negotiation_tenant": _check_negotiation_tenant,
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


def run_contract_checks() -> TestResult:
    """Cross-artifact contracts: evaluated once per run, not per vector."""
    errors = _check_history_filter_pattern_equivalence({}, {}, [])
    return TestResult(
        "CONTRACT-HISTORY",
        "OpenAPI history filter patterns match the JSON Schema row contract",
        not errors,
        errors,
    )


# --- Main --------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="LCP Conformance Runner")
    parser.add_argument(
        "--tier", choices=["L1", "L2", "L3", "NEG", "all"], default="all"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    tiers = {
        "L1": "l1-envelope.json",
        "L2": "l2-lifecycle.json",
        "L3": "l3-advanced.json",
        # LEP-0009: its own registered tier (id prefix NEG), loaded exactly
        # like the others so a missing/renamed file is a visible warning.
        "NEG": "negotiation.json",
    }

    if args.tier != "all":
        tiers = {args.tier: tiers[args.tier]}

    total_pass = 0
    total_fail = 0

    suites = [
        (f"{tier_name} - {tier_file}", run_tier(tier_file))
        for tier_name, tier_file in sorted(tiers.items())
    ]
    suites.append(("CONTRACT - OpenAPI <-> JSON Schema", [run_contract_checks()]))

    for label, results in suites:
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"{'=' * 60}")

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

        print(f"\n  {label.split(' - ')[0]}: {tier_pass} passed, {tier_fail} failed")
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

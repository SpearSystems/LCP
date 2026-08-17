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
_registry = Registry().with_resource(
    uri="https://lcp.dev/schemas/core.json", resource=_core_resource
).with_resource(
    uri="core.json", resource=_core_resource
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
            violations.extend(_nested_ping_safe_errors(field_value, definition, field_path))
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
    def __init__(self, vector_id: str, name: str, passed: bool, errors: Optional[List[str]] = None):
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
                errors.append("envelope: message.id must be a UUID v4 at lcp/message/id")
        except ValueError:
            errors.append("envelope: message.id must be a UUID at lcp/message/id")
    timestamp = message.get("timestamp")
    if isinstance(timestamp, str):
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed_timestamp.tzinfo is None or "T" not in timestamp:
                raise ValueError
        except ValueError:
            errors.append("envelope: message.timestamp must be an RFC3339 date-time at lcp/message/timestamp")

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
        vertical = payload.get("vertical") if msg_type == "ping" else (
            attributes.get("vertical") if isinstance(attributes, dict) else None
        )
        if isinstance(vertical, str) and isinstance(attributes, dict):
            vertical_validator = _vertical_validators.get(vertical)
            if vertical_validator is None:
                errors.append(f"vertical: unknown vertical schema '{vertical}'")
            else:
                vertical_attributes = dict(attributes)
                if msg_type == "ping":
                    version_definition = vertical_validator.schema.get("properties", {}).get("schema_version", {})
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

    return errors


def run_vector(vector: dict) -> TestResult:
    """Run a single test vector."""
    vid = vector.get("id", "?")
    name = vector.get("name", "?")
    expect = vector.get("expect", "pass")

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

    if expect == "pass":
        if errors:
            return TestResult(vid, name, False, errors)
        return TestResult(vid, name, True)
    elif expect == "fail":
        if not errors:
            return TestResult(vid, name, False, ["expected failure but message validated successfully"])
        expected_text = vector.get("error_contains")
        if expected_text:
            expected_values = [expected_text] if isinstance(expected_text, str) else expected_text
            combined = " ".join(errors)
            missing = [value for value in expected_values if value not in combined]
            if missing:
                return TestResult(
                    vid,
                    name,
                    False,
                    [f"expected error text not found: {value}" for value in missing] + errors,
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
        print(f"\n{'='*60}")
        print(f"  {tier_name} - {tier_file}")
        print(f"{'='*60}")

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

    print(f"\n{'='*60}")
    print(f"  TOTAL: {total_pass} passed, {total_fail} failed")
    print(f"{'='*60}")

    if total_fail > 0:
        print(f"\n  {total_fail} test(s) FAILED")
        sys.exit(1)
    else:
        print(f"\n  ALL {total_pass} tests PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
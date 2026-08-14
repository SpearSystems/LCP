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
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import jsonschema
    from jsonschema import Draft202012Validator
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


def make_validator(schema: dict) -> Draft202012Validator:
    """Create a validator that resolves $ref to core.json definitions."""
    return Draft202012Validator(schema, registry=_registry)


# --- Message schema registry -------------------------------------------------

_validators: Dict[str, Draft202012Validator] = {}
for _msg_type in ["lead", "call", "ping", "post", "ack", "event", "bid"]:
    _validators[_msg_type] = make_validator(load_schema(_msg_type))

_envelope_validator = make_validator(load_schema("envelope"))

# --- Ping-safe enforcement ---------------------------------------------------


def get_ping_safe_fields(vertical_name: str) -> Tuple[Set[str], Set[str]]:
    """Return (safe_fields, unsafe_fields) from a vertical schema."""
    vschema = load_vertical(vertical_name)
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


def check_ping_safe(payload: dict) -> List[str]:
    """Check that ping attributes only contain ping_safe fields.

    Returns list of violation messages (empty = pass).
    """
    violations: List[str] = []
    attrs = payload.get("attributes", {})
    vertical = payload.get("vertical", "")
    if not vertical or not attrs:
        return violations  # nothing to check

    vertical_file = VERTICALS_DIR / f"{vertical}.json"
    if not vertical_file.exists():
        violations.append(
            f"PII_IN_PING: vertical schema '{vertical}' not found — cannot validate ping_safe"
        )
        return violations

    safe, unsafe = get_ping_safe_fields(vertical)
    for field_name in attrs:
        if field_name in unsafe:
            violations.append(
                f"PII_IN_PING: attributes.{field_name} is tagged ping_safe: false "
                f"in vertical '{vertical}' (LCP-008)"
            )
        elif field_name not in safe:
            violations.append(
                f"PII_IN_PING: attributes.{field_name} is not tagged ping_safe "
                f"in vertical '{vertical}' (LCP-008)"
            )
    return violations


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

    # 1. Validate envelope structure
    for err in _envelope_validator.iter_errors(envelope):
        errors.append(f"envelope: {err.message} at {_format_path(err.path)}")

    if errors:
        return errors  # Can't validate payload if envelope is broken

    # 2. Extract message type and validate payload against the right schema
    msg_type = envelope.get("lcp", {}).get("message", {}).get("type")
    payload = envelope.get("lcp", {}).get("payload", {})

    if msg_type in _validators:
        for err in _validators[msg_type].iter_errors(payload):
            errors.append(f"{msg_type}: {err.message} at {_format_path(err.path)}")

    # 3. Ping-specific PII checks
    if msg_type == "ping":
        ping_safe_violations = check_ping_safe(payload)
        errors.extend(ping_safe_violations)

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
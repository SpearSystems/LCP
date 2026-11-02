"""Python SDK must refuse every attack in test-vectors/sdk/negative/.

Loads the shared negative security-vector suite (format documented in
test-vectors/sdk/negative/README.md), runs each vector through the SDK's real
verify/validate primitives (lcp_sdk.signing / lcp_sdk.validation), and asserts
the expected outcome. Deterministic: fixed clocks, bundled repo schemas, no
network. Gap vectors (sdk_exercisable: false) are reported loudly on stderr —
never a silent pass. Format lint runs first so a malformed vector fails CI
even when behavior would accidentally match.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

from lcp_sdk.signing import SignatureError, verify_hmac
from lcp_sdk.validation import MESSAGE_TYPES, SchemaValidator

ROOT = Path(__file__).resolve().parents[4]
NEGATIVE_DIR = ROOT / "test-vectors" / "sdk" / "negative"

SUITE_NAME = "lcp-sdk-negative"
SUITE_VERSION = 1
REQUIRED_KEYS = (
    "id",
    "invariant",
    "spec_ref",
    "description",
    "applies_to",
    "check",
    "inputs",
    "expect",
    "sdk_exercisable",
)
# SPEC.md §5 codes each check is allowed to expect on rejection.
CHECK_CODES = {
    "verify_hmac": {"LCP-012"},
    "verify_raw_body": {"LCP-012"},
    "envelope_validate": {"LCP-006", "LCP-008", "LCP-100"},
    "offer_validate": {"LCP-100"},
    "production_mode_verify": {"LCP-013"},
}


def load_suites() -> list[dict[str, Any]]:
    files = sorted(NEGATIVE_DIR.glob("*.json"))
    if not files:
        raise AssertionError(f"no vector files found under {NEGATIVE_DIR}")
    suites = []
    for path in files:
        with path.open(encoding="utf-8") as handle:
            suite = json.load(handle)
        suite["_file"] = path.name
        suites.append(suite)
    return suites


def iter_vectors(suites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [vector for suite in suites for vector in suite.get("vectors", [])]


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class NegativeVectorFormatTests(unittest.TestCase):
    """Lint the shared vector format before trusting any behavior assertion."""

    def test_suite_files_are_well_formed(self) -> None:
        problems: list[str] = []
        seen_ids: set[str] = set()
        for suite in load_suites():
            source = suite["_file"]
            if suite.get("suite") != SUITE_NAME:
                problems.append(f"{source}: suite must be {SUITE_NAME!r}")
            if suite.get("version") != SUITE_VERSION:
                problems.append(
                    f"{source}: unsupported suite version {suite.get('version')!r}"
                )
            for field in ("group", "description"):
                if not suite.get(field):
                    problems.append(f"{source}: missing {field}")
            if not suite.get("vectors"):
                problems.append(f"{source}: empty vectors array")
            for vector in suite.get("vectors", []):
                vid = vector.get("id", "(no id)")
                where = f"{source}:{vid}"
                for key in REQUIRED_KEYS:
                    if key not in vector:
                        problems.append(f"{where}: missing field {key!r}")
                if not vid.startswith("lcp-neg-"):
                    problems.append(f"{where}: id must start with 'lcp-neg-'")
                if vid in seen_ids:
                    problems.append(f"{where}: duplicate vector id")
                seen_ids.add(vid)
                invariant = vector.get("invariant")
                if invariant is not None and not (
                    isinstance(invariant, int)
                    and not isinstance(invariant, bool)
                    and 1 <= invariant <= 8
                ):
                    problems.append(
                        f"{where}: invariant must be 1-8 or null, got {invariant!r}"
                    )
                if not vector.get("spec_ref"):
                    problems.append(
                        f"{where}: spec_ref required (even with invariant set)"
                    )
                applies_to = vector.get("applies_to")
                if not isinstance(applies_to, list) or not applies_to:
                    problems.append(f"{where}: applies_to must be a non-empty list")
                elif any(
                    kind not in MESSAGE_TYPES and kind != "*" for kind in applies_to
                ):
                    problems.append(
                        f"{where}: applies_to has unknown kind {applies_to!r}"
                    )
                check = vector.get("check")
                if check not in CHECK_CODES:
                    problems.append(f"{where}: unknown check {check!r}")
                    continue
                if not isinstance(vector.get("inputs"), dict):
                    problems.append(f"{where}: inputs must be an object")
                expect = vector.get("expect") or {}
                outcome = expect.get("outcome")
                if outcome not in ("reject", "accept"):
                    problems.append(f"{where}: expect.outcome must be reject|accept")
                elif outcome == "reject":
                    if expect.get("error_code") not in CHECK_CODES[check]:
                        problems.append(
                            f"{where}: reject expects error_code in "
                            f"{sorted(CHECK_CODES[check])}, got {expect.get('error_code')!r}"
                        )
                    if not expect.get("error_name"):
                        problems.append(f"{where}: reject requires error_name")
                elif outcome == "accept" and (
                    expect.get("error_code") is not None
                    or expect.get("error_name") is not None
                ):
                    problems.append(
                        f"{where}: accept vectors must not declare an error"
                    )
                if vector.get("sdk_exercisable") is False:
                    note = vector.get("gap_note")
                    if not isinstance(note, str) or len(note) < 40:
                        problems.append(
                            f"{where}: sdk_exercisable=false requires a substantive gap_note"
                        )
                elif "gap_note" in vector:
                    problems.append(
                        f"{where}: gap_note only allowed when sdk_exercisable is false"
                    )
        self.assertEqual([], problems)


class NegativeVectorExecutionTests(unittest.TestCase):
    """Run every exercisable vector against the real SDK primitives."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = SchemaValidator(ROOT / "schemas")

    def _execute(self, vector: dict[str, Any]) -> tuple[str, str]:
        """Return ("reject"|"accept"|"gap", detail)."""
        inputs = vector["inputs"]
        check = vector["check"]
        if vector.get("sdk_exercisable", True) is False:
            return "gap", vector["gap_note"]
        if check in ("verify_hmac", "verify_raw_body"):
            body = inputs["body"] if check == "verify_hmac" else inputs["verify_body"]
            try:
                verify_hmac(
                    inputs["secret"],
                    inputs["signature"],
                    inputs["timestamp"],
                    inputs["idempotency_key"],
                    body.encode("utf-8"),
                    max_skew_seconds=inputs["replay_window_seconds"],
                    now=_parse_iso(inputs["now"]),
                )
                return "accept", ""
            except SignatureError as exc:
                return "reject", f"SignatureError: {exc}"
        if check == "envelope_validate":
            errors = self.validator.validate_envelope(inputs["envelope"])
            return ("reject", "; ".join(errors)) if errors else ("accept", "")
        if check == "offer_validate":
            errors = self.validator.validate_offer(inputs["offer"])
            return ("reject", "; ".join(errors)) if errors else ("accept", "")
        # Exercisable but no adapter in this runner: format lint passed, so the
        # vector expects an SDK surface this lane has not mapped. Fail loudly.
        return "error", f"no runner adapter for check {check!r}"

    def test_negative_vector_outcomes(self) -> None:
        mismatches: list[str] = []
        gaps: list[str] = []
        ran = 0
        for vector in iter_vectors(load_suites()):
            outcome, detail = self._execute(vector)
            if outcome == "gap":
                gaps.append(f"{vector['id']}: {detail}")
                continue
            ran += 1
            expected = vector["expect"]["outcome"]
            if outcome != expected:
                mismatches.append(
                    f"{vector['id']} ({vector['check']}): expected {expected}, "
                    f"got {outcome} — {detail or 'no detail'} — {vector['description']}"
                )
        for gap in gaps:
            print(f"GAP (reported, never silently passed): {gap}", file=sys.stderr)
        self.assertEqual([], mismatches)
        self.assertGreater(ran, 0, "no negative vector was actually executed")


if __name__ == "__main__":
    unittest.main()

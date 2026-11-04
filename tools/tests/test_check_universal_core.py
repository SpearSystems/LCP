"""Universal-audit checker tests: deterministic, filesystem-only.

Every test docstring quotes the governing clause verbatim (SPEC.md @
5d6000dbc0d70bccb0ed8ea62472c350d29755b0, §12 "Universal audit" and §3
"Canonical core"). The sandbox seeds a copy of the real schemas/,
verticals/, spec/ and reference-platform storage.py so each planted
violation mutates exactly one machine-checkable §12 member.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1]))

import check_universal_core  # noqa: E402
from check_universal_core import find_violations  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
STORAGE = (
    REPO / "implementations" / "reference-platform" / "lcp_platform" / "storage.py"
)


class Sandbox(unittest.TestCase):
    """Tmp repo seeded with the full real audit inputs, then mutated per test."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(REPO / "schemas", self.tmp / "schemas")
        shutil.copytree(REPO / "verticals", self.tmp / "verticals")
        shutil.copytree(REPO / "spec", self.tmp / "spec")
        shutil.copyfile(STORAGE, self.tmp / "storage.py")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def audit(self) -> list[str]:
        return find_violations(
            self.tmp / "schemas",
            self.tmp / "verticals",
            self.tmp / "spec",
            self.tmp / "storage.py",
        )

    def fired(self, check_id: str) -> list[str]:
        return [e for e in self.audit() if e.startswith(f"{check_id}:")]

    def edit_json(self, rel: str, fn: Any) -> None:
        path = self.tmp / rel
        doc = json.loads(path.read_text(encoding="utf-8"))
        fn(doc)
        path.write_text(json.dumps(doc), encoding="utf-8")

    def mutate_spec(self, old: str, new: str) -> None:
        for path in sorted((self.tmp / "spec").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if old in text:
                path.write_text(text.replace(old, new), encoding="utf-8")
                return
        raise AssertionError(f"fixture drift: {old!r} no longer in spec/")

    def mutate_storage(self, old: str, new: str) -> None:
        path = self.tmp / "storage.py"
        text = path.read_text(encoding="utf-8")
        assert old in text, f"fixture drift: {old!r} no longer in storage.py"
        path.write_text(text.replace(old, new), encoding="utf-8")


class TestCleanBaseline(Sandbox):
    """§12: "The core must work for a gutter lead in Australia, a solar lead
    in the US, a call lead in NZ, and a lead submitted by an AI agent — with
    zero core changes." The shipped repo must pass the mechanical subset."""

    def test_real_repo_passes_clean(self) -> None:
        self.assertEqual(
            find_violations(REPO / "schemas", REPO / "verticals", REPO / "spec"),
            [],
        )

    def test_sandbox_copy_passes_clean(self) -> None:
        self.assertEqual(self.audit(), [])


class TestNoVerticalFieldsInCore(Sandbox):
    """§12: "[ ] No vertical-specific fields in core." Inverse of
    check_reserved_fields: a vertical-schema property name may not appear in
    any schema; the attributes envelope (vertical/schema_version) and the
    country-scoped discriminator (country_code) stay exempt."""

    def test_vertical_name_planted_in_core_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["consumer"]["properties"].__setitem__(
                "accident_type", {"type": "string"}
            ),
        )
        fired = self.fired("UCA-CORE-NO-VERTICAL-FIELDS")
        self.assertEqual(len(fired), 1)
        self.assertIn("accident_type", fired[0])

    def test_vertical_name_in_message_schema_fires(self) -> None:
        self.edit_json(
            "schemas/lead.json",
            lambda d: d["properties"].__setitem__("claim_stage", {"type": "string"}),
        )
        fired = self.fired("UCA-CORE-NO-VERTICAL-FIELDS")
        self.assertEqual(len(fired), 1)
        self.assertIn("claim_stage", fired[0])

    def test_envelope_and_country_code_stay_exempt(self) -> None:
        # country_code/vertical/schema_version legitimately exist on both
        # sides (core location + every vertical root); clean baseline holds.
        self.assertEqual(self.audit(), [])


class TestComplianceGeneric(Sandbox):
    """§12: "[ ] No market-specific fields in core — including named
    sub-fields of ``compliance`` (no vendor names like ``jornaya_token`` or
    law references like ``tcpac_consent`` as core fields; use
    ``consent_evidence[]`` with generic ``type``/``provider``)."""

    def test_vendor_named_compliance_subfield_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["compliance"]["properties"].__setitem__(
                "jornaya_token", {"type": "string"}
            ),
        )
        fired = self.fired("UCA-CORE-COMPLIANCE-GENERIC")
        self.assertEqual(len(fired), 1)
        self.assertIn("jornaya_token", fired[0])

    def test_law_reference_compliance_subfield_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["compliance"]["properties"].__setitem__(
                "tcpac_consent", {"type": "boolean"}
            ),
        )
        fired = self.fired("UCA-CORE-COMPLIANCE-GENERIC")
        self.assertEqual(len(fired), 1)
        self.assertIn("tcpac_consent", fired[0])


class TestDemographicsAbsent(Sandbox):
    """§12: "[ ] ``consumer.gender`` is NOT in core (moved to vertical
    ``attributes``)." §3: "Vertical schemas that need gender (e.g. insurance)
    define it in ``attributes``." CON-08 assert-absent pin."""

    def test_gender_planted_in_core_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["consumer"]["properties"].__setitem__(
                "gender", {"type": "string"}
            ),
        )
        fired = self.fired("UCA-DEMOGRAPHICS-ABSENT")
        self.assertEqual(len(fired), 1)
        self.assertIn("gender", fired[0])

    def test_nested_marital_status_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["compliance"]["properties"]["otp"][
                "properties"
            ].__setitem__("marital_status", {"type": "string"}),
        )
        fired = self.fired("UCA-DEMOGRAPHICS-ABSENT")
        self.assertEqual(len(fired), 1)
        self.assertIn("marital_status", fired[0])

    def test_gender_in_vertical_is_legal(self) -> None:
        # insurance.json defines gender in attributes; the pin targets schemas
        # only — clean baseline proves no false positive.
        self.assertIn("gender", REPO.joinpath("verticals/insurance.json").read_text())
        self.assertEqual(self.fired("UCA-DEMOGRAPHICS-ABSENT"), [])


class TestFullNameAlternative(Sandbox):
    """§12: "[ ] ``consumer`` does not assume Western name splitting
    (``full_name`` available as alternative to ``first_name``/``last_name``)."""

    def test_full_name_removal_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["consumer"]["properties"].pop("full_name"),
        )
        fired = self.fired("UCA-FULLNAME-ALT")
        self.assertEqual(len(fired), 1)
        self.assertIn("full_name", fired[0])


class TestPhoneUniversal(Sandbox):
    """§12: "[ ] Phone E.164 universal; ``phone_hash`` is HMAC-SHA256
    (per-pair)."""

    def test_phone_pattern_removal_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["consumer"]["properties"]["phone"].pop("pattern"),
        )
        self.assertTrue(self.fired("UCA-PHONE-UNIVERSAL"))

    def test_hash_pattern_weakened_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["consumer"]["properties"]["phone_hash"].__setitem__(
                "pattern", "^[a-f0-9]+$"
            ),
        )
        fired = self.fired("UCA-PHONE-UNIVERSAL")
        self.assertEqual(len(fired), 1)
        self.assertIn("phone_hash", fired[0])


class TestCallFirstClass(Sandbox):
    """§12: "[ ] Call leads first-class (``call`` message type)."""

    def test_call_schema_removal_fires(self) -> None:
        (self.tmp / "schemas" / "call.json").unlink()
        fired = self.fired("UCA-CALL-FIRSTCLASS")
        self.assertEqual(len(fired), 1)


class TestAgentReady(Sandbox):
    """§12: "[ ] AI-agent submissions: ``channel=agent``,
    ``source_type=agent``."""

    def test_channel_agent_removed_from_spec_fires(self) -> None:
        self.mutate_spec("click|api|agent", "click|api")
        fired = self.fired("UCA-AGENT-READY")
        self.assertEqual(len(fired), 1)
        self.assertIn("channel", fired[0])

    def test_source_type_agent_removed_from_spec_fires(self) -> None:
        self.mutate_spec("direct|agent|api", "direct|api")
        fired = self.fired("UCA-AGENT-READY")
        self.assertEqual(len(fired), 1)
        self.assertIn("source_type", fired[0])

    def test_source_type_field_removed_from_schema_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["provenance"]["properties"].pop("source_type"),
        )
        self.assertTrue(self.fired("UCA-AGENT-READY"))


class TestPingAllowlist(Sandbox):
    """§12: "[ ] Ping is a strict allowlist (``additionalProperties: false``),
    not a blocklist."""

    def test_ping_allowlist_flip_fires(self) -> None:
        self.edit_json(
            "schemas/ping.json", lambda d: d.__setitem__("additionalProperties", True)
        )
        self.assertTrue(self.fired("UCA-PING-ALLOWLIST"))


class TestPingSafeTagged(Sandbox):
    """§12: "[ ] Vertical schemas tag every field ``ping_safe: true/false``."""

    def test_missing_ping_safe_tag_fires(self) -> None:
        def strip(d: dict) -> None:
            for name, subschema in d["properties"].items():
                if name != "vertical":
                    subschema.pop("ping_safe", None)

        self.edit_json("verticals/solar.json", strip)
        fired = self.fired("UCA-PINGSAFE-TAGGED")
        self.assertTrue(fired)
        self.assertIn("solar.json", fired[0])

    def test_envelope_keys_exempt(self) -> None:
        # vertical/schema_version are untagged on purpose (attributes
        # envelope, not data) — clean baseline holds without firing.
        self.assertEqual(self.fired("UCA-PINGSAFE-TAGGED"), [])


class TestMetricsAbsent(Sandbox):
    """§3: "Performance metrics (speed-to-contact, contact rate, close rate,
    close value) are buyer-side analytics, not lead data. They belong in the
    extensions mechanism, not in the core or vertical schemas" (ATR-06).
    §3 exception: "The CONVERTED event's details.conversion_value_cents is
    the one exception ... because it's a lifecycle fact, not a calculated
    metric."""

    def test_metric_planted_in_vertical_fires(self) -> None:
        self.edit_json(
            "verticals/solar.json",
            lambda d: d["properties"].__setitem__(
                "contact_rate", {"type": "number", "ping_safe": True}
            ),
        )
        fired = self.fired("UCA-METRICS-ABSENT")
        self.assertEqual(len(fired), 1)
        self.assertIn("contact_rate", fired[0])

    def test_volume_name_in_core_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["consumer"]["properties"].__setitem__(
                "storage_volume", {"type": "integer"}
            ),
        )
        fired = self.fired("UCA-METRICS-ABSENT")
        self.assertEqual(len(fired), 1)
        self.assertIn("storage_volume", fired[0])

    def test_close_value_in_core_fires(self) -> None:
        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["compliance"]["properties"].__setitem__(
                "close_value_cents", {"type": "integer"}
            ),
        )
        fired = self.fired("UCA-METRICS-ABSENT")
        self.assertEqual(len(fired), 1)
        self.assertIn("close_value_cents", fired[0])

    def test_conversion_value_event_exception_allowed(self) -> None:
        # §3 names this exact field inside the event's details as the one
        # legal exception; the checker must not false-positive on it.
        def add(d: dict) -> None:
            d["properties"]["details"]["properties"] = {
                "conversion_value_cents": {"type": "integer"}
            }

        self.edit_json("schemas/event.json", add)
        fired = self.fired("UCA-METRICS-ABSENT")
        self.assertEqual(fired, [])
        # ...but the same name outside event details is a metric violation.
        self.edit_json(
            "schemas/lead.json",
            lambda d: d["properties"].__setitem__(
                "conversion_value_cents", {"type": "integer"}
            ),
        )
        fired = self.fired("UCA-METRICS-ABSENT")
        self.assertEqual(len(fired), 1)
        self.assertIn("conversion_value_cents", fired[0])

    def test_loan_rate_fields_are_not_metrics(self) -> None:
        # Mortgage product data (desired_rate_term, existing_rate_percent)
        # must never trip the metric deny-list: no false positives shipped.
        self.assertEqual(self.fired("UCA-METRICS-ABSENT"), [])


class TestCountriesIso(Sandbox):
    """§12: "[ ] ``capabilities.countries`` is ISO 3166-1 alpha-2 codes (no
    legacy ``markets`` grouping)." §8: "legacy ``markets`` field — no more
    ``au_nz`` / ``us`` grouping."""

    def test_legacy_markets_property_fires(self) -> None:
        self.edit_json(
            "schemas/offer.json",
            lambda d: d["properties"].__setitem__(
                "markets", {"type": "array", "items": {"type": "string"}}
            ),
        )
        fired = self.fired("UCA-COUNTRIES-ISO")
        self.assertEqual(len(fired), 1)
        self.assertIn("markets", fired[0])

    def test_countries_pattern_weakened_fires(self) -> None:
        self.edit_json(
            "schemas/offer.json",
            lambda d: d["properties"]["countries"]["items"].pop("pattern"),
        )
        fired = self.fired("UCA-COUNTRIES-ISO")
        self.assertEqual(len(fired), 1)
        self.assertIn("countries", fired[0])


class TestTransitionTable(Sandbox):
    """§12: "[ ] Status transition table is published and matches §3." The
    §3-published edge DISPUTED → ACCEPTED, the 11-state list and the
    terminal-state set must equal the reference platform's
    LEGAL_LEAD_TRANSITIONS graph."""

    def test_extra_state_breaks_match(self) -> None:
        self.mutate_storage(
            '    "DUPLICATE": set(),',
            '    "DUPLICATE": set(),\n    "PENDING": set(),',
        )
        fired = self.fired("UCA-TRANSITION-TABLE")
        self.assertTrue(fired)
        self.assertIn("PENDING", fired[0])

    def test_notable_edge_removed_breaks_match(self) -> None:
        self.mutate_storage(
            '"DISPUTED": {"REFUNDED", "ACCEPTED"},',
            '"DISPUTED": {"REFUNDED"},',
        )
        fired = self.fired("UCA-TRANSITION-TABLE")
        self.assertEqual(len(fired), 1)
        self.assertIn("DISPUTED → ACCEPTED", fired[0])

    def test_terminal_state_broken_breaks_match(self) -> None:
        self.mutate_storage('"ARCHIVED": set(),', '"ARCHIVED": {"NEW"},')
        fired = self.fired("UCA-TRANSITION-TABLE")
        self.assertTrue(fired)
        self.assertIn("terminal states differ", fired[0])


class TestReservedShadowAggregated(Sandbox):
    """§12: "[ ] Vertical schemas do not shadow core field names in
    ``attributes``." UNI-01 aggregates check_reserved_fields instead of
    re-implementing it."""

    def test_reserved_shadow_surfaces_via_audit(self) -> None:
        self.edit_json(
            "verticals/insurance.json",
            lambda d: d["properties"].__setitem__(
                "phone", {"type": "string", "ping_safe": False}
            ),
        )
        fired = self.fired("UCA-RESERVED-NO-SHADOW")
        self.assertEqual(len(fired), 1)
        self.assertIn("redefines reserved core field 'phone'", fired[0])


class TestEnumPolicyStated(Sandbox):
    """§12: "[ ] Open/closed enum policy is stated (§6)."""

    def test_heading_removal_fires(self) -> None:
        self.mutate_spec(
            "### Open vs. closed enumerations",
            "### (removed) enumeration flexibility",
        )
        fired = self.fired("UCA-ENUM-POLICY-STATED")
        self.assertEqual(len(fired), 1)


class TestFailLoud(Sandbox):
    def test_missing_core_fails_loud(self) -> None:
        shutil.rmtree(self.tmp / "schemas")
        errors = self.audit()
        self.assertEqual(len(errors), 1)
        self.assertIn("core schema is the universal-audit source", errors[0])

    def test_unparseable_vertical_fails_loud(self) -> None:
        (self.tmp / "verticals" / "broken.json").write_text("{oops", encoding="utf-8")
        errors = self.audit()
        self.assertTrue(any(e.startswith("UCA-LOAD:") for e in errors))


class TestCli(Sandbox):
    def test_cli_exit_codes(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code_ok = check_universal_core.main([])
        self.assertEqual(code_ok, 0)
        self.assertIn("check_universal_core:", out.getvalue())

        self.edit_json(
            "schemas/core.json",
            lambda d: d["$defs"]["consumer"]["properties"].__setitem__(
                "gender", {"type": "string"}
            ),
        )
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code_bad = check_universal_core.main(
                [
                    "--schemas-dir",
                    str(self.tmp / "schemas"),
                    "--verticals-dir",
                    str(self.tmp / "verticals"),
                    "--spec",
                    str(self.tmp / "spec"),
                    "--platform-storage",
                    str(self.tmp / "storage.py"),
                ]
            )
        self.assertEqual(code_bad, 1)
        self.assertIn("UCA-DEMOGRAPHICS-ABSENT", err.getvalue())


if __name__ == "__main__":
    unittest.main()

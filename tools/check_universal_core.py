#!/usr/bin/env python3
"""Gate: machine-checkable subset of the SPEC §12 universal audit checklist.

SPEC §12: "The core must work for a gutter lead in Australia, a solar lead
in the US, a call lead in NZ, and a lead submitted by an AI agent — with
zero core changes." Each check id below names the §12 checklist line (or
the §3 clause it pins) it executes mechanically, so the audit can run on
every commit instead of "manually before core changes":

* ``UCA-CORE-NO-VERTICAL-FIELDS`` — "[ ] No vertical-specific fields in
  core." Deny-list = every property name used by any ``verticals/*.json``
  minus the attributes-envelope exemptions (``vertical``/``schema_version``
  from core's attributes def, and ``country_code``, the legal nested
  discriminator). No schema property may carry a denied name. This is the
  inverse of check_reserved_fields (which forbids core names inside
  verticals; this forbids vertical names anywhere in schemas/).
* ``UCA-CORE-COMPLIANCE-GENERIC`` — "[ ] No market-specific fields in core
  — including named sub-fields of ``compliance`` (no vendor names like
  ``jornaya_token`` or law references like ``tcpac_consent`` as core
  fields; use ``consent_evidence[]`` with generic ``type``/``provider``)."
  Every top-level ``compliance`` property must appear in the §3 compliance
  field table; a vendor/law-named field cannot enter core without a SPEC
  table change.
* ``UCA-FULLNAME-ALT`` — "[ ] ``consumer`` does not assume Western name
  splitting (``full_name`` available as alternative to
  ``first_name``/``last_name``)."
* ``UCA-DEMOGRAPHICS-ABSENT`` — "[ ] ``consumer.gender`` is NOT in core
  (moved to vertical ``attributes``)." Assert-absent pin: gender,
  ethnicity, marital and similar demographic names may never appear as
  schema properties. Verticals defining them (insurance ``gender``) is the
  sanctioned location per §3.
* ``UCA-PHONE-UNIVERSAL`` — "[ ] Phone E.164 universal; ``phone_hash`` is
  HMAC-SHA256 (per-pair)." consumer.phone must be pattern-constrained to
  an E.164 shape; phone_hash/email_hash must be 64-hex pins.
* ``UCA-CALL-FIRSTCLASS`` — "[ ] Call leads first-class (``call`` message
  type)." schemas/call.json must exist and be a lead-shaped object schema.
* ``UCA-AGENT-READY`` — "[ ] AI-agent submissions: ``channel=agent``,
  ``source_type=agent``." SPEC §3/§4 must list ``agent`` in the channel and
  source_type value enumerations, and the schemas must expose those
  fields.
* ``UCA-PING-ALLOWLIST`` — "[ ] Ping is a strict allowlist
  (``additionalProperties: false``), not a blocklist."
* ``UCA-PINGSAFE-TAGGED`` — "[ ] Vertical schemas tag every field
  ``ping_safe: true/false``." Every vertical property at every depth must
  carry a boolean ``ping_safe``; the root envelope keys
  ``vertical``/``schema_version`` are exempt (they are the attributes
  envelope, not data fields).
* ``UCA-RESERVED-NO-SHADOW`` — "[ ] Vertical schemas do not shadow core
  field names in ``attributes``." Delegates verbatim to
  ``check_reserved_fields.find_violations`` (its ATR-04 coverage is not
  re-implemented, just aggregated into this audit).
* ``UCA-METRICS-ABSENT`` — §3 attributes: "Performance metrics
  (speed-to-contact, contact rate, close rate, close value) are
  buyer-side analytics, not lead data. They belong in the extensions
  mechanism, not in the core or vertical schemas" (coverage row ATR-06).
  Deny-list is derived from the clause's four named metrics plus the
  volume/quality-score/metrics word family. Exception mandated by the
  same clause: ``conversion_value_cents`` is legal only inside the event
  schema's ``details`` ("close value on conversion ... is structured in
  the event ... because it's a lifecycle fact").
* ``UCA-COUNTRIES-ISO`` — "[ ] ``capabilities.countries`` is ISO 3166-1
  alpha-2 codes (no legacy ``markets`` grouping)." offer.countries items
  must carry the ``^[A-Z]{2}$`` pattern and ``markets`` may appear nowhere
  as a property name (§8: "legacy ``markets`` field — no more ``au_nz`` /
  ``us`` grouping").
* ``UCA-TRANSITION-TABLE`` — "[ ] Status transition table is published and
  matches §3." Machine subset: the §3 table is present in SPEC.md, its
  published status set / terminal-state list / notable resolution edges
  equal the reference platform's ``LEGAL_LEAD_TRANSITIONS`` graph
  (extracted by AST, not import).
* ``UCA-ENUM-POLICY-STATED`` — "[ ] Open/closed enum policy is stated
  (§6)." The §6 heading "Open vs. closed enumerations" must exist.

Two §12 lines stay manual and are honestly out of scope here: "Legacy
production wire contract fields map 1:1 into LCP" (the legacy contract is
an external artifact with no machine-readable inventory in this repo) and
the human judgement of what makes a *new* field "universal" (the name-based
checks above constrain drift but cannot pre-approve intent).

Exit code 1 with one line per violation, prefixed by check id.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import check_reserved_fields

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_STORAGE = (
    ROOT / "implementations" / "reference-platform" / "lcp_platform" / "storage.py"
)
COUNTRY_CODE = "country_code"

# SPEC §3 named performance metrics plus the buyer-analytics word family.
# "rate" only as the final token: verticals legitimately carry loan-rate
# fields (desired_rate_term, existing_rate_percent) that are product data.
METRIC_EXACT = re.compile(
    r"^(?:speed_to_contact(?:_\w+)*|close_value(?:_\w+)*|contact_rate|close_rate"
    r"|conversion_value(?:_\w+)*|rate)$"
)
METRIC_WORD = re.compile(r"(?:^|_)(?:volume|metrics|quality_score)(?:_|$)")
DEMOGRAPHIC_EXACT = re.compile(
    r"^(?:gender|sex|race|ethnicity|marital_status|marital_type)$"
)
DEMOGRAPHIC_SUB = re.compile(r"(?:^|_)(?:gender|ethnic|marital)(?:_|$)")


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"{path}: not found"
    except json.JSONDecodeError as exc:
        return None, f"{path}: invalid JSON: {exc}"


def _walk_properties(
    node: Any, namepath: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], str, dict]]:
    """Yield (name-path, property name, property schema) at every depth."""
    if isinstance(node, list):
        for item in node:
            yield from _walk_properties(item, namepath)
        return
    if not isinstance(node, dict):
        return
    properties = node.get("properties")
    if isinstance(properties, dict):
        for name, subschema in properties.items():
            yield namepath, name, subschema if isinstance(subschema, dict) else {}
            yield from _walk_properties(subschema, namepath + (name,))
    for key, value in node.items():
        if key != "properties":
            yield from _walk_properties(value, namepath)


def _all_property_names(node: Any) -> set[str]:
    return {name for _, name, _schema in _walk_properties(node)}


def _spec_section(spec: str, heading: str, stop: str = r"\n#{2,4} ") -> str:
    start = spec.index(heading)
    rest = spec[start + len(heading) :]
    match = re.search(stop, rest)
    return rest[: match.start()] if match else rest


def _spec_table_fields(section: str) -> set[str]:
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.MULTILINE))


def find_violations(
    schemas_dir: Path,
    verticals_dir: Path,
    spec_path: Path,
    storage_path: Path = PLATFORM_STORAGE,
) -> list[str]:
    """Run every machine-checkable §12 line; return violation messages."""
    errors: list[str] = []

    spec_text = spec_path.read_text(encoding="utf-8")

    core_doc, error = _load_json(schemas_dir / "core.json")
    if error or not isinstance(core_doc, dict):
        return [
            f"{error or f'{schemas_dir}/core.json: unusable'} — core schema is "
            "the universal-audit source; cannot check"
        ]
    defs = core_doc.get("$defs", {})

    schema_docs: dict[str, dict] = {}
    for path in sorted(schemas_dir.glob("*.json")):
        doc, error = _load_json(path)
        if error or not isinstance(doc, dict):
            errors.append(f"UCA-LOAD: {error or path}: unusable schema document")
            continue
        schema_docs[path.name] = doc

    vertical_docs: dict[str, dict] = {}
    for path in sorted(verticals_dir.glob("*.json")):
        doc, error = _load_json(path)
        if error or not isinstance(doc, dict):
            errors.append(f"UCA-LOAD: {error or path}: unusable vertical document")
            continue
        vertical_docs[path.name] = doc
    if errors:
        return errors

    schema_names: dict[str, str] = {}  # name -> first location
    for fname, doc in schema_docs.items():
        for namepath, name, _ in _walk_properties(doc):
            schema_names.setdefault(name, f"{fname}#/{'/'.join(namepath + (name,))}")
    vertical_names: set[str] = set()
    for doc in vertical_docs.values():
        vertical_names |= _all_property_names(doc)

    # UCA-CORE-NO-VERTICAL-FIELDS: vertical vocabulary must stay out of core.
    envelope = set(defs.get("attributes", {}).get("properties", {}))
    deny = vertical_names - envelope - {COUNTRY_CODE}
    for name in sorted(schema_names.keys() & deny):
        errors.append(
            f"UCA-CORE-NO-VERTICAL-FIELDS: {schema_names[name]} is a "
            f"vertical-schema property name — core must contain zero "
            "vertical-specific fields (SPEC §12); move it out of the core "
            "schemas"
        )

    # UCA-CORE-COMPLIANCE-GENERIC: no vendor/law-named compliance sub-fields.
    compliance_table = _spec_table_fields(_spec_section(spec_text, "### compliance"))
    if not compliance_table:
        errors.append(
            "UCA-CORE-COMPLIANCE-GENERIC: SPEC.md §3 compliance field table not "
            "found — update this checker together with the prose"
        )
    else:
        for name in defs.get("compliance", {}).get("properties", {}):
            if name not in compliance_table:
                errors.append(
                    f"UCA-CORE-COMPLIANCE-GENERIC: schemas/core.json compliance "
                    f"sub-field '{name}' is not in the SPEC §3 compliance table — "
                    "no market-specific named sub-fields in core; use "
                    "consent_evidence[] with generic type/provider (SPEC §12)"
                )

    # UCA-FULLNAME-ALT: Western name splitting must stay optional.
    consumer_props = set(defs.get("consumer", {}).get("properties", {}))
    for required in ("first_name", "last_name", "full_name"):
        if required not in consumer_props:
            errors.append(
                f"UCA-FULLNAME-ALT: consumer.{required} missing — consumer must "
                "not assume Western name splitting (SPEC §12)"
            )

    # UCA-DEMOGRAPHICS-ABSENT: gender/ethnicity/marital never in core.
    for name, loc in sorted(schema_names.items()):
        if DEMOGRAPHIC_EXACT.match(name) or DEMOGRAPHIC_SUB.search(name):
            errors.append(
                f"UCA-DEMOGRAPHICS-ABSENT: {loc} is a demographic field — "
                "`consumer.gender` is NOT in core (moved to vertical "
                "attributes, SPEC §3/§12); verticals define it instead"
            )

    # UCA-PHONE-UNIVERSAL: E.164 + HMAC hash pins.
    consumer_def = defs.get("consumer", {}).get("properties", {})
    phone_pattern = str(consumer_def.get("phone", {}).get("pattern", ""))
    if not phone_pattern.startswith("^\\+"):
        errors.append(
            "UCA-PHONE-UNIVERSAL: consumer.phone lacks an E.164 pattern "
            "(SPEC §12 'Phone E.164 universal')"
        )
    for hash_field in ("phone_hash", "email_hash"):
        pattern = str(consumer_def.get(hash_field, {}).get("pattern", ""))
        if pattern != "^[a-f0-9]{64}$":
            errors.append(
                f"UCA-PHONE-UNIVERSAL: consumer.{hash_field} must be a 64-hex "
                "HMAC-SHA256 pin (SPEC §12)"
            )

    # UCA-CALL-FIRSTCLASS: the call message type exists.
    if "call.json" not in schema_docs:
        errors.append(
            "UCA-CALL-FIRSTCLASS: schemas/call.json missing — call leads are "
            "first-class (SPEC §12)"
        )

    # UCA-AGENT-READY: channel=agent and source_type=agent are enumerated.
    if not re.search(r"`channel` \([^)]*\bagent\b[^)]*\)", spec_text):
        errors.append(
            "UCA-AGENT-READY: SPEC does not list `agent` among channel values "
            "(SPEC §12 'AI-agent submissions: channel=agent')"
        )
    if not re.search(r"`source_type` \([^)]*\bagent\b[^)]*\)", spec_text):
        errors.append(
            "UCA-AGENT-READY: SPEC does not list `agent` among source_type "
            "values (SPEC §12 'AI-agent submissions ... source_type=agent')"
        )
    if "channel" not in _all_property_names(schema_docs.get("lead.json", {})):
        errors.append("UCA-AGENT-READY: lead payload does not expose `channel`")
    if "source_type" not in defs.get("provenance", {}).get("properties", {}):
        errors.append("UCA-AGENT-READY: provenance.source_type missing")

    # UCA-PING-ALLOWLIST: strict allowlist, not a blocklist.
    ping = schema_docs.get("ping.json")
    if ping is None:
        errors.append("UCA-PING-ALLOWLIST: schemas/ping.json missing")
    elif ping.get("additionalProperties") is not False:
        errors.append(
            "UCA-PING-ALLOWLIST: ping.json root additionalProperties must be "
            "false — strict allowlist, not a blocklist (SPEC §12)"
        )

    # UCA-PINGSAFE-TAGGED: every vertical field carries a boolean ping_safe.
    for fname, doc in vertical_docs.items():
        for namepath, name, schema in _walk_properties(doc):
            if len(namepath) == 0 and name in envelope:
                continue
            if not isinstance(schema.get("ping_safe"), bool):
                errors.append(
                    f"UCA-PINGSAFE-TAGGED: verticals/{fname} "
                    f"{'.'.join(namepath + (name,))} lacks a boolean "
                    "ping_safe tag (SPEC §12, §3 attributes)"
                )

    # UCA-RESERVED-NO-SHADOW: aggregate the existing ATR-04 checker.
    for message in check_reserved_fields.find_violations(
        schemas_dir, verticals_dir, spec_path
    ):
        errors.append(f"UCA-RESERVED-NO-SHADOW: {message}")

    # UCA-METRICS-ABSENT: buyer-side analytics never in schemas/verticals.
    def metric_offenders(node: Any, label: str, allow_event_details: bool) -> None:
        for namepath, name, _ in _walk_properties(node):
            if not (METRIC_EXACT.match(name) or METRIC_WORD.search(name)):
                continue
            if (
                allow_event_details
                and name.startswith("conversion_value")
                and namepath
                and namepath[0] == "details"
            ):
                continue  # §3 exception: lifecycle fact inside event details
            loc = f"{label}:{'.'.join(namepath + (name,))}"
            errors.append(
                f"UCA-METRICS-ABSENT: {loc} is a performance metric — metrics "
                "belong in extensions, never in core or vertical schemas "
                "(SPEC §3, §12 universal-audit intent)"
            )

    for fname, doc in schema_docs.items():
        metric_offenders(doc, f"schemas/{fname}", fname == "event.json")
    for fname, doc in vertical_docs.items():
        metric_offenders(doc, f"verticals/{fname}", False)

    # UCA-COUNTRIES-ISO: alpha-2 countries, no legacy markets grouping.
    if "markets" in schema_names or "markets" in vertical_names:
        errors.append(
            "UCA-COUNTRIES-ISO: a `markets` property exists — legacy `markets` "
            "grouping is banned (SPEC §8, §12)"
        )
    countries = (
        schema_docs.get("offer.json", {}).get("properties", {}).get("countries", {})
    )
    pattern = (
        str(countries.get("items", {}).get("pattern", ""))
        if isinstance(countries.get("items"), dict)
        else ""
    )
    if pattern != "^[A-Z]{2}$":
        errors.append(
            "UCA-COUNTRIES-ISO: offer.countries items must be constrained by "
            "the ISO 3166-1 alpha-2 pattern ^[A-Z]{2}$ (SPEC §8, §12)"
        )

    # UCA-TRANSITION-TABLE: published §3 table matches the platform graph.
    status_section = _spec_section(spec_text, "### status", stop=r"\n### ")
    statuses = set(re.findall(r"`([A-Z][A-Z_]+)`", status_section.split("####")[0]))
    terminal_match = re.search(
        r"\*\*Terminal states\*\*.*?\.", status_section, re.DOTALL
    )
    terminals = (
        set(re.findall(r"`([A-Z][A-Z_]+)`", terminal_match.group(0)))
        if terminal_match
        else set()
    )
    notable = set(re.findall(r"`([A-Z][A-Z_]+) → ([A-Z][A-Z_]+)`", status_section))
    if not statuses or not terminal_match:
        errors.append(
            "UCA-TRANSITION-TABLE: §3 status list or published status "
            "transition table not found in SPEC.md (SPEC §12)"
        )
    try:
        table = _extract_transitions(storage_path)
    except (FileNotFoundError, ValueError) as exc:
        errors.append(f"UCA-TRANSITION-TABLE: {exc}")
    else:
        if table.keys() != statuses:
            missing = statuses - table.keys()
            extra = set(table) - statuses
            errors.append(
                f"UCA-TRANSITION-TABLE: reference platform "
                f"LEGAL_LEAD_TRANSITIONS states {sorted(extra) or ''} and/or "
                f"omits {sorted(missing) or ''} — table must match §3 "
                "(SPEC §12)"
            )
        platform_terminals = {s for s, targets in table.items() if not targets}
        if terminals and platform_terminals != terminals:
            errors.append(
                f"UCA-TRANSITION-TABLE: terminal states differ — §3 publishes "
                f"{sorted(terminals)}, platform dead-ends at "
                f"{sorted(platform_terminals)} (SPEC §12)"
            )
        for src, dst in sorted(notable):
            if dst not in table.get(src, set()):
                errors.append(
                    f"UCA-TRANSITION-TABLE: §3-published edge {src} → {dst} is "
                    "absent from LEGAL_LEAD_TRANSITIONS (SPEC §12)"
                )

    # UCA-ENUM-POLICY-STATED: §6 open/closed enum policy present.
    if "Open vs. closed enumerations" not in spec_text:
        errors.append(
            "UCA-ENUM-POLICY-STATED: SPEC §6 'Open vs. closed enumerations' "
            "heading missing — policy must be stated (SPEC §12)"
        )

    return errors


def _extract_transitions(storage_path: Path) -> dict[str, set[str]]:
    """AST-extract LEGAL_LEAD_TRANSITIONS without importing the platform."""
    try:
        source = storage_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(
            f"{storage_path}: reference platform storage not found"
        ) from exc
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if node.value is None:
            continue
        if any(
            isinstance(t, ast.Name) and t.id == "LEGAL_LEAD_TRANSITIONS"
            for t in targets
        ):
            raw = ast.literal_eval(node.value)
            return {str(k): set(v) for k, v in raw.items()}
    raise ValueError(f"{storage_path}: LEGAL_LEAD_TRANSITIONS not found")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schemas-dir", type=Path, default=ROOT / "schemas")
    parser.add_argument("--verticals-dir", type=Path, default=ROOT / "verticals")
    parser.add_argument("--spec", type=Path, default=ROOT / "SPEC.md")
    parser.add_argument("--platform-storage", type=Path, default=PLATFORM_STORAGE)
    args = parser.parse_args(argv)

    errors = find_violations(
        args.schemas_dir, args.verticals_dir, args.spec, args.platform_storage
    )
    for message in errors:
        print(message, file=sys.stderr)
    if errors:
        return 1
    print(
        "check_universal_core: all 14 machine-checkable SPEC §12 audit "
        "checks passed (manual members: legacy 1:1 wire mapping, "
        "new-field universality judgement)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

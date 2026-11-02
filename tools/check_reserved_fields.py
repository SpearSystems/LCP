#!/usr/bin/env python3
"""Gate: vertical attribute schemas must not shadow core field names (SPEC §3/§12).

The reserved-name set is DERIVED from ``schemas/core.json`` — every property
name at every depth of the core schema — not from the prose list, so adding a
core field automatically extends the blocklist (drift in the schema cannot
quietly shrink enforcement). The §3 prose list is cross-checked against the
derived set as a tripwire: if a name the SPEC calls reserved disappears from
``core.json``, that too fails.

Two exemptions, both mandated by the same section they guard (SPEC §3,
AGENTS.md rules 6-7):

* ``vertical`` / ``schema_version`` — the attributes envelope keys, exactly
  the property names declared by core's ``attributes`` def — are REQUIRED at
  the root of every vertical schema and legal nowhere else.
* ``country_code`` — the discriminator every country-scoped object MUST
  carry — is legal nested (inside a country-scoped object), illegal at the
  attributes root where it would shadow ``core.json location.country_code``.

Everything else in the derived set is rejected at any depth, including
inside nested objects, combinator branches (``allOf``/``anyOf``/``oneOf``),
array ``items``, ``additionalProperties`` and ``patternProperties``.

Exit code 1 with one line per violation; ``find_violations()`` is embedded
by ``tools/check_sdk_schema_sync.py`` (pre-hash) and
``tools/generate_sdk_models.py`` (pre-write) so the violation can neither
pass ``--check`` after a regeneration re-hash nor propagate into any SDK
bundle (issue #134).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SPEC_RESERVED_RE = re.compile(
    r"MUST NOT redefine core field names \((.*?)\)", re.DOTALL
)
BACKTICK_RE = re.compile(r"`([a-z0-9_]+)`")

COUNTRY_CODE = "country_code"


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"{path}: not found"
    except json.JSONDecodeError as exc:
        return None, f"{path}: invalid JSON: {exc}"


def reserved_from_core(core_doc: dict) -> tuple[dict[str, str], set[str]]:
    """Walk every ``properties`` map at any depth in the core schema.

    Returns (name -> first schema-location-of-definition, envelope names).
    Envelope names are the property names of core's own ``attributes`` def:
    the vertical/schema_version discriminators that every attributes object
    must carry.
    """
    reserved: dict[str, str] = {}

    def walk(node: Any, loc: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, loc + (str(index),))
            return
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, subschema in properties.items():
                reserved.setdefault(name, "#/" + "/".join(loc + ("properties", name)))
                walk(subschema, loc + ("properties", name))
        for key, value in node.items():
            if key != "properties":
                walk(value, loc + (key,))

    walk(core_doc, ())
    attributes_def = core_doc.get("$defs", {}).get("attributes", {})
    envelope = set(attributes_def.get("properties", {}))
    return reserved, envelope


def scan_vertical(
    doc: Any, reserved: dict[str, str], envelope: set[str], file_label: str
) -> list[str]:
    """Every attributes property name at any depth that shadows a core field."""
    violations: list[str] = []

    def walk(node: Any, namepath: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, namepath)
            return
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            depth = len(namepath)
            for name, subschema in properties.items():
                path = ".".join(namepath + (name,))
                exempt = (depth == 0 and name in envelope) or (
                    depth >= 1 and name == COUNTRY_CODE
                )
                if name in reserved and not exempt:
                    violations.append(
                        f"{file_label}: attributes.{path} redefines reserved "
                        f"core field '{name}' (schemas/core.json {reserved[name]}) "
                        "— vertical schemas MUST NOT redefine core field names "
                        "in attributes (SPEC §3)"
                    )
                walk(subschema, namepath + (name,))
        for key, value in node.items():
            if key != "properties":
                walk(value, namepath)

    walk(doc, ())
    return violations


def spec_prose_names(spec_text: str) -> list[str] | str:
    """The backtick names in the SPEC §3 'MUST NOT redefine' sentence."""
    match = SPEC_RESERVED_RE.search(spec_text)
    if not match:
        return (
            "SPEC.md: §3 reserved-core-field sentence "
            "('MUST NOT redefine core field names (...)') not found — "
            "update this checker together with the prose"
        )
    return BACKTICK_RE.findall(match.group(1))


def find_violations(
    schemas_dir: Path, verticals_dir: Path, spec_path: Path
) -> list[str]:
    """All reserved-field violations plus core/prose drift, as messages."""
    errors: list[str] = []

    core_path = schemas_dir / "core.json"
    core_doc, error = _load_json(core_path)
    if error:
        return [f"{error} — core schema is the reserved-name source; cannot check"]
    reserved, envelope = reserved_from_core(core_doc)
    if not reserved:
        return [
            f"{core_path}: no core property names derived — schemas/core.json is "
            "not a usable reserved-name source"
        ]

    prose = spec_prose_names(spec_path.read_text(encoding="utf-8"))
    if isinstance(prose, str):
        errors.append(prose)
    else:
        for name in prose:
            if name not in reserved:
                errors.append(
                    f"{core_path}: SPEC §3 lists core field '{name}' as reserved, "
                    "but schemas/core.json no longer defines a property of that "
                    "name — core drift vs SPEC prose (update one or the other)"
                )

    vertical_files = sorted(verticals_dir.glob("*.json"))
    if not vertical_files:
        errors.append(
            f"{verticals_dir}: no vertical schemas found — refusing to certify "
            "an empty verticals directory"
        )
    for path in vertical_files:
        doc, error = _load_json(path)
        if error:
            errors.append(error)
            continue
        errors.extend(scan_vertical(doc, reserved, envelope, path.name))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schemas-dir", default=str(ROOT / "schemas"))
    parser.add_argument("--verticals-dir", default=str(ROOT / "verticals"))
    parser.add_argument("--spec", default=str(ROOT / "SPEC.md"))
    options = parser.parse_args()
    errors = find_violations(
        Path(options.schemas_dir), Path(options.verticals_dir), Path(options.spec)
    )
    if errors:
        print("Reserved-core-field check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    core_doc, _ = _load_json(Path(options.schemas_dir) / "core.json")
    reserved, _ = reserved_from_core(core_doc)
    print(
        f"Reserved-core-field check passed: no vertical attribute shadows any of "
        f"the {len(reserved)} core field names derived from schemas/core.json"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

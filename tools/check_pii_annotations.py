#!/usr/bin/env python3
"""Gate: every schema property carries an ``x-lcp-pii`` class (D9/LEP-0011).

``docs/PII-CLASSES.md`` is normative for *which* class a field takes; this
checker is the coverage half of D9's rollout step 3 — a property without a
class cannot merge, so the count stays at zero instead of becoming a default.

Scope: every property definition under ``schemas/*.json`` and
``verticals/*.json``, plus every ``$defs`` entry in those documents (a
``$ref`` consumers inherits its definition's class, so the definition is the
thing that must be classed). A property whose definition is only
``{"$ref": ...}`` is exempt by the same rule: the annotation lives on the
definition it points at, never on the reference site, so ``lead.consumer`` and
``post.consumer`` cannot drift apart.

Failures: an unannotated definition, or a class value outside the five names.
``computed`` is legal on ``envelope.json``'s ``payload`` and nowhere else.
``prohibited`` is a *valid* value here — the class exists so this coverage
number can distinguish "deliberately refused" from "not yet classified"; the
load-time validator is what refuses such a property.

Exit code 1 with one line per violation and the violation count.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

PII_KEY = "x-lcp-pii"
# Least to most protective; the order is the no-downgrade ladder and is not
# used here beyond naming the vocabulary (docs/PII-CLASSES.md).
PII_CLASSES = ("public", "internal", "contact", "restricted", "prohibited")
PII_COMPUTED = "computed"
COMPUTED_ALLOWED = "envelope.json:lcp.payload"


def _load_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"{path}: not found"
    except json.JSONDecodeError as exc:
        return None, f"{path}: invalid JSON: {exc}"


def definitions(document: Any) -> list[tuple[str, Any]]:
    """Every property definition and ``$defs`` entry, with its dotted path.

    ``properties`` is transparent (``consumer.phone``), a ``$defs`` entry
    appears as ``$defs.<name>...`` and array positions as ``[i]``.
    """

    found: list[tuple[str, Any]] = []

    def walk(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, path + (f"[{index}]",))
            return
        if not isinstance(node, dict):
            return
        declared_defs = node.get("$defs")
        if isinstance(declared_defs, dict):
            for name, definition in declared_defs.items():
                found.append((".".join(path + ("$defs", name)), definition))
                walk(definition, path + ("$defs", name))
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, definition in properties.items():
                found.append((".".join(path + (name,)), definition))
                walk(definition, path + (name,))
        for key, value in node.items():
            if key in {"properties", "$defs"}:
                continue
            walk(value, path + (key,))

    walk(document, ())
    return found


@dataclass
class Audit:
    violations: list[str] = field(default_factory=list)
    annotated: int = 0
    unannotated: int = 0
    inherited: int = 0


def audit_document(document: Any, label: str, audit: Audit) -> None:
    """Classify every definition in one schema document into ``audit``."""

    for path, definition in definitions(document):
        if not isinstance(definition, dict):
            audit.unannotated += 1
            audit.violations.append(
                f"{label}: {path} is a boolean schema with no {PII_KEY} class"
            )
            continue
        if set(definition) == {"$ref"}:
            audit.inherited += 1
            continue
        declared = definition.get(PII_KEY)
        if declared is None:
            audit.unannotated += 1
            audit.violations.append(
                f"{label}: {path} has no {PII_KEY} annotation "
                f"(docs/PII-CLASSES.md is the class source)"
            )
            continue
        if declared == PII_COMPUTED:
            if f"{label}:{path}" == COMPUTED_ALLOWED:
                audit.annotated += 1
                continue
        elif declared in PII_CLASSES:
            audit.annotated += 1
            continue
        audit.violations.append(
            f"{label}: {path} declares {PII_KEY} {declared!r}, which is not one "
            f"of {', '.join(PII_CLASSES)}"
            + (
                ""
                if declared == PII_COMPUTED
                else f" (or {PII_COMPUTED!r} on {COMPUTED_ALLOWED})"
            )
        )


def find_violations(schemas_dir: Path, verticals_dir: Path) -> Audit:
    """Audit both schema roots; an empty directory is itself a failure."""

    audit = Audit()
    for directory in (schemas_dir, verticals_dir):
        files = sorted(directory.glob("*.json"))
        if not files:
            audit.violations.append(
                f"{directory}: no schema documents found — refusing to certify "
                "an empty directory"
            )
            continue
        for path in files:
            document, error = _load_json(path)
            if error:
                audit.violations.append(error)
                continue
            audit_document(document, path.name, audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Every schema property must carry an x-lcp-pii class (D9/LEP-0011)"
    )
    parser.add_argument("--schemas-dir", default=str(ROOT / "schemas"))
    parser.add_argument("--verticals-dir", default=str(ROOT / "verticals"))
    options = parser.parse_args()
    audit = find_violations(Path(options.schemas_dir), Path(options.verticals_dir))
    if audit.violations:
        print(
            f"PII-annotation check failed: {len(audit.violations)} violation(s) "
            f"({audit.unannotated} unannotated definition(s), "
            f"{audit.annotated} annotated, {audit.inherited} inherited via $ref)"
        )
        for violation in audit.violations:
            print(f"- {violation}")
        return 1
    print(
        f"PII-annotation check passed: {audit.annotated} definitions carry "
        f"{PII_KEY}, {audit.inherited} inherit their class through $ref, "
        "0 unannotated"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

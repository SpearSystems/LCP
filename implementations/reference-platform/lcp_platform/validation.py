"""JSON Schema validation for the reference platform."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


MESSAGE_TYPES = ("lead", "call", "ping", "post", "ack", "event", "bid")

# §6/§8: the LCP protocol versions this platform supports and advertises.
# ONE source for the §8 capabilities advertisement and the intake version
# negotiation (§5 LCP-007 SCHEMA_VERSION_UNSUPPORTED ↔ 422, issue #181) so
# what the wire endpoint accepts can never drift from what it advertises.
# Every element is a full major.minor.patch semver (§8).
SUPPORTED_LCP_VERSIONS = ("1.0.0", "1.1.0")

# §3 "phone | string | E.164 format." — mirrors the published
# schemas/core.json $defs/consumer pattern (^\+[1-9]\d{1,14}$) so the
# intake-side §5 re-code (LCP-009 INVALID_PHONE ↔ 400, issue #176) checks
# the same value axis the schema pins structurally.
E164_PATTERN = re.compile(r"\+[1-9]\d{1,14}")

# ISO 3166-1 alpha-2 officially assigned code elements (249; retrieved
# 2026-11-03 from the ISO 3166-1 alpha-2 code list). User-assigned
# elements (XK, EU, XX, ...) are deliberately excluded — §3 requires an
# "ISO 3166-1 alpha-2" country code. Backs the §5 LCP-010 INVALID_TERRITORY
# ↔ 422 intake check for pattern-valid-but-unassigned codes (issue #182).
_ISO_3166_1_ALPHA2_TEXT = """
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM
IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK
LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS
ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
"""
ISO_3166_1_ALPHA2 = frozenset(_ISO_3166_1_ALPHA2_TEXT.split())

# D7/v1.2 wire surface + v1.0/v1.1 registrations from
# governance/EXTENSION-REGISTRY.md. The value states whether the registration
# declares the namespace ping-safe. A ping carries `extensions` only for a
# registered, ping-safe namespace; every other key is refused by name.
# Unregistered namespaces stay legal on `lead`/`post` (forward compatibility),
# but never on a ping: the strict allowlist is the PII guard.
EXTENSION_REGISTRY: dict[str, bool] = {
    "lcp.platform.requirements": False,
    "lcp.platform.service_area": False,
    "lcp.marketplace.final_pricing": False,
    "lcp.form.questions": False,
    "lcp.outbound.call_request": False,
    "lcp.platform.assessment": True,
    "lcp.buyer.criteria": False,
    "lcp.intake.raw_form": False,
}


def default_schema_root() -> Path:
    configured = os.environ.get("LCP_SCHEMA_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "schemas"


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class SchemaValidator:
    def __init__(self, schema_root: str | Path | None = None):
        self.schema_root = Path(schema_root) if schema_root else default_schema_root()
        self.vertical_root = self.schema_root.parent / "verticals"
        core = self._load("core")
        resource = Resource(contents=core, specification=DRAFT202012)
        registry = (
            Registry()
            .with_resource(uri="https://lcp.dev/schemas/core.json", resource=resource)
            .with_resource(uri="core.json", resource=resource)
        )
        format_checker = FormatChecker()
        self._validators = {
            name: Draft202012Validator(
                self._load(name), registry=registry, format_checker=format_checker
            )
            for name in MESSAGE_TYPES
        }
        self._envelope = Draft202012Validator(
            self._load("envelope"), registry=registry, format_checker=format_checker
        )
        self._offer = Draft202012Validator(
            self._load("offer"), registry=registry, format_checker=format_checker
        )
        self._vertical_validators: dict[str, Draft202012Validator] = {}
        for path in sorted(self.vertical_root.glob("*.json")):
            with path.open(encoding="utf-8") as handle:
                self._vertical_validators[path.stem] = Draft202012Validator(
                    json.load(handle), format_checker=format_checker
                )

    def _load(self, name: str) -> dict[str, Any]:
        with (self.schema_root / f"{name}.json").open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _format(error: Any) -> str:
        path = "/".join(str(part) for part in error.path)
        return f"{error.message} at {path or '(root)'}"

    def validate_envelope(self, envelope: dict[str, Any]) -> list[str]:
        errors = [self._format(error) for error in self._envelope.iter_errors(envelope)]
        if errors:
            return errors
        message = envelope["lcp"]["message"]
        errors.extend(
            self._format(error)
            for error in self._validators[message["type"]].iter_errors(
                envelope["lcp"]["payload"]
            )
        )
        if message["type"] in {"lead", "call", "post", "ping"}:
            errors.extend(
                self._vertical_errors(
                    envelope["lcp"]["payload"],
                    message_type=message["type"],
                )
            )
        if message["type"] == "ping":
            errors.extend(self._ping_extension_errors(envelope["lcp"]["payload"]))
        return errors

    def require_valid_envelope(self, envelope: dict[str, Any]) -> None:
        errors = self.validate_envelope(envelope)
        if errors:
            raise ValidationError(errors)

    def require_valid_offer(self, offer: dict[str, Any]) -> None:
        errors = [self._format(error) for error in self._offer.iter_errors(offer)]
        if errors:
            raise ValidationError(errors)

    def _vertical_errors(
        self,
        payload: dict[str, Any],
        *,
        message_type: str,
    ) -> list[str]:
        raw_attributes = payload.get("attributes")
        attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
        vertical = (
            payload.get("vertical")
            if message_type == "ping"
            else attributes.get("vertical")
        )
        if not isinstance(vertical, str) or not vertical:
            return ["vertical is required for vertical validation"]
        validator = self._vertical_validators.get(vertical)
        if validator is None:
            return [f"vertical schema '{vertical}' was not found"]

        vertical_attributes = dict(attributes)
        if message_type == "ping":
            vertical_attributes.setdefault("vertical", vertical)
            version_schema = validator.schema.get("properties", {}).get(
                "schema_version", {}
            )
            vertical_attributes.setdefault(
                "schema_version", version_schema.get("const", "1.0.0")
            )
        errors = [
            self._format(error) for error in validator.iter_errors(vertical_attributes)
        ]
        if message_type == "ping":
            errors.extend(self._ping_safe_errors(payload, validator.schema))
        return errors

    def _ping_extension_errors(self, payload: dict[str, Any]) -> list[str]:
        """v1.2/D7: a ping admits only registered, ping-safe namespaces.

        The schema bounds the container shape; what a namespace *is* remains a
        receiver rule (LEP-0006 §B.1). Refuse by name so the sender can see
        which namespace failed admission — never silently drop the block.
        """
        extensions = payload.get("extensions")
        if not isinstance(extensions, dict) or not extensions:
            return []
        errors: list[str] = []
        for namespace in sorted(extensions):
            ping_safe = EXTENSION_REGISTRY.get(namespace)
            if ping_safe is None:
                errors.append(
                    f"extensions.{namespace} is not a registered extension "
                    "namespace; a ping admits only registered, ping-safe "
                    "namespaces (governance/EXTENSION-REGISTRY.md)"
                )
            elif not ping_safe:
                errors.append(
                    f"extensions.{namespace} is registered but not ping-safe; "
                    "a ping admits only registrations that declare ping safety"
                )
        return errors

    def _ping_safe_errors(
        self,
        payload: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> list[str]:
        vertical = payload.get("vertical")
        attributes = payload.get("attributes", {})
        if not isinstance(vertical, str) or not isinstance(attributes, dict):
            return []
        if schema is None:
            validator = self._vertical_validators.get(vertical)
            if validator is None:
                return [
                    f"vertical schema '{vertical}' not found for ping-safe validation"
                ]
            schema = validator.schema

        errors: list[str] = []

        def walk(value: Any, definition: dict[str, Any], path: str) -> None:
            if not isinstance(value, dict):
                return
            properties = definition.get("properties", {})
            for field_name, field_value in value.items():
                if path == "attributes" and field_name in {
                    "vertical",
                    "schema_version",
                }:
                    continue
                field_definition = properties.get(field_name)
                field_path = f"{path}.{field_name}"
                if (
                    not isinstance(field_definition, dict)
                    or field_definition.get("ping_safe") is not True
                ):
                    errors.append(
                        f"{field_path} is not tagged ping_safe: true in vertical '{vertical}'"
                    )
                    continue
                if isinstance(field_definition.get("properties"), dict):
                    walk(field_value, field_definition, field_path)

        walk(attributes, schema, "attributes")
        return errors

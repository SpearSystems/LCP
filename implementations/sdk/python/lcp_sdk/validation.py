"""JSON Schema validation for LCP messages."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


MESSAGE_TYPES = ("lead", "call", "ping", "post", "ack", "event", "bid")


def default_schema_root() -> Path:
    """Find the repository schemas when running from a source checkout."""
    configured = os.environ.get("LCP_SCHEMA_DIR")
    if configured:
        return Path(configured)
    bundled = Path(__file__).resolve().parent / "schemas"
    if bundled.exists():
        return bundled
    # implementations/sdk/python/lcp_sdk -> repository root / schemas
    return Path(__file__).resolve().parents[4] / "schemas"


class ValidationError(ValueError):
    """Raised when a message fails LCP schema validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class SchemaValidator:
    """Validate LCP envelopes and payloads against repository schemas."""

    def __init__(self, schema_root: str | Path | None = None):
        self.schema_root = Path(schema_root) if schema_root else default_schema_root()
        self.vertical_root = self.schema_root.parent / "verticals"
        core = self._load("core")
        core_resource = Resource(contents=core, specification=DRAFT202012)
        registry = Registry().with_resource(
            uri="https://lcp.dev/schemas/core.json", resource=core_resource
        ).with_resource(uri="core.json", resource=core_resource)
        self._registry = registry
        self._schemas: dict[str, dict[str, Any]] = {
            name: self._load(name) for name in ("core", "envelope", "offer", *MESSAGE_TYPES)
        }
        self._validators = {
            name: Draft202012Validator(schema, registry=registry)
            for name, schema in self._schemas.items()
            if name != "core"
        }
        self._envelope = self._validators["envelope"]
        self._offer = self._validators["offer"]
        self._verticals: dict[str, Draft202012Validator] = {}
        if self.vertical_root.exists():
            for path in self.vertical_root.glob("*.json"):
                self._verticals[path.stem] = Draft202012Validator(
                    json.loads(path.read_text(encoding="utf-8")), registry=registry
                )

    def _load(self, name: str) -> dict[str, Any]:
        path = self.schema_root / f"{name}.json"
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _format_error(error: Any) -> str:
        path = "/".join(str(part) for part in error.path)
        return f"{error.message} at {path or '(root)'}"

    @classmethod
    def from_bundle(cls, bundle: dict[str, dict[str, Any]]) -> "SchemaValidator":
        """Build a validator from the generated ``schema-bundle.json`` object."""
        instance = cls.__new__(cls)
        instance.schema_root = Path(".")
        instance.vertical_root = Path(".")
        core = bundle.get("schemas/core.json") or bundle.get("core.json")
        if core is None:
            raise ValueError("schema bundle does not contain schemas/core.json")
        resource = Resource(contents=core, specification=DRAFT202012)
        registry = Registry().with_resource(uri="https://lcp.dev/schemas/core.json", resource=resource).with_resource(uri="core.json", resource=resource)
        instance._registry = registry
        instance._schemas = {
            key.removeprefix("schemas/").removesuffix(".json"): value
            for key, value in bundle.items()
            if key.startswith("schemas/")
        }
        instance._validators = {
            name: Draft202012Validator(schema, registry=registry)
            for name, schema in instance._schemas.items()
            if name != "core"
        }
        instance._envelope = instance._validators["envelope"]
        instance._offer = instance._validators["offer"]
        instance._verticals = {
            key.removeprefix("verticals/").removesuffix(".json"): Draft202012Validator(value, registry=registry)
            for key, value in bundle.items()
            if key.startswith("verticals/")
        }
        return instance

    def validate(self, schema_name: str, value: Any) -> list[str]:
        """Validate arbitrary data against a canonical core/message/vertical schema."""
        normalized = schema_name.removeprefix("schemas/").removesuffix(".json")
        validator = self._validators.get(normalized) or self._verticals.get(
            schema_name.removeprefix("verticals/").removesuffix(".json")
        )
        if validator is None:
            raise KeyError(f"unknown LCP schema: {schema_name}")
        return [self._format_error(error) for error in validator.iter_errors(value)]

    def require_valid(self, schema_name: str, value: Any) -> None:
        errors = self.validate(schema_name, value)
        if errors:
            raise ValidationError(errors)

    def validate_envelope(self, envelope: dict[str, Any]) -> list[str]:
        """Return validation errors for an envelope and its message payload."""
        errors = [self._format_error(error) for error in self._envelope.iter_errors(envelope)]
        if errors:
            return errors
        message = envelope["lcp"]["message"]
        message_type = message["type"]
        payload = envelope["lcp"]["payload"]
        errors.extend(
            self._format_error(error)
            for error in self._validators[message_type].iter_errors(payload)
        )
        if message_type == "ping":
            errors.extend(self._ping_safe_errors(payload))
        return errors

    def require_valid_envelope(self, envelope: dict[str, Any]) -> None:
        errors = self.validate_envelope(envelope)
        if errors:
            raise ValidationError(errors)

    def validate_offer(self, offer: dict[str, Any]) -> list[str]:
        return self.validate("offer", offer)

    def require_valid_offer(self, offer: dict[str, Any]) -> None:
        errors = self.validate_offer(offer)
        if errors:
            raise ValidationError(errors)

    def _ping_safe_errors(self, payload: dict[str, Any]) -> list[str]:
        attributes = payload.get("attributes", {})
        vertical = payload.get("vertical")
        if not attributes or not vertical:
            return []
        vertical_path = self.vertical_root / f"{vertical}.json"
        if not vertical_path.exists():
            return [f"vertical schema '{vertical}' not found for ping-safe validation"]
        with vertical_path.open(encoding="utf-8") as handle:
            schema = json.load(handle)
        errors: list[str] = []
        for field_name in attributes:
            definition = schema.get("properties", {}).get(field_name, {})
            if definition.get("ping_safe") is not True:
                errors.append(
                    f"attributes.{field_name} is not tagged ping_safe: true "
                    f"in vertical '{vertical}'"
                )
        return errors

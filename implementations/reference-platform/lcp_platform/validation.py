"""JSON Schema validation for the reference platform."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


MESSAGE_TYPES = ("lead", "call", "ping", "post", "ack", "event", "bid")


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
        registry = Registry().with_resource(
            uri="https://lcp.dev/schemas/core.json", resource=resource
        ).with_resource(uri="core.json", resource=resource)
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
            for error in self._validators[message["type"]].iter_errors(envelope["lcp"]["payload"])
        )
        if message["type"] == "ping":
            errors.extend(self._ping_safe_errors(envelope["lcp"]["payload"]))
        elif message["type"] in {"lead", "call", "post"}:
            errors.extend(self._vertical_errors(envelope["lcp"]["payload"]))
        return errors

    def require_valid_envelope(self, envelope: dict[str, Any]) -> None:
        errors = self.validate_envelope(envelope)
        if errors:
            raise ValidationError(errors)

    def require_valid_offer(self, offer: dict[str, Any]) -> None:
        errors = [self._format(error) for error in self._offer.iter_errors(offer)]
        if errors:
            raise ValidationError(errors)

    def _vertical_errors(self, payload: dict[str, Any]) -> list[str]:
        attributes = payload.get("attributes", {})
        vertical = attributes.get("vertical") if isinstance(attributes, dict) else None
        if not vertical:
            return ["attributes.vertical is required for vertical validation"]
        validator = self._vertical_validators.get(str(vertical))
        if not validator:
            return [f"vertical schema '{vertical}' was not found"]
        return [self._format(error) for error in validator.iter_errors(attributes)]

    def _ping_safe_errors(self, payload: dict[str, Any]) -> list[str]:
        vertical = payload.get("vertical")
        attributes = payload.get("attributes", {})
        if not vertical or not attributes:
            return []
        path = self.vertical_root / f"{vertical}.json"
        if not path.exists():
            return [f"vertical schema '{vertical}' not found for ping-safe validation"]
        with path.open(encoding="utf-8") as handle:
            schema = json.load(handle)
        errors: list[str] = []
        for field in attributes:
            if schema.get("properties", {}).get(field, {}).get("ping_safe") is not True:
                errors.append(f"attributes.{field} is not ping_safe in vertical '{vertical}'")
        return errors

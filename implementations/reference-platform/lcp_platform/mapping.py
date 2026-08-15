"""Declarative publisher form mapping and normalization.

Publisher systems are allowed to be messy; the LCP wire contract is not. This
module keeps source-specific mapping in versioned data rather than executable
callbacks. Only a small allowlisted transform set is supported so an operator
cannot accidentally turn a mapping file into an arbitrary code execution
surface.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from .messages import _envelope
from .storage import now_iso


class MappingError(ValueError):
    """Raised when a mapping cannot safely normalize a source record."""


_SUPPORTED_TRANSFORMS = {
    "trim",
    "lower",
    "upper",
    "string",
    "integer",
    "boolean",
    "e164",
    "date_time",
}
_COUNTRY_CALLING_CODES = {
    "AU": "61",
    "CA": "1",
    "GB": "44",
    "NZ": "64",
    "US": "1",
}


def source_digest(source_record: Mapping[str, Any]) -> str:
    """Return a stable audit digest without retaining the source payload."""
    encoded = json.dumps(source_record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _get_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.removeprefix("payload.").split(".")
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _normalize_e164(value: Any, country_code: str | None) -> str:
    if not isinstance(value, str):
        raise MappingError("phone value must be a string")
    raw = value.strip()
    if raw.startswith("+"):
        digits = "+" + re.sub(r"[^0-9]", "", raw[1:])
    else:
        digits = re.sub(r"[^0-9]", "", raw)
        calling_code = _COUNTRY_CALLING_CODES.get(country_code or "")
        if not calling_code:
            raise MappingError("a country_code is required to normalize a non-E.164 phone")
        digits = digits.removeprefix("0")
        digits = "+" + calling_code + digits
    if not re.fullmatch(r"\+[1-9]\d{1,14}", digits):
        raise MappingError("phone could not be normalized to E.164")
    return digits


def _normalize_value(value: Any, transform: str | None, *, country_code: str | None) -> Any:
    if value is None or transform is None:
        return value
    if transform not in _SUPPORTED_TRANSFORMS:
        raise MappingError(f"unsupported mapping transform: {transform}")
    if transform == "trim":
        return value.strip() if isinstance(value, str) else value
    if transform == "lower":
        return value.lower() if isinstance(value, str) else value
    if transform == "upper":
        return value.upper() if isinstance(value, str) else value
    if transform == "string":
        return str(value)
    if transform == "integer":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise MappingError(f"value {value!r} is not an integer") from exc
    if transform == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "yes", "y", "1", "on"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"false", "no", "n", "0", "off"}:
            return False
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        raise MappingError(f"value {value!r} is not a boolean")
    if transform == "e164":
        return _normalize_e164(value, country_code)
    if transform == "date_time":
        if not isinstance(value, str):
            raise MappingError("date_time values must be strings")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MappingError(f"invalid date-time value: {value!r}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


class MappingRegistry:
    """In-memory registry for versioned publisher/form mapping documents."""

    def __init__(self, mappings: list[dict[str, Any]] | None = None):
        self._mappings: dict[tuple[str, str, str], dict[str, Any]] = {}
        for mapping in mappings or []:
            self.register(mapping)

    @classmethod
    def from_json(cls, document: Mapping[str, Any] | list[Mapping[str, Any]]) -> "MappingRegistry":
        if isinstance(document, list):
            values = document
        elif isinstance(document, Mapping) and isinstance(document.get("mappings"), list):
            values = document["mappings"]
        elif isinstance(document, Mapping):
            values = [document]
        else:
            raise MappingError("mapping document must be an object or mappings array")
        return cls([dict(value) for value in values])

    def register(self, mapping: Mapping[str, Any]) -> None:
        required = ("mapping_id", "publisher_id", "form_key", "version", "vertical", "channel")
        missing = [field for field in required if not mapping.get(field)]
        if missing:
            raise MappingError(f"mapping is missing required fields: {', '.join(missing)}")
        if mapping["channel"] not in {"form", "chat", "click", "api", "agent", "referral", "call"}:
            raise MappingError("mapping channel is not supported")
        field_map = mapping.get("field_map", {})
        if not isinstance(field_map, Mapping):
            raise MappingError("field_map must be an object")
        transforms = mapping.get("transforms", {})
        if not isinstance(transforms, Mapping):
            raise MappingError("transforms must be an object")
        for transform in transforms.values():
            if transform not in _SUPPORTED_TRANSFORMS:
                raise MappingError(f"unsupported mapping transform: {transform}")
        key = (str(mapping["publisher_id"]), str(mapping["form_key"]), str(mapping["version"]))
        self._mappings[key] = deepcopy(dict(mapping))

    def resolve(self, publisher_id: str, form_key: str, version: str | None = None) -> dict[str, Any]:
        candidates = [
            mapping
            for (publisher, form, mapping_version), mapping in self._mappings.items()
            if publisher == publisher_id
            and form == form_key
            and (version is None or mapping_version == version)
            and mapping.get("active", True) is True
        ]
        if not candidates:
            raise MappingError(f"no active mapping for {publisher_id}/{form_key}")
        candidates.sort(key=lambda mapping: str(mapping["version"]), reverse=True)
        return deepcopy(candidates[0])

    def all(self) -> list[dict[str, Any]]:
        return [deepcopy(value) for value in self._mappings.values()]


class PublisherNormalizer:
    """Apply one registry mapping to a source record and build an LCP envelope."""

    def normalize(
        self,
        source_record: Mapping[str, Any],
        mapping: Mapping[str, Any],
        *,
        receiver_id: str,
        test: bool = False,
    ) -> dict[str, Any]:
        mapping = dict(mapping)
        field_map = mapping.get("field_map", {})
        transforms = mapping.get("transforms", {})
        value_maps = mapping.get("value_maps", {})
        payload: dict[str, Any] = {}

        country_code = mapping.get("country_code")
        country_path = field_map.get("location.country_code")
        if country_path:
            country_code = _get_path(source_record, str(country_path)) or country_code
            country_code = str(country_code).upper() if country_code else None

        for target, source_spec in field_map.items():
            if isinstance(source_spec, Mapping):
                source_path = source_spec.get("path")
                transform = source_spec.get("transform")
            else:
                source_path = source_spec
                transform = transforms.get(target)
            if not isinstance(source_path, str) or not source_path:
                raise MappingError(f"field_map entry {target!r} must name a source path")
            value = _get_path(source_record, source_path)
            if value is None:
                continue
            value = _normalize_value(value, transform, country_code=country_code)
            choices = value_maps.get(target)
            if choices is not None:
                if not isinstance(choices, Mapping):
                    raise MappingError(f"value_maps.{target} must be an object")
                if isinstance(value, list):
                    value = [choices.get(str(item), item) for item in value]
                else:
                    value = choices.get(str(value), value)
            _set_path(payload, target, value)

        constants = mapping.get("constants", {})
        if not isinstance(constants, Mapping):
            raise MappingError("constants must be an object")
        for target, value in constants.items():
            _set_path(payload, str(target), deepcopy(value))

        attributes = payload.setdefault("attributes", {})
        attributes.setdefault("vertical", mapping["vertical"])
        attributes.setdefault("schema_version", mapping.get("schema_version", "1.0.0"))
        if "lead_id" not in payload:
            source_lead_id = _get_path(source_record, "lead_id") or _get_path(source_record, "id")
            if source_lead_id is not None:
                payload["lead_id"] = source_lead_id
        if "external_id" not in payload:
            source_external_id = _get_path(source_record, "external_id") or _get_path(source_record, "id")
            if source_external_id is not None:
                payload["external_id"] = source_external_id
        payload.setdefault("status", "NEW")
        payload.setdefault("channel", mapping["channel"])
        if not payload.get("lead_id"):
            raise MappingError("mapping must produce a stable lead_id from the source record")
        if "submitted_at" not in payload:
            payload["submitted_at"] = now_iso()

        provenance = payload.setdefault("provenance", {})
        provenance.setdefault("source_type", "publisher")
        provenance.setdefault("source_id", mapping["publisher_id"])
        provenance.setdefault("brand_id", mapping.get("brand_id", mapping["publisher_id"]))
        provenance.setdefault("form_id", mapping["form_key"])
        provenance.setdefault("flow_key", mapping["form_key"])
        provenance.setdefault("received_at", now_iso())

        otp = mapping.get("otp", {})
        if otp:
            if not isinstance(otp, Mapping):
                raise MappingError("otp mapping must be an object")
            verified = _get_path(source_record, str(otp.get("verified_path", ""))) if otp.get("verified_path") else None
            if verified is not None:
                verified = _normalize_value(verified, "boolean", country_code=country_code)
                compliance = payload.setdefault("compliance", {})
                compliance["otp_verified"] = verified
                if verified:
                    otp_payload: dict[str, Any] = {}
                    for output, source_key in (
                        ("channel", "channel_path"),
                        ("verified_at", "verified_at_path"),
                        ("verified_value_hash", "value_hash_path"),
                        ("attempts", "attempts_path"),
                    ):
                        if otp.get(source_key):
                            value = _get_path(source_record, str(otp[source_key]))
                            if value is not None:
                                otp_payload[output] = value
                    if otp_payload:
                        compliance["otp"] = otp_payload
                    quality = payload.setdefault("lead_quality", {})
                    quality["verified_phone"] = otp.get("verified_field", "phone") == "phone"
                    quality.setdefault("verification", {})["phone_method"] = otp.get("method", "otp")
                    if otp_payload.get("verified_at"):
                        quality["verification"]["phone_verified_at"] = otp_payload["verified_at"]

        message_type = "call" if mapping["channel"] == "call" else "lead"
        return _envelope(
            message_type,
            str(mapping["publisher_id"]),
            receiver_id,
            payload,
            idempotency_key=f"{mapping['publisher_id']}-{payload['lead_id']}-{mapping['version']}",
            test=test,
        )

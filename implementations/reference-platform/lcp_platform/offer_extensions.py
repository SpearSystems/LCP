"""Safe, deployment-scoped offer extension matching."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


REQUIREMENTS_NAMESPACE = "lcp.platform.requirements"
SERVICE_AREA_NAMESPACE = "lcp.platform.service_area"
_ALLOWED_PATH_ROOTS = {"attributes", "location", "provenance"}
_ALLOWED_OPERATORS = {"equals", "in", "exists", "between", "prefix"}


def _path_value(payload: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """Read one allowlisted top-level path without evaluating expressions."""

    if path == "channel":
        return "channel" in payload, payload.get("channel")

    parts = path.split(".")
    if len(parts) != 2 or parts[0] not in _ALLOWED_PATH_ROOTS or not parts[1]:
        raise ValueError("path is not an allowed two-level LCP path")
    container = payload.get(parts[0])
    if not isinstance(container, Mapping):
        return False, None
    return parts[1] in container, container.get(parts[1])


def _predicate_match(predicate: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    path = predicate.get("path")
    operator = predicate.get("operator")
    if not isinstance(path, str) or not isinstance(operator, str):
        raise ValueError("predicate requires string path and operator")
    if operator not in _ALLOWED_OPERATORS:
        raise ValueError(f"unsupported predicate operator: {operator}")

    present, actual = _path_value(payload, path)
    if operator == "exists":
        expected = predicate.get("value")
        if not isinstance(expected, bool):
            raise ValueError("exists predicate requires a boolean value")
        return present is expected
    if not present:
        return False

    if operator == "equals":
        if "value" not in predicate:
            raise ValueError("equals predicate requires value")
        return actual == predicate["value"]
    if operator == "in":
        values = predicate.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError("in predicate requires a non-empty values array")
        return actual in values
    if operator == "between":
        minimum = predicate.get("min")
        maximum = predicate.get("max")
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or isinstance(minimum, bool)
            or not isinstance(minimum, (int, float))
            or isinstance(maximum, bool)
            or not isinstance(maximum, (int, float))
            or minimum > maximum
        ):
            raise ValueError("between predicate requires ordered numeric min and max")
        return minimum <= actual <= maximum
    if operator == "prefix":
        prefix = predicate.get("value")
        if not isinstance(prefix, str):
            raise ValueError("prefix predicate requires a string value")
        return isinstance(actual, str) and actual.startswith(prefix)
    raise ValueError(f"unsupported predicate operator: {operator}")


def _require_profile_identity(profile: Mapping[str, Any], label: str) -> None:
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
        raise ValueError(f"{label} requires profile_id")
    if not isinstance(profile.get("version"), str) or not profile["version"]:
        raise ValueError(f"{label} requires version")


def _match_requirements(profile: Any, payload: Mapping[str, Any]) -> list[str]:
    if not isinstance(profile, Mapping):
        return ["requirements_profile_invalid"]
    try:
        _require_profile_identity(profile, "requirements profile")
        predicates = profile.get("predicates")
        if not isinstance(predicates, list) or not predicates:
            raise ValueError("requirements profile requires predicates")
        reasons: list[str] = []
        for predicate in predicates:
            if not isinstance(predicate, Mapping):
                raise ValueError("requirements predicate must be an object")
            if not _predicate_match(predicate, payload):
                reasons.append("requirements_predicate_mismatch")
        return reasons
    except ValueError:
        return ["requirements_profile_invalid"]


def _match_service_area(profile: Any, payload: Mapping[str, Any]) -> list[str]:
    if not isinstance(profile, Mapping):
        return ["service_area_profile_invalid"]
    try:
        _require_profile_identity(profile, "service-area profile")
        location = payload.get("location")
        if not isinstance(location, Mapping):
            return ["service_area_location_missing"]
        reasons: list[str] = []
        for field, reason in (
            ("countries", "service_area_country_not_supported"),
            ("state_regions", "service_area_state_not_supported"),
            ("postal_codes", "service_area_postal_code_not_supported"),
        ):
            allowed = profile.get(field)
            if allowed is None:
                continue
            if not isinstance(allowed, list) or not allowed or not all(
                isinstance(value, str) and value for value in allowed
            ):
                raise ValueError(f"{field} must be a non-empty string array")
            location_field = "state_region" if field == "state_regions" else (
                "postal_code" if field == "postal_codes" else "country_code"
            )
            actual = location.get(location_field)
            if actual not in allowed:
                reasons.append(reason)
        return reasons
    except ValueError:
        return ["service_area_profile_invalid"]


def evaluate_offer_extensions(
    offer: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> tuple[str, ...]:
    """Evaluate known platform extensions and return explainable failure reasons."""

    extensions = offer.get("extensions", {})
    if not isinstance(extensions, Mapping):
        return ("offer_extensions_invalid",)

    reasons: list[str] = []
    if REQUIREMENTS_NAMESPACE in extensions:
        reasons.extend(_match_requirements(extensions[REQUIREMENTS_NAMESPACE], payload))
    if SERVICE_AREA_NAMESPACE in extensions:
        reasons.extend(_match_service_area(extensions[SERVICE_AREA_NAMESPACE], payload))
    return tuple(reasons)

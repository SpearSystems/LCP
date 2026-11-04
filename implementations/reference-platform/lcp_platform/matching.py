"""Deterministic buyer-offer matching.

Two declarative, deployment-scoped surfaces extend the standard offer
predicates:

* ``lcp.platform.requirements`` — either an inline predicate block (the
  v1.0-compatible form) or an immutable reference to a stored, versioned
  requirement profile (``{"profile_id": ..., "version": ...}``).
* ``lcp.platform.service_area`` — either an inline allowlist or a reference
  (``{"service_area_ref": name}``) to a versioned dataset on disk.

Nothing here executes offer-authored expressions, functions, or regexes: the
predicate language is an allowlisted operator set over a typed path registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .offer_extensions import (
    REQUIREMENTS_NAMESPACE,
    SERVICE_AREA_NAMESPACE,
    evaluate_offer_extensions,
)


_COMPLETENESS = {"minimal": 1, "standard": 2, "rich": 3}

# The requirement-profile predicate language. Operators are allowlisted and
# intentionally small; arbitrary expressions, functions, and regex are never
# evaluated. Legacy inline blocks still use their own operator names in
# ``offer_extensions`` for v1.0 compatibility.
PROFILE_OPERATORS = frozenset({"eq", "in", "not_in", "gte", "lte", "range", "exists"})
PROFILE_STATUSES = frozenset({"active", "inactive", "superseded"})
PROFILE_MAX_PREDICATES = 50
PROFILE_MAX_SET_VALUES = 100
PROFILE_MAX_PATH_DEPTH = 3

_STRING, _NUMBER, _INTEGER, _BOOLEAN = "string", "number", "integer", "boolean"

# The typed path registry: the only payload paths a stored profile predicate
# may read, each with the value type its predicates must use. Adding a path is
# a deliberate platform change; an unknown path is a write-time validation
# error naming the path, never a silent match-time miss.
PATH_REGISTRY: Mapping[str, str] = {
    "channel": _STRING,
    "attributes.vertical": _STRING,
    "attributes.schema_version": _STRING,
    "attributes.service_type": _STRING,
    "attributes.service_subtype": _STRING,
    "attributes.project_type": _STRING,
    "attributes.project_value": _NUMBER,
    "attributes.exact_budget": _NUMBER,
    "attributes.budget_band": _STRING,
    "attributes.urgency": _STRING,
    "attributes.property_type": _STRING,
    "attributes.has_existing_damage": _BOOLEAN,
    "attributes.has_insurance_claim": _BOOLEAN,
    "attributes.loan_amount_cents": _INTEGER,
    "attributes.property_value_cents": _INTEGER,
    "attributes.existing_rate_percent": _NUMBER,
    "attributes.annual_income_cents": _INTEGER,
    "attributes.is_first_home_buyer": _BOOLEAN,
    "attributes.time_to_contact_deadline_days": _INTEGER,
    "attributes.injury_present": _BOOLEAN,
    "location.country_code": _STRING,
    "location.state_region": _STRING,
    "location.postal_code": _STRING,
    "provenance.source_type": _STRING,
    "provenance.acquisition_method": _STRING,
    "provenance.brand_id": _STRING,
    "provenance.is_incentivized": _BOOLEAN,
    "provenance.campaign_id": _STRING,
    "lead_quality.spam_risk_score": _INTEGER,
    "lead_quality.data_completeness": _STRING,
    "lead_quality.verification.phone_verified_at": _STRING,
    "lead_quality.verification.email_verified_at": _STRING,
    "compliance.consent_expires_at": _STRING,
}


class ProfileValidationError(ValueError):
    """A requirement profile that the write path refuses by name."""

    def __init__(self, message: str, *, path: str | None = None, operator: str | None = None):
        self.path = path
        self.operator = operator
        super().__init__(message)


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an RFC 3339 value into an aware UTC datetime.

    Matching deliberately carries its own copy of this parser so importing the
    engine never pulls the persistence layer (and its crypto dependency).
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_iso_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class PredicateAudit:
    """One explainable predicate outcome inside a match audit record."""

    path: str
    operator: str
    matched: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operator": self.operator,
            "matched": self.matched,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    profile: dict[str, Any] | None = None
    predicates: tuple[dict[str, Any], ...] = ()
    service_area: dict[str, Any] | None = None


# ─── Predicate language ────────────────────────────────────────────────────


def _value_matches(kind: str, value: Any) -> bool:
    if kind == _STRING:
        return isinstance(value, str)
    if kind == _BOOLEAN:
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind == _INTEGER:
        return isinstance(value, int)
    return isinstance(value, (int, float))


def _require_typed_value(value: Any, kind: str, path: str) -> None:
    if not _value_matches(kind, value):
        raise ProfileValidationError(
            f"predicate value for path {path} must be {kind}",
            path=path,
        )


def _validate_predicate(predicate: Any, index: int) -> None:
    if not isinstance(predicate, Mapping):
        raise ProfileValidationError(f"predicate {index} must be an object")
    path = predicate.get("path")
    if not isinstance(path, str) or not path:
        raise ProfileValidationError(f"predicate {index} requires a string path")
    depth = len(path.split("."))
    if depth > PROFILE_MAX_PATH_DEPTH:
        raise ProfileValidationError(
            f"predicate path {path} exceeds the maximum depth of {PROFILE_MAX_PATH_DEPTH}",
            path=path,
        )
    kind = PATH_REGISTRY.get(path)
    if kind is None:
        raise ProfileValidationError(f"unknown predicate path: {path}", path=path)
    operator = predicate.get("operator")
    if not isinstance(operator, str) or operator not in PROFILE_OPERATORS:
        raise ProfileValidationError(
            f"unsupported predicate operator: {operator!r} on path {path}",
            path=path,
            operator=str(operator),
        )
    numeric = kind in {_NUMBER, _INTEGER}
    if operator in {"gte", "lte", "range"} and not numeric:
        raise ProfileValidationError(
            f"operator {operator} requires a numeric path; {path} is {kind}",
            path=path,
            operator=operator,
        )
    if operator == "exists":
        if not isinstance(predicate.get("value"), bool):
            raise ProfileValidationError(
                f"exists predicate on {path} requires a boolean value",
                path=path,
                operator=operator,
            )
        return
    if operator in {"eq", "gte", "lte"}:
        if "value" not in predicate:
            raise ProfileValidationError(
                f"{operator} predicate on {path} requires value",
                path=path,
                operator=operator,
            )
        _require_typed_value(predicate["value"], kind, path)
        return
    if operator in {"in", "not_in"}:
        values = predicate.get("values")
        if not isinstance(values, list) or not values:
            raise ProfileValidationError(
                f"{operator} predicate on {path} requires a non-empty values array",
                path=path,
                operator=operator,
            )
        if len(values) > PROFILE_MAX_SET_VALUES:
            raise ProfileValidationError(
                f"{operator} predicate on {path} has {len(values)} values; "
                f"the limit is {PROFILE_MAX_SET_VALUES}",
                path=path,
                operator=operator,
            )
        for value in values:
            _require_typed_value(value, kind, path)
        return
    minimum = predicate.get("min")
    maximum = predicate.get("max")
    if "min" not in predicate or "max" not in predicate:
        raise ProfileValidationError(
            f"range predicate on {path} requires min and max",
            path=path,
            operator=operator,
        )
    _require_typed_value(minimum, kind, path)
    _require_typed_value(maximum, kind, path)
    if minimum > maximum:
        raise ProfileValidationError(
            f"range predicate on {path} requires min <= max",
            path=path,
            operator=operator,
        )


def validate_requirement_profile(profile: Any) -> None:
    """Validate a stored profile at write time, naming the offending part."""

    if not isinstance(profile, Mapping):
        raise ProfileValidationError("requirement profile must be an object")
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise ProfileValidationError("requirement profile requires profile_id")
    version = profile.get("version")
    if not isinstance(version, str) or not version:
        raise ProfileValidationError(f"requirement profile {profile_id} requires version")
    predicates = profile.get("predicates")
    if not isinstance(predicates, list) or not predicates:
        raise ProfileValidationError(
            f"requirement profile {profile_id} requires a non-empty predicates array"
        )
    if len(predicates) > PROFILE_MAX_PREDICATES:
        raise ProfileValidationError(
            f"requirement profile {profile_id} has {len(predicates)} predicates; "
            f"the limit is {PROFILE_MAX_PREDICATES}"
        )
    for index, predicate in enumerate(predicates):
        _validate_predicate(predicate, index)
    service_area_ref = profile.get("service_area_ref")
    if service_area_ref is not None and (
        not isinstance(service_area_ref, str) or not service_area_ref
    ):
        raise ProfileValidationError(
            f"requirement profile {profile_id} requires a non-empty string service_area_ref"
        )


def _path_value(payload: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _evaluate_predicate(
    predicate: Mapping[str, Any], payload: Mapping[str, Any]
) -> PredicateAudit:
    path = str(predicate["path"])
    operator = str(predicate["operator"])
    kind = PATH_REGISTRY[path]
    present, actual = _path_value(payload, path)

    def audit(matched: bool, reason: str) -> PredicateAudit:
        return PredicateAudit(path=path, operator=operator, matched=matched, reason=reason)

    if operator == "exists":
        expected = bool(predicate["value"])
        if present is expected:
            return audit(True, "matched")
        return audit(False, "field_present" if present else "missing_field")
    if not present:
        return audit(False, "missing_field")
    if not _value_matches(kind, actual):
        return audit(False, "wrong_type")
    if operator == "eq":
        matched = actual == predicate["value"]
        return audit(matched, "matched" if matched else "value_mismatch")
    if operator == "in":
        matched = actual in predicate["values"]
        return audit(matched, "matched" if matched else "set_miss")
    if operator == "not_in":
        matched = actual not in predicate["values"]
        return audit(matched, "matched" if matched else "value_present")
    if operator == "gte":
        matched = actual >= predicate["value"]
        return audit(matched, "matched" if matched else "outside_range")
    if operator == "lte":
        matched = actual <= predicate["value"]
        return audit(matched, "matched" if matched else "outside_range")
    matched = predicate["min"] <= actual <= predicate["max"]
    return audit(matched, "matched" if matched else "outside_range")


def evaluate_requirement_profile(
    profile: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[bool, tuple[PredicateAudit, ...]]:
    """Evaluate a stored profile's predicates, returning an audit per predicate."""

    try:
        validate_requirement_profile(profile)
    except ProfileValidationError:
        return False, (PredicateAudit("", "", False, "invalid_profile"),)
    audits = tuple(
        _evaluate_predicate(predicate, payload) for predicate in profile["predicates"]
    )
    return all(audit.matched for audit in audits), audits


class RequirementProfileResolver(Protocol):
    """The store contract matching needs to resolve a versioned profile."""

    def resolve_requirement_profile(
        self,
        profile_id: str,
        version: str | None = None,
        *,
        tenant_id: str = "default",
        at: datetime | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]: ...


# ─── Service-area datasets ──────────────────────────────────────────────────

_DATASET_FIELDS = frozenset(
    {
        "name",
        "version",
        "effective_from",
        "effective_to",
        "source",
        "countries",
        "regions",
        "postal_codes",
    }
)


class ServiceAreaDatasetError(ValueError):
    """A dataset file that the loader refuses by name."""


@dataclass(frozen=True)
class ServiceAreaVersion:
    """One immutable, effective-dated service-area dataset."""

    name: str
    version: str
    effective_from: datetime
    effective_to: datetime | None
    source: str
    countries: tuple[str, ...]
    regions: tuple[str, ...]
    postal_codes: tuple[str, ...]

    def reference(self) -> dict[str, Any]:
        return {
            "ref": self.name,
            "version": self.version,
            "source": self.source,
            "effective_from": format_iso_datetime(self.effective_from),
            "effective_to": format_iso_datetime(self.effective_to)
            if self.effective_to
            else None,
        }

    def coverage_reasons(self, location: Mapping[str, Any]) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.countries and location.get("country_code") not in self.countries:
            reasons.append("service_area_country_not_supported")
        if self.regions and location.get("state_region") not in self.regions:
            reasons.append("service_area_state_not_supported")
        if self.postal_codes and location.get("postal_code") not in self.postal_codes:
            reasons.append("service_area_postal_code_not_supported")
        return tuple(reasons)


def _dataset_strings(raw: Mapping[str, Any], field: str, label: str) -> tuple[str, ...]:
    values = raw.get(field, [])
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value for value in values
    ):
        raise ServiceAreaDatasetError(f"{label}: {field} must be a string array")
    return tuple(values)


def load_service_area_dataset(path: Path) -> ServiceAreaVersion:
    """Load and validate one ``<name>.json`` dataset file."""

    label = path.name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ServiceAreaDatasetError(f"{label}: cannot read dataset: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ServiceAreaDatasetError(f"{label}: invalid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ServiceAreaDatasetError(f"{label}: dataset must be a JSON object")
    unknown = sorted(set(raw) - _DATASET_FIELDS)
    if unknown:
        raise ServiceAreaDatasetError(
            f"{label}: unknown dataset field(s): {', '.join(unknown)}"
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ServiceAreaDatasetError(f"{label}: name is required")
    if name != path.stem:
        raise ServiceAreaDatasetError(
            f"{label}: name {name!r} does not match the file name {path.stem!r}"
        )
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise ServiceAreaDatasetError(f"{label}: version is required")
    effective_from = parse_iso_datetime(raw.get("effective_from"))
    if effective_from is None:
        raise ServiceAreaDatasetError(
            f"{label}: effective_from must be an RFC 3339 timestamp"
        )
    effective_to: datetime | None = None
    if raw.get("effective_to") is not None:
        effective_to = parse_iso_datetime(raw.get("effective_to"))
        if effective_to is None:
            raise ServiceAreaDatasetError(
                f"{label}: effective_to must be an RFC 3339 timestamp or null"
            )
        if effective_to <= effective_from:
            raise ServiceAreaDatasetError(
                f"{label}: effective_to must be after effective_from"
            )
    source = raw.get("source")
    if not isinstance(source, str) or not source:
        raise ServiceAreaDatasetError(f"{label}: source is required")
    countries = _dataset_strings(raw, "countries", label)
    if not countries:
        raise ServiceAreaDatasetError(f"{label}: countries must be a non-empty array")
    for country in countries:
        if len(country) != 2 or country != country.upper():
            raise ServiceAreaDatasetError(
                f"{label}: countries must use ISO 3166-1 alpha-2 codes; got {country!r}"
            )
    return ServiceAreaVersion(
        name=name,
        version=version,
        effective_from=effective_from,
        effective_to=effective_to,
        source=source,
        countries=countries,
        regions=_dataset_strings(raw, "regions", label),
        postal_codes=_dataset_strings(raw, "postal_codes", label),
    )


class ServiceAreaCatalog:
    """Directory-backed cache of versioned service-area datasets.

    The cache is keyed by each file's size and mtime, so a dropped file is
    picked up on the next resolve. A dataset that fails validation raises
    :class:`ServiceAreaDatasetError`, never silently matching or failing closed
    without a name.
    """

    def __init__(self, directory: str | Path | None):
        self.directory = Path(directory) if directory else None
        self._lock = RLock()
        self._cache: dict[Path, tuple[tuple[int, int], ServiceAreaVersion]] = {}
        self._datasets: dict[str, list[ServiceAreaVersion]] = {}
        self._signature: tuple[tuple[str, int, int], ...] | None = None

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._datasets = {}
            self._signature = None

    def _scan_signature(self) -> tuple[tuple[str, int, int], ...]:
        if self.directory is None or not self.directory.is_dir():
            return ()
        entries = []
        for path in sorted(self.directory.glob("*.json")):
            if not path.is_file():
                continue
            stat = path.stat()
            entries.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def _load(self) -> dict[str, list[ServiceAreaVersion]]:
        with self._lock:
            signature = self._scan_signature()
            if signature == self._signature:
                return self._datasets
            datasets: dict[str, list[ServiceAreaVersion]] = {}
            for filename, mtime, size in signature:
                path = self.directory / filename  # type: ignore[operator]
                cached = self._cache.get(path)
                if cached is not None and cached[0] == (mtime, size):
                    version = cached[1]
                else:
                    version = load_service_area_dataset(path)
                    self._cache[path] = ((mtime, size), version)
                datasets.setdefault(version.name, []).append(version)
            self._datasets = datasets
            self._signature = signature
            return datasets

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._load()))

    def resolve(
        self, name: str, at: datetime | None = None
    ) -> tuple[ServiceAreaVersion | None, str | None]:
        """Resolve a dataset name to the version effective at ``at``."""

        datasets = self._load()
        with self._lock:
            versions = list(datasets.get(name, ()))
        if not versions:
            return None, "service_area_unknown"
        current = at or datetime.now(timezone.utc)
        candidates = [
            version
            for version in versions
            if version.effective_from <= current
            and (version.effective_to is None or current < version.effective_to)
        ]
        if not candidates:
            return None, "service_area_expired"
        candidates.sort(key=lambda version: version.effective_from, reverse=True)
        if (
            len(candidates) > 1
            and candidates[0].effective_from == candidates[1].effective_from
        ):
            return None, "service_area_ambiguous"
        return candidates[0], None


# ─── Reference resolution ──────────────────────────────────────────────────


def _is_profile_reference(stanza: Any) -> bool:
    """A reference carries identity and no inline predicates."""

    return (
        isinstance(stanza, Mapping)
        and "predicates" not in stanza
        and isinstance(stanza.get("profile_id"), str)
        and bool(stanza["profile_id"])
    )


def _is_service_area_reference(stanza: Any) -> bool:
    return isinstance(stanza, Mapping) and isinstance(
        stanza.get("service_area_ref"), str
    ) and bool(stanza["service_area_ref"])


def _inline_offer(offer: Mapping[str, Any]) -> Mapping[str, Any]:
    """Strip resolved reference stanzas before the legacy inline evaluator."""

    extensions = offer.get("extensions")
    if not isinstance(extensions, Mapping):
        return offer
    stripped = dict(extensions)
    if _is_profile_reference(stripped.get(REQUIREMENTS_NAMESPACE)):
        stripped.pop(REQUIREMENTS_NAMESPACE, None)
    if _is_service_area_reference(stripped.get(SERVICE_AREA_NAMESPACE)):
        stripped.pop(SERVICE_AREA_NAMESPACE, None)
    if len(stripped) == len(extensions):
        return offer
    reduced = dict(offer)
    reduced["extensions"] = stripped
    return reduced


def _resolve_profile(
    profiles: RequirementProfileResolver | None,
    reference: Mapping[str, Any],
    offer: Mapping[str, Any],
    at: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    version = reference.get("version")
    if version is not None and (not isinstance(version, str) or not version):
        return None, "requirements_profile_invalid"
    if profiles is None:
        return None, "requirement_profile_unresolved"
    return profiles.resolve_requirement_profile(
        str(reference["profile_id"]),
        version,
        tenant_id=str(offer.get("tenant_id", "default")),
        at=at,
    )


def _service_area_check(
    catalog: ServiceAreaCatalog | None,
    name: str,
    location: Mapping[str, Any],
    at: datetime,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if catalog is None:
        reason = "service_area_catalog_unavailable"
        return {"ref": name, "version": None, "matched": False, "reason": reason,
                "reasons": [reason]}, (reason,)
    version, reason = catalog.resolve(name, at)
    if version is None:
        assert reason is not None
        return {"ref": name, "version": None, "matched": False, "reason": reason,
                "reasons": [reason]}, (reason,)
    reasons = version.coverage_reasons(location)
    check = version.reference()
    check["matched"] = not reasons
    check["reason"] = "matched" if not reasons else reasons[0]
    check["reasons"] = list(reasons)
    return check, reasons


# ─── Standard offer predicates ─────────────────────────────────────────────


def _scrub_status(payload: dict[str, Any], scrub_type: str) -> str | None:
    for scrub in payload.get("compliance", {}).get("scrubs", []):
        if scrub.get("type") == scrub_type:
            return scrub.get("result")
    return None


def _verified(payload: dict[str, Any], field: str) -> bool:
    quality = payload.get("lead_quality", {})
    if quality.get(field) is True:
        return True
    verification = quality.get("verification", {})
    return bool(verification.get(f"{field.removeprefix('verified_')}_verified_at"))


def _in_window(window: dict[str, Any], now: datetime) -> bool:
    try:
        local = now.astimezone(ZoneInfo(window["timezone"]))
    except (KeyError, ValueError):
        return False
    day = local.strftime("%a").lower()[:3]
    if day not in window.get("days", []):
        return False
    current = local.strftime("%H:%M")
    start = window["available_from"]
    end = window["available_to"]
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def offer_is_available(
    offer: dict[str, Any],
    now: datetime | None = None,
    *,
    vertical: str | None = None,
    channel: str | None = None,
) -> MatchResult:
    """Check active status and configured delivery windows.

    A delivery window may narrow an offer to particular verticals or channels.
    Missing lead criteria cannot satisfy a restricted window; this keeps
    matching fail-closed when a publisher omits a field that the buyer used to
    constrain availability.
    """
    reasons: list[str] = []
    if offer.get("active", True) is not True:
        reasons.append("offer_inactive")
    windows = offer.get("delivery_windows", [])
    if windows:
        current = now or datetime.now(timezone.utc)
        matching_windows = []
        for window in windows:
            allowed_verticals = window.get("verticals")
            allowed_channels = window.get("channels")
            criteria_match = (
                (not allowed_verticals or vertical in allowed_verticals)
                and (not allowed_channels or channel in allowed_channels)
            )
            if criteria_match and _in_window(window, current):
                matching_windows.append(window)
        if not matching_windows:
            reasons.append("outside_delivery_window")
    return MatchResult(not reasons, tuple(reasons))


def match_offer(
    offer: dict[str, Any],
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    sender_id: str | None = None,
    profiles: RequirementProfileResolver | None = None,
    service_areas: ServiceAreaCatalog | None = None,
) -> MatchResult:
    """Evaluate all standard offer predicates against a lead payload.

    ``profiles`` resolves stored requirement-profile references, and
    ``service_areas`` resolves dataset references. A reference that cannot be
    resolved is non-comparable with a named reason: it never silently matches,
    and it never falls back to match-all.
    """
    reasons: list[str] = []
    attributes = payload.get("attributes", {})
    location = payload.get("location", {})
    provenance = payload.get("provenance", {})
    compliance = payload.get("compliance", {})
    quality = payload.get("lead_quality", {})

    current = now or datetime.now(timezone.utc)
    available = offer_is_available(
        offer,
        current,
        vertical=attributes.get("vertical"),
        channel=payload.get("channel"),
    )
    reasons.extend(available.reasons)

    profile_audit: dict[str, Any] | None = None
    predicate_audits: tuple[dict[str, Any], ...] = ()
    service_area_checks: list[dict[str, Any]] = []

    extensions = offer.get("extensions", {})
    if isinstance(extensions, Mapping):
        reference = extensions.get(REQUIREMENTS_NAMESPACE)
        if _is_profile_reference(reference):
            record, reason = _resolve_profile(profiles, reference, offer, current)
            if record is None:
                assert reason is not None
                reasons.append(reason)
                profile_audit = {
                    "profile_id": str(reference["profile_id"]),
                    "version": reference.get("version"),
                    "resolved": False,
                    "reason": reason,
                }
            else:
                profile_audit = {
                    "profile_id": record["profile_id"],
                    "version": record["version"],
                    "resolved": True,
                    "effective_from": record.get("effective_from"),
                    "effective_to": record.get("effective_to"),
                }
                matched, audits = evaluate_requirement_profile(record, payload)
                predicate_audits = tuple(audit.as_dict() for audit in audits)
                if not matched:
                    reasons.append("requirements_predicate_mismatch")
                area_ref = record.get("service_area_ref")
                if isinstance(area_ref, str) and area_ref:
                    check, area_reasons = _service_area_check(
                        service_areas, area_ref, location, current
                    )
                    service_area_checks.append(check)
                    reasons.extend(area_reasons)
        area_reference = extensions.get(SERVICE_AREA_NAMESPACE)
        if _is_service_area_reference(area_reference):
            check, area_reasons = _service_area_check(
                service_areas, area_reference["service_area_ref"], location, current
            )
            service_area_checks.append(check)
            reasons.extend(area_reasons)

    consent_expires_at = parse_iso_datetime(compliance.get("consent_expires_at"))
    if consent_expires_at is not None and consent_expires_at <= current:
        reasons.append("consent_expired")
    if isinstance(extensions, Mapping):
        required_purposes = extensions.get("lcp.platform.required_consent_purposes", [])
        contact_purpose = extensions.get("lcp.platform.contact_purpose")
        if contact_purpose and contact_purpose not in required_purposes:
            required_purposes = [*required_purposes, contact_purpose] if isinstance(required_purposes, list) else [contact_purpose]
        if required_purposes:
            purposes = compliance.get("consent_purposes", [])
            if not isinstance(required_purposes, list) or not isinstance(purposes, list):
                reasons.append("consent_purpose_policy_invalid")
            else:
                missing = sorted({str(purpose) for purpose in required_purposes} - set(purposes))
                if missing:
                    reasons.append("consent_purpose_missing:" + ",".join(missing))

    if attributes.get("vertical") != offer.get("vertical"):
        reasons.append("vertical_mismatch")
    allowed_publishers = offer.get("allowed_publisher_ids", [])
    if allowed_publishers and sender_id not in allowed_publishers:
        reasons.append("publisher_not_allowed")
    allowed_brands = offer.get("allowed_brand_ids", [])
    if allowed_brands and provenance.get("brand_id") not in allowed_brands:
        reasons.append("brand_not_allowed")
    if location.get("country_code") not in offer.get("countries", []):
        reasons.append("country_not_supported")
    if offer.get("state_regions") and location.get("state_region") not in offer["state_regions"]:
        reasons.append("state_region_not_supported")
    if offer.get("postal_codes") and location.get("postal_code") not in offer["postal_codes"]:
        reasons.append("postal_code_not_supported")
    if offer.get("channels") and payload.get("channel") not in offer["channels"]:
        reasons.append("channel_not_supported")

    source_type = provenance.get("source_type")
    if source_type in offer.get("excluded_source_types", []):
        reasons.append("source_type_excluded")
    acquisition_method = provenance.get("acquisition_method")
    if acquisition_method in offer.get("excluded_acquisition_methods", []):
        reasons.append("acquisition_method_excluded")
    if offer.get("reject_incentivized") and provenance.get("is_incentivized") is not False:
        reasons.append("incentivized_lead")
    if provenance.get("incentive_type") in offer.get("excluded_incentive_types", []):
        reasons.append("incentive_type_excluded")

    if offer.get("require_verified_phone") and not _verified(payload, "verified_phone"):
        reasons.append("phone_not_verified")
    if offer.get("require_verified_email") and not _verified(payload, "verified_email"):
        reasons.append("email_not_verified")

    if "max_spam_risk_score" in offer:
        score = quality.get("spam_risk_score")
        if not isinstance(score, int) or score > offer["max_spam_risk_score"]:
            reasons.append("spam_risk_too_high")
    if "min_data_completeness" in offer:
        completeness = quality.get("data_completeness")
        if _COMPLETENESS.get(completeness, 0) < _COMPLETENESS[offer["min_data_completeness"]]:
            reasons.append("data_completeness_too_low")

    for field, expected in offer.get("attribute_equals", {}).items():
        if attributes.get(field) != expected:
            reasons.append(f"attribute_{field}_mismatch")
    for field, allowed in offer.get("attribute_in", {}).items():
        if attributes.get(field) not in allowed:
            reasons.append(f"attribute_{field}_not_allowed")

    if offer.get("require_consent_evidence") and not compliance.get("consent_evidence"):
        reasons.append("consent_evidence_missing")
    if offer.get("reject_dnc_flagged") and _scrub_status(payload, "dnc_national") != "clean":
        reasons.append("dnc_not_clean")
    if offer.get("reject_litigator_flagged") and _scrub_status(payload, "litigator") != "clean":
        reasons.append("litigator_not_clean")
    if offer.get("reject_blacklist_flagged") and _scrub_status(payload, "blacklist") != "clean":
        reasons.append("blacklist_not_clean")

    reasons.extend(evaluate_offer_extensions(_inline_offer(offer), payload))

    # Claim-language evidence is intentionally fail-closed. A deployment may
    # provide a namespaced evidence block without putting copy/PII in a ping.
    if offer.get("excluded_claim_language") and not payload.get("extensions", {}).get(
        "lcp.platform.claim_language"
    ):
        reasons.append("claim_language_evidence_missing")

    service_area = None
    if service_area_checks:
        service_area = {
            "matched": all(check["matched"] for check in service_area_checks),
            "checks": service_area_checks,
        }
    return MatchResult(
        not reasons,
        tuple(reasons),
        profile=profile_audit,
        predicates=predicate_audits,
        service_area=service_area,
    )

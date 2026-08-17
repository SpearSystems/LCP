"""Deterministic buyer-offer matching."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .offer_extensions import evaluate_offer_extensions
from .storage import parse_iso_datetime


_COMPLETENESS = {"minimal": 1, "standard": 2, "rich": 3}


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


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
) -> MatchResult:
    """Evaluate all standard offer predicates against a lead payload."""
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

    consent_expires_at = parse_iso_datetime(compliance.get("consent_expires_at"))
    if consent_expires_at is not None and consent_expires_at <= current:
        reasons.append("consent_expired")
    extensions = offer.get("extensions", {})
    if isinstance(extensions, dict):
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

    reasons.extend(evaluate_offer_extensions(offer, payload))

    # Claim-language evidence is intentionally fail-closed. A deployment may
    # provide a namespaced evidence block without putting copy/PII in a ping.
    if offer.get("excluded_claim_language") and not payload.get("extensions", {}).get(
        "lcp.platform.claim_language"
    ):
        reasons.append("claim_language_evidence_missing")

    return MatchResult(not reasons, tuple(reasons))

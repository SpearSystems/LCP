"""Receiver capability helpers for v1.1 slot fields (SPEC.md §8, LEP-0003).

The v1.1 slot fields are **capability-gated, never ignore-gated**: the
``ping``/``bid``/``post`` payloads are closed objects (``additionalProperties:
false``), so a schema-validating v1.0 receiver rejects unknown slot fields
instead of ignoring them. Senders MUST therefore only include them toward a
receiver whose §8 ``lcp_versions`` advertisement parses to a full
``major.minor.patch`` element with ``major == 1`` and ``minor >= 1`` —
compared as parsed versions, never as a string literal or prefix (a literal
``1.1`` is unsatisfiable by full-semver elements; a ``1.1`` prefix match
would also accept malformed elements).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: Payload fields introduced at v1.1 that require the receiver to advertise
#: v1.1 support before they may be sent (SPEC.md §4 capability gate, §8).
SLOT_FIELDS = ("slots_available", "slots_claimed", "contacted_hashes")

MIN_SLOT_MINOR = 1


def receiver_advertises_slot_fields(advertised_lcp_versions: Iterable[str]) -> bool:
    """Return True when an advertised version set supports v1.1 slot fields.

    Implements the §8 version-string membership rule: every element MUST be a
    full ``major.minor.patch`` semantic version; the gate opens only when a
    parseable element has ``major == 1`` and ``minor >= 1``. Malformed or
    partial elements (``1.1``, ``v1.1.0``, ``1.1.0.1``) never open the gate.
    """
    for element in advertised_lcp_versions:
        parts = element.split(".") if isinstance(element, str) else None
        if (
            parts is None
            or len(parts) != 3
            or not all(part.isdigit() for part in parts)
        ):
            continue
        major, minor = int(parts[0]), int(parts[1])
        if major == 1 and minor >= MIN_SLOT_MINOR:
            return True
    return False


def slot_fields_present(payload: Any) -> list[str]:
    """Return the slot fields present on a message payload (recursively-safe,
    shallow on the payload object itself, plus CONTACTED report details)."""
    if not isinstance(payload, dict):
        return []
    present = [field for field in SLOT_FIELDS if field in payload]
    if payload.get("event") == "CONTACTED":
        details = payload.get("details")
        if isinstance(details, dict) and "contacted_hashes" in details:
            present.append("details.contacted_hashes")
    return present


def slot_capability_errors(
    envelope: dict[str, Any],
    advertised_lcp_versions: Iterable[str],
) -> list[str]:
    """Return errors when an envelope carries v1.1 slot fields but the
    receiver's advertisement does not support them (§8 gate; invalid example D).

    An empty list means the envelope is capability-compatible; it is NOT a
    schema verdict — use ``SchemaValidator`` for that.
    """
    payload = (
        envelope.get("lcp", {}).get("payload") if isinstance(envelope, dict) else None
    )
    present = slot_fields_present(payload)
    if not present:
        return []
    if receiver_advertises_slot_fields(advertised_lcp_versions):
        return []
    return [
        f"{field} requires the receiver to advertise a full-semver LCP version with major 1 and minor >= {MIN_SLOT_MINOR} (SPEC.md §8 capability gate)"
        for field in present
    ]

"""GENERATED FROM schemas/ — do not edit manually.

These TypedDicts are generated from the canonical schema set. Runtime
acceptance remains the JSON Schema validator's responsibility.
"""
from __future__ import annotations

from typing import Any, NotRequired, TypedDict

JsonObject = dict[str, Any]


class MessageModel(TypedDict, total=False):
    id: str
    type: str
    timestamp: str
    sender_id: str
    receiver_id: str
    correlation_id: str | None
    idempotency_key: str
    test: NotRequired[bool]
    security: NotRequired[JsonObject]


class EnvelopeModel(TypedDict):
    lcp: dict[str, Any]


class LeadPayload(TypedDict, total=False):
    lead_id: str
    external_id: NotRequired[str]
    submitted_at: NotRequired[str]
    status: str
    channel: str
    consumer: JsonObject
    location: JsonObject
    compliance: NotRequired[JsonObject]
    provenance: NotRequired[JsonObject]
    attributes: JsonObject
    exclusivity: NotRequired[JsonObject]
    contact_window: NotRequired[JsonObject]
    lead_quality: NotRequired[JsonObject]
    expiry: NotRequired[JsonObject]
    attachments: NotRequired[list[JsonObject]]


class CallPayload(TypedDict, total=False):
    lead_id: str
    external_id: NotRequired[str]
    submitted_at: NotRequired[str]
    status: str
    channel: str
    consumer: JsonObject
    location: JsonObject
    call: JsonObject
    compliance: NotRequired[JsonObject]
    provenance: NotRequired[JsonObject]
    attributes: NotRequired[JsonObject]
    expiry: NotRequired[JsonObject]
    exclusivity: NotRequired[JsonObject]
    attachments: NotRequired[list[JsonObject]]


class PingPayload(TypedDict, total=False):
    ping_id: str
    lead_reference: str
    publisher_id: NotRequired[str]
    offer_id: NotRequired[str]
    phone_hash: str
    email_hash: NotRequired[str]
    country_code: str
    state_region: NotRequired[str]
    postal_code: NotRequired[str]
    vertical: str
    lead_age_minutes: NotRequired[int]
    attributes: NotRequired[JsonObject]
    compliance_flags: NotRequired[JsonObject]
    quality_flags: NotRequired[JsonObject]
    floor_price_cents: int
    currency: str
    exclusivity: NotRequired[str]
    dedup_window_hours: NotRequired[int]
    expires_at: NotRequired[str]


class PostPayload(TypedDict, total=False):
    lead_id: str
    delivered_at: str
    submitted_at: NotRequired[str]
    offer_id: NotRequired[str]
    price_cents: int
    currency: str
    buyer_id: str
    buyer_reference: NotRequired[str]
    pricing: NotRequired[JsonObject]
    matched_preferences: NotRequired[JsonObject]
    consumer: JsonObject
    location: JsonObject
    compliance: NotRequired[JsonObject]
    attributes: JsonObject
    provenance: NotRequired[JsonObject]
    exclusivity: NotRequired[JsonObject]
    call: NotRequired[JsonObject]
    attachments: NotRequired[list[JsonObject]]


class BidPayload(TypedDict, total=False):
    ping_id: str
    decision: str
    bid_price_cents: int
    currency: str
    estimated_contact_seconds: NotRequired[int]
    buyer_reference: NotRequired[str]
    reject_reason: NotRequired[str]
    capacity_remaining: NotRequired[int]


class AckPayload(TypedDict, total=False):
    original_message_id: str
    status: str
    errors: NotRequired[list[JsonObject]]
    lead_id: NotRequired[str]
    request_id: NotRequired[str]
    rejection_reason: NotRequired[str]


class EventPayload(TypedDict, total=False):
    lead_id: str
    event: str
    timestamp: str
    details: NotRequired[JsonObject]
    external_reference: NotRequired[str]


class OfferModel(TypedDict, total=False):
    offer_id: str
    buyer_id: str
    tenant_id: NotRequired[str]
    active: NotRequired[bool]
    routing_mode: NotRequired[str]
    vertical: str
    schema_version: NotRequired[str]
    countries: list[str]
    state_regions: NotRequired[list[str]]
    postal_codes: NotRequired[list[str]]
    channels: NotRequired[list[str]]
    floor_price_cents: int
    currency: str
    extensions: NotRequired[JsonObject]
    allowed_publisher_ids: NotRequired[list[str]]
    allowed_brand_ids: NotRequired[list[str]]
    attribute_equals: NotRequired[JsonObject]
    attribute_in: NotRequired[JsonObject]
    monthly_minimum_payable: NotRequired[int]
    monthly_maximum_payable: NotRequired[int]
    monthly_quota_timezone: NotRequired[str]
    monthly_quota_policy: NotRequired[str]
    payable_rules: NotRequired[JsonObject]
    call_routing_mode: NotRequired[str]
    connect_timeout_seconds: NotRequired[int]

"""LCP message builders used by the reference router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from .auth import signature_for
from .storage import is_expired, now_iso, parse_iso_datetime


def _envelope(
    message_type: str,
    sender_id: str,
    receiver_id: str,
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    test: bool = False,
) -> dict[str, Any]:
    message_id = str(uuid4())
    return {
        "lcp": {
            "version": "1.0.0",
            "message": {
                "id": message_id,
                "type": message_type,
                "timestamp": now_iso(),
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "correlation_id": correlation_id,
                "idempotency_key": idempotency_key or f"{sender_id}-{message_type}-{message_id}",
                "test": test,
            },
            "payload": payload,
        }
    }


def build_ack(
    original: dict[str, Any],
    *,
    sender_id: str,
    status: str,
    lead_id: str | None = None,
    rejection_reason: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    original_message = original["lcp"]["message"]
    payload: dict[str, Any] = {
        "original_message_id": original_message["id"],
        "status": status,
    }
    if lead_id:
        payload["lead_id"] = lead_id
    if rejection_reason:
        payload["rejection_reason"] = rejection_reason
    if errors:
        payload["errors"] = errors
    return _envelope(
        "ack",
        sender_id,
        original_message["sender_id"],
        payload,
        correlation_id=original_message["id"],
        test=original_message.get("test", False),
    )


def build_ping(
    lead_envelope: dict[str, Any],
    offer: dict[str, Any],
    *,
    platform_id: str,
    buyer_secret: str,
) -> tuple[dict[str, Any], str]:
    lead_message = lead_envelope["lcp"]["message"]
    lead = lead_envelope["lcp"]["payload"]
    consumer = lead.get("consumer", {})
    location = lead.get("location", {})
    attributes = lead.get("attributes", {})
    vertical = attributes.get("vertical", offer["vertical"])
    phone = consumer.get("phone")
    if phone:
        phone_hash = hmac.new(
            buyer_secret.encode(), phone.encode(), hashlib.sha256
        ).hexdigest()
    else:
        phone_hash = consumer.get("phone_hash")
    if not phone_hash:
        raise ValueError("A routable lead must contain phone or phone_hash")

    vertical_path = Path(__file__).resolve().parents[3] / "verticals" / f"{vertical}.json"
    safe_attributes: dict[str, Any] = {}
    if vertical_path.exists():
        with vertical_path.open(encoding="utf-8") as handle:
            vertical_schema = json.load(handle)
        for name, definition in vertical_schema.get("properties", {}).items():
            if name in {"vertical", "schema_version"}:
                continue
            if definition.get("ping_safe") is True and name in attributes:
                safe_attributes[name] = attributes[name]

    submitted_at = lead.get("submitted_at") or lead_message["timestamp"]
    try:
        submitted = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
        age_minutes = max(0, int((datetime.now(timezone.utc) - submitted).total_seconds() // 60))
    except ValueError:
        age_minutes = 0
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=offer.get("ping_timeout_seconds", 30))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    ping_id = f"ping_{uuid4().hex}"
    reference = hashlib.sha256(
        f"{lead['lead_id']}:{offer['offer_id']}".encode()
    ).hexdigest()[:32]
    payload: dict[str, Any] = {
        "ping_id": ping_id,
        "lead_reference": reference,
        "publisher_id": lead_message["sender_id"],
        "offer_id": offer["offer_id"],
        "phone_hash": phone_hash,
        "country_code": location["country_code"],
        "vertical": vertical,
        "lead_age_minutes": age_minutes,
        "attributes": safe_attributes,
        "compliance_flags": {
            "consent": bool(lead.get("compliance", {}).get("consent_evidence")),
            "otp_verified": bool(lead.get("compliance", {}).get("otp_verified")),
            "dnc_checked": bool(lead.get("compliance", {}).get("dnc_checked")),
            "consent_valid": not is_expired(
                parse_iso_datetime(lead.get("compliance", {}).get("consent_expires_at"))
            ),
        },
        "quality_flags": {
            "verified_phone": lead.get("lead_quality", {}).get("verified_phone", False),
            "verified_email": lead.get("lead_quality", {}).get("verified_email", False),
            "is_incentivized": lead.get("provenance", {}).get("is_incentivized", False),
            "spam_risk_score": lead.get("lead_quality", {}).get("spam_risk_score", 100),
        },
        "floor_price_cents": offer["floor_price_cents"],
        "currency": offer["currency"],
        "exclusivity": lead.get("exclusivity", {}).get("exclusivity", "shared"),
        "expires_at": expires_at,
    }
    if location.get("state_region"):
        payload["state_region"] = location["state_region"]
    if location.get("postal_code"):
        payload["postal_code"] = location["postal_code"]
    if lead.get("exclusivity", {}).get("max_buyers"):
        payload["exclusivity"] = lead["exclusivity"]["exclusivity"]
    return (
        _envelope(
            "ping",
            platform_id,
            offer["buyer_id"],
            payload,
            correlation_id=lead_message["id"],
            idempotency_key=f"{platform_id}-ping-{ping_id}",
            test=lead_message.get("test", False),
        ),
        expires_at,
    )


def build_post(
    lead_envelope: dict[str, Any],
    offer: dict[str, Any],
    *,
    platform_id: str,
    price_cents: int,
    buyer_reference: str | None,
    correlation_id: str,
) -> dict[str, Any]:
    lead = lead_envelope["lcp"]["payload"]
    payload: dict[str, Any] = {
        "lead_id": lead["lead_id"],
        "delivered_at": now_iso(),
        "submitted_at": lead.get("submitted_at", lead_envelope["lcp"]["message"]["timestamp"]),
        "offer_id": offer["offer_id"],
        "price_cents": price_cents,
        "currency": offer["currency"],
        "buyer_id": offer["buyer_id"],
        "consumer": lead["consumer"],
        "location": lead["location"],
        "attributes": lead["attributes"],
    }
    for field in ("compliance", "provenance", "exclusivity", "attachments", "call"):
        if field in lead:
            payload[field] = lead[field]
    if buyer_reference:
        payload["buyer_reference"] = buyer_reference
    payload["pricing"] = {
        "floor_price_cents": offer["floor_price_cents"],
        "final_price_cents": price_cents,
        "price_guardrails": True,
        "payable_definition": offer.get("payable_definition", "bilateral_definition"),
        "payable_status": "pending",
    }
    payload["matched_preferences"] = {
        "offer_id": offer["offer_id"],
        "vertical": offer["vertical"],
        "countries": offer["countries"],
    }
    return _envelope(
        "post",
        platform_id,
        offer["buyer_id"],
        payload,
        correlation_id=correlation_id,
        idempotency_key=f"{platform_id}-post-{lead['lead_id']}-{offer['offer_id']}",
        test=lead_envelope["lcp"]["message"].get("test", False),
    )


def build_event(
    lead_id: str,
    event_name: str,
    *,
    platform_id: str,
    receiver_id: str,
    correlation_id: str | None = None,
    details: dict[str, Any] | None = None,
    test: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "lead_id": lead_id,
        "event": event_name,
        "timestamp": now_iso(),
    }
    if details:
        payload["details"] = details
    return _envelope(
        "event",
        platform_id,
        receiver_id,
        payload,
        correlation_id=correlation_id,
        test=test,
    )


def webhook_headers(
    envelope: dict[str, Any],
    *,
    secret: str,
    timestamp: str | None = None,
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    message = envelope["lcp"]["message"]
    timestamp = timestamp or now_iso()
    idempotency_key = message["idempotency_key"]
    return body, {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-LCP-Sender-Id": message["sender_id"],
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Idempotency-Key": idempotency_key,
        "X-LCP-Signature": signature_for(secret, timestamp, idempotency_key, body),
        **({"X-LCP-Test": "true"} if message.get("test") else {}),
    }

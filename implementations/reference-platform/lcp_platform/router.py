"""Production-oriented LCP platform and routing orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
import hashlib
import re
import httpx

from .attachments import (
    AttachmentError,
    FileAttachmentStore,
    build_attachment_store,
    build_malware_scanner,
    validate_residency,
)
from .auth import Authenticator, header
from .crypto import EnvelopeCipher
from .mapping import MappingError, MappingRegistry, PublisherNormalizer, source_digest
from .config import PlatformConfig
from .database import create_store
from .matching import MatchResult, match_offer
from .messages import (
    build_ack,
    build_event,
    build_ping,
    build_post,
    webhook_headers,
)
from .security import SecurityPolicyError, validate_egress_host, validate_webhook_url
from .storage import (
    EventIdempotencyConflict,
    InvalidStatusTransition,
    Store,
    envelope_expiry,
    format_iso_datetime,
    is_expired,
    now_iso,
    parse_iso_datetime,
)
from .validation import ValidationError, SchemaValidator


class RequestError(ValueError):
    """A structured HTTP/API error."""

    def __init__(
        self,
        message: str,
        code: str,
        status_code: int = 400,
        details: list[str] | dict[str, Any] | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.details = details
        super().__init__(message)


_EVENT_STATUS_TARGETS = {
    "ACCEPTED": "ACCEPTED",
    "REJECTED": "REJECTED",
    "DISPUTED": "DISPUTED",
    "REFUNDED": "REFUNDED",
    "CONVERTED": "CONVERTED",
    "ARCHIVED": "ARCHIVED",
    "EXPIRED": "EXPIRED",
}
_PUBLISHER_EVENT_TYPES = {
    "REJECTED",
    "DISPUTED",
    "CONSENT_WITHDRAWN",
    "ERASURE_REQUEST",
}
_BUYER_EVENT_TYPES = {
    "DELIVERED",
    "ACCEPTED",
    "REJECTED",
    "DISPUTED",
    "CONVERTED",
    "CALL_OFFERED",
    "CALL_CONNECTED",
    "CALL_ENDED",
    "CALL_OUTCOME",
}


class Platform:
    """Coordinates persistence, validation, matching, and delivery."""

    def __init__(self, config: PlatformConfig, store: Store | None = None):
        self.config = config
        self.store = store or create_store(config)
        self.validator = SchemaValidator(config.schema_root)
        self.auth = Authenticator(self.store, config)
        self.attachments = build_attachment_store(
            config,
            cipher=EnvelopeCipher(config.pii_encryption_key),
        )
        self.attachment_scanner = build_malware_scanner(config)
        self.mapping_registry = MappingRegistry(self.store.list_mappings(active_only=False))
        self.normalizer = PublisherNormalizer()
        self.worker_id = f"worker-{uuid4().hex}"
        self._retry_delays = (1, 5, 30, 120, 600)

    def close(self) -> None:
        self.store.close()

    # ─── Administrative configuration ──────────────────────────────────────

    def upsert_credential(self, sender_id: str, **kwargs: Any) -> None:
        self.store.upsert_credential(sender_id, **kwargs)
        self.store.insert_audit(
            tenant_id=str(kwargs.get("tenant_id", "default")),
            actor_id="operator",
            action="credential.upsert",
            resource_type="credential",
            resource_id=sender_id,
            metadata={"scopes": kwargs.get("scopes") if kwargs.get("scopes") is not None else ["*"]},
        )

    def upsert_mapping(self, mapping: dict[str, Any]) -> None:
        try:
            self.mapping_registry.register(mapping)
        except MappingError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        self.store.upsert_mapping(mapping)
        self.store.insert_audit(
            tenant_id=str(mapping.get("tenant_id", self.config.routing_tenant_id)),
            actor_id="operator",
            action="mapping.upsert",
            resource_type="publisher_mapping",
            resource_id=str(mapping["mapping_id"]),
            metadata={
                "publisher_id": mapping["publisher_id"],
                "form_key": mapping["form_key"],
                "version": mapping["version"],
            },
        )

    def normalize_publisher_record(
        self,
        source_record: dict[str, Any],
        *,
        publisher_id: str,
        form_key: str,
        version: str | None = None,
        receiver_id: str | None = None,
        test: bool = False,
    ) -> dict[str, Any]:
        try:
            mapping = self.mapping_registry.resolve(publisher_id, form_key, version)
            envelope = self.normalizer.normalize(
                source_record,
                mapping,
                receiver_id=receiver_id or self.config.platform_id,
                test=test,
            )
        except MappingError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        payload = envelope["lcp"]["payload"]
        self.validator.require_valid_envelope(envelope)
        self.store.insert_mapping_application(
            mapping=mapping,
            source_record_id=str(payload.get("external_id")) if payload.get("external_id") else None,
            lead_id=str(payload["lead_id"]),
            source_digest=source_digest(source_record),
        )
        self.store.insert_audit(
            tenant_id=self.auth.tenant_for(publisher_id) or self.config.routing_tenant_id,
            actor_id=publisher_id,
            action="mapping.applied",
            resource_type="lead",
            resource_id=str(payload["lead_id"]),
            metadata={"mapping_id": mapping["mapping_id"], "version": mapping["version"]},
        )
        return envelope

    def upsert_offer(self, offer: dict[str, Any]) -> None:
        offer = dict(offer)
        offer.setdefault("tenant_id", self.config.routing_tenant_id)
        self.validator.require_valid_offer(offer)
        if offer.get("webhook_url"):
            try:
                validate_webhook_url(offer["webhook_url"], self.config)
            except SecurityPolicyError as exc:
                raise RequestError(str(exc), "LCP-100") from exc
        self.store.upsert_offer(offer)
        self.store.insert_audit(
            tenant_id=str(offer["tenant_id"]),
            actor_id="operator",
            action="offer.upsert",
            resource_type="offer",
            resource_id=offer["offer_id"],
            metadata={"buyer_id": offer["buyer_id"], "active": offer.get("active", True)},
        )

    # ─── Inbound messages ──────────────────────────────────────────────────

    def ingest(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        headers = headers or {}
        self._validate_envelope(envelope)
        message = envelope["lcp"]["message"]
        if message["type"] not in {"lead", "call"}:
            raise RequestError("Only lead and call messages can enter intake", "LCP-006")
        self._validate_transport(message, headers)
        if message["receiver_id"] != self.config.platform_id:
            raise RequestError("Message receiver is not this platform", "LCP-002", 401)
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body if raw_body is not None else self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
            required_scope="lead:submit",
        )
        if sender_id != message["sender_id"]:
            raise RequestError("Authenticated sender does not match message sender", "LCP-002", 401)
        if not self.store.consume_rate_limit(
            sender_id,
            limit=self.config.rate_limit_per_minute,
        ):
            raise RequestError("Sender rate limit exceeded", "LCP-011", 429)

        existing = self.store.get_lead_by_idempotency(sender_id, message["idempotency_key"])
        if existing:
            if self._body(self.store.decode_envelope(existing["envelope_json"])) != self._body(envelope):
                raise RequestError(
                    "Idempotency key was reused with a different message",
                    "LCP-005",
                    409,
                )
            self.store.insert_audit(
                tenant_id=self.auth.tenant_for(sender_id) or self.config.routing_tenant_id,
                actor_id=sender_id,
                action="lead.duplicate",
                resource_type="lead",
                resource_id=existing["lead_id"],
                metadata={"message_id": message["id"]},
            )
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="DUPLICATE",
                lead_id=existing["lead_id"],
            )

        payload = envelope["lcp"]["payload"]
        lead_expired = is_expired(envelope_expiry(envelope))
        offers = [] if lead_expired else self.store.list_offer_candidates(
            payload,
            tenant_id=self.config.routing_tenant_id,
        )
        matches: list[tuple[dict[str, Any], MatchResult]] = []
        self._validate_attachment_references(payload, sender_id)
        for offer in offers:
            result = match_offer(offer, payload, sender_id=message["sender_id"])
            if result.matched and not self._capacity_available(offer):
                result = MatchResult(False, result.reasons + ("capacity_exceeded",))
            matches.append((offer, result))
        matching_offers = [offer for offer, result in matches if result.matched]
        direct_offers = [
            offer for offer in matching_offers if offer.get("routing_mode", "auction") == "direct"
        ]
        if direct_offers:
            direct_offers.sort(key=self._pacing_priority)
            lead_exclusivity = payload.get("exclusivity", {})
            if lead_exclusivity.get("exclusivity", "shared") == "exclusive":
                matching_offers = direct_offers[:1]
            else:
                matching_offers = direct_offers[: max(1, int(lead_exclusivity.get("max_buyers", 1)))]
        sender_tenant = self.auth.tenant_for(sender_id) or self.config.routing_tenant_id
        initial_status = "EXPIRED" if lead_expired else "NEW"
        if not self.store.insert_lead(envelope, status=initial_status, tenant_id=sender_tenant):
            existing = self.store.get_lead_by_idempotency(sender_id, message["idempotency_key"])
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="DUPLICATE",
                lead_id=existing["lead_id"] if existing else payload["lead_id"],
            )

        for offer, result in matches:
            self.store.insert_match_decision(
                lead_id=payload["lead_id"],
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                matched=result.matched,
                reasons=result.reasons,
            )

        created_pings = 0
        created_direct_posts = 0
        if not lead_expired:
            for offer in matching_offers:
                if offer.get("routing_mode", "auction") == "direct":
                    if self._create_direct_post(envelope, offer):
                        created_direct_posts += 1
                elif self._create_ping(envelope, offer):
                    created_pings += 1
        if created_direct_posts:
            self.store.update_lead_status(
                payload["lead_id"], "POSTED", reason="direct_delivery"
            )
        elif created_pings:
            self.store.update_lead_status(payload["lead_id"], "PINGED")
        self.store.complete_routing_job(payload["lead_id"])
        self.store.insert_audit(
            tenant_id=sender_tenant,
            actor_id=sender_id,
            action="lead.routed",
            resource_type="lead",
            resource_id=payload["lead_id"],
            metadata={"message_type": message["type"], "ping_count": created_pings, "post_count": created_direct_posts},
        )

        return build_ack(
            envelope,
            sender_id=self.config.platform_id,
            status="RECEIVED",
            lead_id=payload["lead_id"],
        )

    def submit_bid(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        headers = headers or {}
        self._validate_envelope(envelope)
        message = envelope["lcp"]["message"]
        if message["type"] != "bid":
            raise RequestError("Bid endpoint requires a bid message", "LCP-006")
        self._validate_transport(message, headers)
        if message["receiver_id"] != self.config.platform_id:
            raise RequestError("Message receiver is not this platform", "LCP-002", 401)
        payload = envelope["lcp"]["payload"]
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body if raw_body is not None else self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
            required_scope="bid:submit",
        )
        ping = self.store.get_ping(payload["ping_id"])
        if not ping:
            raise RequestError("Unknown ping_id", "LCP-003", 404)
        if sender_id != ping["buyer_id"]:
            raise RequestError("Buyer is not authorized for this ping", "LCP-002", 401)
        ping_envelope = self.store.decode_envelope(ping["envelope_json"])
        if message.get("correlation_id") != ping_envelope["lcp"]["message"]["id"]:
            raise RequestError("Bid correlation does not match the ping", "LCP-003", 400)
        if not self.store.consume_rate_limit(
            sender_id,
            limit=self.config.rate_limit_per_minute,
        ):
            raise RequestError("Sender rate limit exceeded", "LCP-011", 429)
        if ping["status"] != "OPEN":
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="REJECTED",
                rejection_reason="expired",
            )
        if not self.store.insert_bid(envelope, ping):
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="DUPLICATE",
            )
        return build_ack(envelope, sender_id=self.config.platform_id, status="RECEIVED")

    # ─── Routing and delivery worker ───────────────────────────────────────

    def _process_routing_jobs(self) -> None:
        for job in self.store.claim_routing_jobs(self.worker_id):
            lead_id = job["lead_id"]
            try:
                self._route_pending_lead(lead_id)
                self.store.complete_routing_job(lead_id, self.worker_id)
            except Exception as exc:
                self.store.release_routing_job(
                    lead_id,
                    str(exc),
                    self.worker_id,
                    max_attempts=len(self._retry_delays),
                )

    def _route_pending_lead(self, lead_id: str) -> None:
        row = self.store.get_lead(lead_id)
        if not row or row["status"] != "NEW":
            return
        if self.store.is_lead_suppressed(lead_id):
            return
        if is_expired(parse_iso_datetime(row["expires_at"])):
            self.store.expire_lead(lead_id)
            return
        if is_expired(parse_iso_datetime(row["consent_expires_at"])):
            self.store.suppress_lead(
                lead_id,
                reason="Consent expired",
                actor_id=self.config.platform_id,
            )
            return
        envelope = self.store.decode_envelope(row["envelope_json"])
        payload = envelope["lcp"]["payload"]
        offers = self.store.list_offer_candidates(
            payload,
            tenant_id=self.config.routing_tenant_id,
        )
        matches: list[tuple[dict[str, Any], MatchResult]] = []
        for offer in offers:
            result = match_offer(offer, payload, sender_id=row["sender_id"])
            if result.matched and not self._capacity_available(offer):
                result = MatchResult(False, result.reasons + ("capacity_exceeded",))
            matches.append((offer, result))
            self.store.insert_match_decision(
                lead_id=lead_id,
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                matched=result.matched,
                reasons=result.reasons,
            )
        matching_offers = [offer for offer, result in matches if result.matched]
        direct_offers = [
            offer for offer in matching_offers if offer.get("routing_mode", "auction") == "direct"
        ]
        if direct_offers:
            direct_offers.sort(key=self._pacing_priority)
            exclusivity = payload.get("exclusivity", {})
            if exclusivity.get("exclusivity", "shared") == "exclusive":
                matching_offers = direct_offers[:1]
            else:
                matching_offers = direct_offers[: max(1, int(exclusivity.get("max_buyers", 1)))]
        created_pings = 0
        created_posts = 0
        for offer in matching_offers:
            if offer.get("routing_mode", "auction") == "direct":
                if not self.store.has_delivery_for_offer(lead_id, offer["offer_id"], "post") and self._create_direct_post(envelope, offer):
                    created_posts += 1
            elif not self.store.has_ping_for_offer(lead_id, offer["offer_id"]):
                if self._create_ping(envelope, offer):
                    created_pings += 1
        if created_posts:
            self.store.update_lead_status(lead_id, "POSTED", reason="direct_delivery")
        elif created_pings:
            self.store.update_lead_status(lead_id, "PINGED")

    def process_once(self) -> None:
        """Process retention, durable routing jobs, webhooks, and auction expiry."""
        for lead in self.store.list_expired_leads():
            self.store.expire_lead(lead["lead_id"])
        for lead in self.store.list_consent_expired_leads():
            self.store.suppress_lead(
                lead["lead_id"],
                reason="Consent expired",
                actor_id=self.config.platform_id,
            )
        for attachment in self.store.list_expired_attachments():
            self.store.expire_attachment(attachment["attachment_id"])
        self._process_attachment_deletions()
        self._process_routing_jobs()
        self._process_deliveries()
        self._process_attachment_deletions()
        for ping in self.store.list_expired_open_pings():
            self.store.expire_ping(ping["ping_id"])
            if self.store.all_pings_terminal(ping["lead_id"]):
                self._route_lead(ping["lead_id"])

    def _create_direct_post(self, lead_envelope: dict[str, Any], offer: dict[str, Any]) -> bool:
        secret = self.auth.secret_for(offer["buyer_id"])
        webhook_url = offer.get("webhook_url")
        if not secret or not webhook_url:
            return False
        if self.store.has_delivery_for_offer(
            lead_envelope["lcp"]["payload"]["lead_id"], offer["offer_id"], "post"
        ):
            return False
        post = build_post(
            lead_envelope,
            offer,
            platform_id=self.config.platform_id,
            price_cents=offer["floor_price_cents"],
            buyer_reference=None,
            correlation_id=lead_envelope["lcp"]["message"]["id"],
        )
        self.validator.require_valid_envelope(post)
        inserted = self.store.insert_delivery(
            lead_id=lead_envelope["lcp"]["payload"]["lead_id"],
            ping_id=None,
            offer_id=offer["offer_id"],
            buyer_id=offer["buyer_id"],
            kind="post",
            envelope=post,
            webhook_url=webhook_url,
        )
        if inserted:
            self._record_pending_payable(lead_envelope, offer, offer["floor_price_cents"])
        return inserted

    @staticmethod
    def _month_key(offer: dict[str, Any], at: datetime | None = None) -> str:
        current = at or datetime.now(timezone.utc)
        timezone_name = offer.get("monthly_quota_timezone", "UTC")
        try:
            current = current.astimezone(ZoneInfo(timezone_name))
        except (KeyError, ValueError):
            current = current.astimezone(timezone.utc)
        return current.strftime("%Y-%m")

    def quota_status(self, offer_id: str, at: datetime | None = None) -> dict[str, Any]:
        offer = self.store.get_offer(offer_id)
        if not offer:
            raise RequestError("Offer not found", "LCP-003", 404)
        month_key = self._month_key(offer, at)
        summary = self.store.payable_summary(offer_id, month_key)
        minimum = int(offer.get("monthly_minimum_payable", 0))
        maximum = offer.get("monthly_maximum_payable")
        payable = summary["payable"]
        remaining = max(0, minimum - payable)
        now = at or datetime.now(timezone.utc)
        try:
            local = now.astimezone(ZoneInfo(offer.get("monthly_quota_timezone", "UTC")))
        except (KeyError, ValueError):
            local = now.astimezone(timezone.utc)
        last_day = ((local.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
        days_remaining = max(1, last_day - local.day + 1)
        return {
            "offer_id": offer_id,
            "month": month_key,
            "minimum_payable": minimum,
            "maximum_payable": maximum,
            "payable_remaining_to_minimum": remaining,
            "required_daily_pace": (remaining + days_remaining - 1) // days_remaining,
            "under_paced": payable < minimum and local.day > 1,
            "policy": offer.get("monthly_quota_policy", "monitor"),
            "summary": summary,
        }

    def _pacing_priority(self, offer: dict[str, Any]) -> tuple[int, int, str]:
        minimum = int(offer.get("monthly_minimum_payable", 0))
        if minimum <= 0:
            return (1, 0, str(offer.get("offer_id", "")))
        summary = self.store.payable_summary(offer["offer_id"], self._month_key(offer))
        remaining = max(0, minimum - summary["payable"])
        return (0 if remaining else 1, -remaining, str(offer.get("offer_id", "")))

    def _record_pending_payable(
        self,
        lead_envelope: dict[str, Any],
        offer: dict[str, Any],
        price_cents: int,
    ) -> None:
        payload = lead_envelope["lcp"]["payload"]
        self.store.record_payable(
            offer_id=offer["offer_id"],
            lead_id=payload["lead_id"],
            buyer_id=offer["buyer_id"],
            month_key=self._month_key(offer),
            channel=payload.get("channel", "unknown"),
            status="pending",
            price_cents=price_cents,
            currency=offer["currency"],
            reason="awaiting_delivery_or_call_outcome",
        )

    def _capacity_available(self, offer: dict[str, Any]) -> bool:
        now = datetime.now(timezone.utc)
        if offer.get("daily_cap"):
            day_start = now.strftime("%Y-%m-%dT00:00:00Z")
            if self.store.count_offer_deliveries(offer["offer_id"], since=day_start) >= offer["daily_cap"]:
                return False
        if offer.get("hourly_cap"):
            hour_start = now.strftime("%Y-%m-%dT%H:00:00Z")
            if self.store.count_offer_deliveries(offer["offer_id"], since=hour_start) >= offer["hourly_cap"]:
                return False
        if offer.get("monthly_maximum_payable") and offer.get("monthly_quota_policy") == "hard_cap":
            summary = self.store.payable_summary(offer["offer_id"], self._month_key(offer, now))
            if summary["payable"] + summary["pending"] >= int(offer["monthly_maximum_payable"]):
                return False
        return True

    def _create_ping(self, lead_envelope: dict[str, Any], offer: dict[str, Any]) -> bool:
        secret = self.auth.secret_for(offer["buyer_id"])
        if not secret:
            # A buyer without a bilateral secret cannot receive a safe ping.
            return False
        lead_id = lead_envelope["lcp"]["payload"]["lead_id"]
        if self.store.has_ping_for_offer(lead_id, offer["offer_id"]):
            return False
        ping, expires_at = build_ping(
            lead_envelope,
            offer,
            platform_id=self.config.platform_id,
            buyer_secret=secret,
        )
        self.validator.require_valid_envelope(ping)
        inserted = self.store.insert_ping(
            ping,
            lead_id=lead_id,
            offer_id=offer["offer_id"],
            buyer_id=offer["buyer_id"],
            expires_at=expires_at,
        )
        if not inserted:
            return False
        webhook_url = offer.get("webhook_url")
        if webhook_url:
            self.store.insert_delivery(
                lead_id=lead_id,
                ping_id=ping["lcp"]["payload"]["ping_id"],
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                kind="ping",
                envelope=ping,
                webhook_url=webhook_url,
            )
        return True

    def _route_lead(self, lead_id: str) -> None:
        row = self.store.get_lead(lead_id)
        if not row or row["status"] in {"ACCEPTED", "REJECTED", "EXPIRED", "DUPLICATE", "ERASED"}:
            return
        if self.store.is_lead_suppressed(lead_id):
            return
        if is_expired(parse_iso_datetime(row["expires_at"])):
            self.store.expire_lead(lead_id)
            return
        if is_expired(parse_iso_datetime(row["consent_expires_at"])):
            self.store.suppress_lead(
                lead_id,
                reason="Consent expired",
                actor_id=self.config.platform_id,
            )
            return
        bids = self.store.list_bids_for_lead(lead_id)
        pings = {ping["ping_id"]: ping for ping in self.store.list_pings(lead_id)}
        candidates: list[tuple[sqlite3_row, sqlite3_row, dict[str, Any]]] = []
        for bid in bids:
            ping = pings.get(bid["ping_id"])
            offer = self.store.get_offer(bid["offer_id"])
            if not ping or not offer or bid["decision"] != "accept":
                continue
            if ping["buyer_id"] != offer["buyer_id"] or bid["buyer_id"] != offer["buyer_id"]:
                continue
            if not self._capacity_available(offer):
                continue
            if bid["bid_price_cents"] < offer["floor_price_cents"]:
                continue
            if bid["currency"] != offer["currency"]:
                continue
            candidates.append((bid, ping, offer))
        candidates.sort(
            key=lambda item: (
                -item[0]["bid_price_cents"],
                item[0]["estimated_contact_seconds"]
                if item[0]["estimated_contact_seconds"] is not None
                else 2**31,
                item[0]["received_at"],
                item[0]["buyer_id"],
            )
        )
        lead_payload = self.store.decode_envelope(row["envelope_json"])["lcp"]["payload"]
        exclusivity = lead_payload.get("exclusivity", {})
        max_winners = 1 if exclusivity.get("exclusivity", "shared") == "exclusive" else max(
            1, int(exclusivity.get("max_buyers", 1))
        )
        winners = candidates[:max_winners]
        if not winners:
            self.store.update_lead_status(lead_id, "EXPIRED")
            return

        lead_envelope = self.store.decode_envelope(row["envelope_json"])
        delivered = 0
        for bid, ping, offer in winners:
            if not offer.get("webhook_url"):
                continue
            if self.store.has_delivery_for_offer(lead_id, offer["offer_id"], "post"):
                self.store.mark_ping_won(ping["ping_id"])
                delivered += 1
                continue
            post = build_post(
                lead_envelope,
                offer,
                platform_id=self.config.platform_id,
                price_cents=bid["bid_price_cents"],
                buyer_reference=self.store.decode_envelope(bid["envelope_json"])["lcp"]["payload"].get("buyer_reference"),
                correlation_id=ping["message_id"],
            )
            self.validator.require_valid_envelope(post)
            if self.store.insert_delivery(
                lead_id=lead_id,
                ping_id=ping["ping_id"],
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                kind="post",
                envelope=post,
                webhook_url=offer["webhook_url"],
            ):
                self._record_pending_payable(lead_envelope, offer, bid["bid_price_cents"])
                self.store.mark_ping_won(ping["ping_id"])
                delivered += 1
        self.store.update_lead_status(lead_id, "POSTED" if delivered else "EXPIRED")

    def _process_attachment_deletions(self) -> None:
        for job in self.store.list_due_attachment_deletions():
            attempts = int(job["attempts"]) + 1
            try:
                self.attachments.delete(job["storage_ref"])
            except Exception as exc:  # deletion is durable; retry without exposing storage details
                if attempts >= self.config.max_delivery_attempts:
                    status = "FAILED"
                    next_attempt = now_iso()
                else:
                    status = "RETRY"
                    delay = self._retry_delays[min(attempts - 1, len(self._retry_delays) - 1)]
                    next_attempt = format_iso_datetime(
                        datetime.now(timezone.utc) + timedelta(seconds=delay)
                    )
                self.store.mark_attachment_deletion(
                    job["job_id"],
                    status=status,
                    attempts=attempts,
                    next_attempt_at=next_attempt,
                    last_error=f"{type(exc).__name__}: {exc}",
                )
            else:
                self.store.mark_attachment_deletion(
                    job["job_id"],
                    status="DONE",
                    attempts=attempts,
                    next_attempt_at=now_iso(),
                )

    def _process_deliveries(self) -> None:
        for delivery in self.store.claim_due_deliveries(self.worker_id):
            self._deliver(delivery)

    def _deliver(self, delivery: Any) -> None:
        envelope = self.store.decode_envelope(delivery["envelope_json"])
        secret = self.auth.secret_for(delivery["buyer_id"])
        attempts = int(delivery["attempts"]) + 1
        if not secret:
            error = "No active buyer HMAC secret"
            self.store.mark_delivery(
                delivery["delivery_id"],
                status="FAILED",
                attempts=attempts,
                next_attempt_at=now_iso(),
                last_error=error,
                worker_id=self.worker_id,
            )
            self.store.record_dead_letter(
                queue_type="delivery",
                resource_id=delivery["delivery_id"],
                lead_id=delivery["lead_id"],
                attempts=attempts,
                last_error=error,
            )
            return
        try:
            validate_webhook_url(delivery["webhook_url"], self.config)
            validate_egress_host(
                urlsplit(delivery["webhook_url"]).hostname or "",
                self.config,
            )
            body, headers = webhook_headers(envelope, secret=secret)
            with httpx.Client(
                timeout=self.config.webhook_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST", delivery["webhook_url"], content=body, headers=headers
                ) as response:
                    response_bytes = 0
                    for chunk in response.iter_bytes():
                        response_bytes += len(chunk)
                        if response_bytes > self.config.max_webhook_response_bytes:
                            raise SecurityPolicyError("Webhook response exceeded the configured limit")
                    response_status = response.status_code
            if response_status == 409 or 200 <= response_status < 300:
                self.store.mark_delivery(
                    delivery["delivery_id"],
                    status="DELIVERED",
                    attempts=attempts,
                    next_attempt_at=now_iso(),
                    worker_id=self.worker_id,
                )
                if delivery["kind"] == "post":
                    self._mark_post_delivered(delivery, envelope)
                    self._queue_delivery_event(delivery, envelope)
                return
            # Do not persist arbitrary buyer response bodies; they may contain PII.
            error = f"HTTP {response_status} from buyer webhook"
        except Exception as exc:  # delivery failures must enter the retry queue
            error = f"{type(exc).__name__}: {exc}"

        if attempts >= self.config.max_delivery_attempts:
            status = "FAILED"
            next_attempt = now_iso()
        else:
            status = "RETRY"
            delay = self._retry_delays[min(attempts - 1, len(self._retry_delays) - 1)]
            next_attempt = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.store.mark_delivery(
            delivery["delivery_id"],
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt,
            last_error=error,
            worker_id=self.worker_id,
        )
        if status == "FAILED":
            self.store.record_dead_letter(
                queue_type="delivery",
                resource_id=delivery["delivery_id"],
                lead_id=delivery["lead_id"],
                attempts=attempts,
                last_error=error,
            )

    def _queue_delivery_event(self, delivery: Any, post: dict[str, Any]) -> None:
        event = build_event(
            delivery["lead_id"],
            "DELIVERED",
            platform_id=self.config.platform_id,
            receiver_id=delivery["buyer_id"],
            correlation_id=post["lcp"]["message"]["id"],
            details={"delivery_id": delivery["delivery_id"], "offer_id": delivery["offer_id"]},
            test=post["lcp"]["message"].get("test", False),
        )
        self.validator.require_valid_envelope(event)
        if not self.store.insert_event(delivery["lead_id"], "DELIVERED", event):
            return
        self.store.insert_delivery(
            lead_id=delivery["lead_id"],
            ping_id=delivery["ping_id"],
            offer_id=delivery["offer_id"],
            buyer_id=delivery["buyer_id"],
            kind="event",
            envelope=event,
            webhook_url=delivery["webhook_url"],
        )

    def _mark_post_delivered(self, delivery: Any, post: dict[str, Any]) -> None:
        offer = self.store.get_offer(delivery["offer_id"])
        if not offer:
            return
        payload = post["lcp"]["payload"]
        rules = offer.get("payable_rules", {})
        if rules.get("mode", "post_delivery") == "call_outcome":
            status, reason, seconds = "pending", "awaiting_call_outcome", None
        else:
            status, reason, seconds = "payable", "post_delivery_acknowledged", None
        self.store.record_payable(
            offer_id=offer["offer_id"],
            lead_id=delivery["lead_id"],
            buyer_id=delivery["buyer_id"],
            month_key=self._month_key(offer),
            channel=payload.get("channel", "call" if "call" in payload else "lead"),
            status=status,
            price_cents=int(payload.get("price_cents", 0)),
            currency=str(payload.get("currency", offer.get("currency", ""))),
            reason=reason,
            call_seconds=seconds,
        )

    def _evaluate_call_outcome(
        self,
        offer: dict[str, Any],
        details: dict[str, Any],
    ) -> tuple[str, str, int | None]:
        rules = offer.get("payable_rules", {})
        seconds_value = details.get("total_seconds", details.get("call_seconds"))
        seconds = int(seconds_value) if isinstance(seconds_value, (int, float)) else None
        call_status = details.get("call_status", details.get("status"))
        if rules.get("require_call_answered") and call_status not in {"answered", "connected"}:
            return "not_payable", "call_not_answered", seconds
        minimum = int(rules.get("minimum_call_seconds", 0))
        if seconds is None and minimum > 0:
            return "not_payable", "call_duration_missing", None
        if seconds is not None and seconds < minimum:
            return "not_payable", "call_below_minimum_duration", seconds
        allowed = rules.get("allowed_call_dispositions", [])
        if allowed and details.get("disposition") not in allowed:
            return "not_payable", "call_disposition_not_payable", seconds
        return "payable", "call_outcome_met_rules", seconds

    def submit_event(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        headers = headers or {}
        self._validate_envelope(envelope)
        message = envelope["lcp"]["message"]
        if message["type"] != "event":
            raise RequestError("Event endpoint requires an event message", "LCP-006")
        self._validate_transport(message, headers)
        if message["receiver_id"] != self.config.platform_id:
            raise RequestError("Message receiver is not this platform", "LCP-002", 401)
        payload = envelope["lcp"]["payload"]
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body if raw_body is not None else self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
            required_scope="event:submit",
        )
        row = self.store.get_lead(payload["lead_id"])
        if not row:
            raise RequestError("Lead not found", "LCP-003", 404)
        event_name = payload["event"]
        deliveries = self.store.deliveries_for_lead(payload["lead_id"])
        is_admin = self.auth.has_scope(sender_id, "platform:admin")
        is_publisher = sender_id == row["sender_id"]
        is_delivered_buyer = self.store.has_delivered_post_for_buyer(
            payload["lead_id"], sender_id
        )
        authorized = (
            is_admin
            or (is_publisher and event_name in _PUBLISHER_EVENT_TYPES)
            or (is_delivered_buyer and event_name in _BUYER_EVENT_TYPES)
        )
        if not authorized:
            raise RequestError("Sender is not authorized for this lead event", "LCP-002", 401)

        existing_event = self.store.get_event(message["id"])
        if existing_event:
            try:
                same_event = (
                    existing_event["lead_id"] == payload["lead_id"]
                    and existing_event["event_name"] == event_name
                    and self.store.decode_envelope(existing_event["envelope_json"]) == envelope
                )
            except (KeyError, TypeError, ValueError):
                same_event = False
            if not same_event:
                raise RequestError(
                    "Event message ID was reused with different content",
                    "LCP-005",
                    409,
                )
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="DUPLICATE",
                lead_id=payload["lead_id"],
            )

        target_status = _EVENT_STATUS_TARGETS.get(event_name)
        if target_status:
            try:
                previous_status = self.store.update_lead_status(
                    payload["lead_id"], target_status
                )
            except InvalidStatusTransition as exc:
                raise RequestError(str(exc), "LCP-004", 422) from exc
            if previous_status is not None:
                self.store.insert_audit(
                    tenant_id=self.auth.tenant_for(sender_id)
                    or self.config.routing_tenant_id,
                    actor_id=sender_id,
                    action=f"lead.{target_status.lower()}",
                    resource_type="lead",
                    resource_id=payload["lead_id"],
                    metadata={
                        "event": event_name,
                        "previous_status": previous_status,
                    },
                )

        try:
            inserted_event = self.store.insert_event(payload["lead_id"], event_name, envelope)
        except EventIdempotencyConflict as exc:
            raise RequestError(str(exc), "LCP-005", 409) from exc
        if not inserted_event:
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="DUPLICATE",
                lead_id=payload["lead_id"],
            )

        if event_name == "CONSENT_WITHDRAWN":
            self.store.suppress_lead(
                payload["lead_id"],
                reason="Consent withdrawn",
                actor_id=sender_id,
            )
        elif event_name == "ERASURE_REQUEST":
            self.erase_lead(payload["lead_id"], actor_id=sender_id)

        details = payload.get("details", {})
        offer_id = details.get("offer_id") if isinstance(details, dict) else None
        post_delivery = next(
            (delivery for delivery in deliveries
             if delivery["kind"] == "post" and (not offer_id or delivery["offer_id"] == offer_id)
             and (is_admin or sender_id == delivery["buyer_id"] or sender_id == row["sender_id"])),
            None,
        )
        if post_delivery:
            offer = self.store.get_offer(post_delivery["offer_id"])
            if offer:
                post = self.store.decode_envelope(post_delivery["envelope_json"])
                status = None
                reason = None
                seconds = None
                if event_name in {"DISPUTED", "REFUNDED"}:
                    status, reason = event_name.lower(), f"lifecycle_{event_name.lower()}"
                elif event_name in {"CALL_OUTCOME", "CALL_ENDED", "CALL_CONNECTED"}:
                    status, reason, seconds = self._evaluate_call_outcome(offer, details)
                elif event_name in {"DELIVERED", "ACCEPTED"}:
                    status, reason = "payable", "buyer_acceptance"
                if status:
                    post_payload = post["lcp"]["payload"]
                    self.store.record_payable(
                        offer_id=offer["offer_id"],
                        lead_id=payload["lead_id"],
                        buyer_id=offer["buyer_id"],
                        month_key=self._month_key(offer),
                        channel=post_payload.get("channel", "call" if "call" in post_payload else "lead"),
                        status=status,
                        price_cents=int(post_payload.get("price_cents", 0)),
                        currency=str(post_payload.get("currency", offer.get("currency", ""))),
                        reason=reason,
                        call_seconds=seconds,
                    )
        return build_ack(envelope, sender_id=self.config.platform_id, status="RECEIVED", lead_id=payload["lead_id"])

    def upload_attachment(self, *, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        self._validate_test_header(headers)
        owner_id = header(headers, "X-LCP-Sender-Id")
        idempotency_key = header(headers, "X-LCP-Idempotency-Key")
        lead_id = header(headers, "X-LCP-Lead-Id")
        attachment_id = header(headers, "X-LCP-Attachment-Id") or f"att_{uuid4().hex}"
        purpose = header(headers, "X-LCP-Attachment-Purpose") or "supporting_document"
        filename = header(headers, "X-LCP-Filename") or "attachment.bin"
        content_type = (header(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
        digest = (header(headers, "X-LCP-Content-SHA256") or "").lower()
        expires_header = header(headers, "X-LCP-Attachment-Expires-At")
        expires_value = parse_iso_datetime(expires_header) if expires_header else None
        if expires_header and expires_value is None:
            raise RequestError("Attachment expiry must be an RFC 3339 date-time", "LCP-100")
        if expires_value is not None and expires_value <= datetime.now(timezone.utc):
            raise RequestError("Attachment expiry must be in the future", "LCP-100")
        if not owner_id or not idempotency_key or not lead_id or not digest:
            raise RequestError("Attachment identity, lead, idempotency, and content hash headers are required", "LCP-003")
        self.auth.authenticate(
            sender_id=owner_id,
            headers=headers,
            body=body,
            idempotency_key=idempotency_key,
            mutating=True,
            required_scope="attachment:write",
        )
        if len(body) == 0 or len(body) > self.config.max_attachment_bytes:
            raise RequestError("Attachment size is outside the configured limit", "LCP-001", 413)
        if content_type not in self.config.allowed_attachment_content_types:
            raise RequestError("Attachment content type is not allowed", "LCP-100", 415)
        try:
            FileAttachmentStore.validate_metadata(
                attachment_id=attachment_id, filename=filename, content_type=content_type, size_bytes=len(body)
            )
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or hashlib.sha256(body).hexdigest() != digest:
            raise RequestError("Attachment content hash is invalid", "LCP-100")
        residency_header = header(headers, "X-LCP-Data-Residency")
        residency_value = residency_header or self.config.attachment_residency
        if not residency_value:
            if self.config.test_mode:
                residency_value = "TEST"
            else:
                raise RequestError("Attachment data residency is required", "LCP-100")
        try:
            residency = validate_residency(residency_value)
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        if self.config.attachment_allowed_residencies and residency not in {
            validate_residency(value) for value in self.config.attachment_allowed_residencies
        }:
            raise RequestError("Attachment residency is not permitted by this deployment", "LCP-100")
        try:
            scan = self.attachment_scanner.scan(
                body,
                filename=filename,
                content_type=content_type,
            )
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        if self.config.attachment_scan_required and scan.status != "clean":
            raise RequestError("Attachment did not pass the required malware scan", "LCP-100")
        existing = self.store.get_attachment_by_idempotency(owner_id, idempotency_key)
        if existing:
            if existing["sha256"] != digest or existing["lead_id"] != lead_id:
                raise RequestError("Attachment idempotency key was reused with different content", "LCP-005", 409)
            return self._attachment_metadata(existing)
        existing_id = self.store.get_attachment(attachment_id)
        if existing_id:
            if existing_id["owner_id"] == owner_id and existing_id["sha256"] == digest and existing_id["lead_id"] == lead_id:
                return self._attachment_metadata(existing_id)
            raise RequestError("Attachment ID is already assigned to another file", "LCP-005", 409)
        try:
            storage_ref = self.attachments.put(
                attachment_id,
                body,
                sha256_hex=digest,
                content_type=content_type,
                filename=filename,
                residency=residency,
            )
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-500", 500) from exc
        metadata = {
            "attachment_id": attachment_id, "lead_id": lead_id, "owner_id": owner_id,
            "idempotency_key": idempotency_key, "purpose": purpose, "filename": filename,
            "content_type": content_type, "size_bytes": len(body), "sha256": digest,
            "storage_ref": storage_ref, "residency": residency,
            "scan_status": scan.status, "scan_engine": scan.engine, "scanned_at": scan.scanned_at,
            "encryption": self.attachments.encryption,
            "expires_at": format_iso_datetime(expires_value) if expires_value else None,
        }
        if not self.store.insert_attachment(metadata):
            existing = self.store.get_attachment_by_idempotency(owner_id, idempotency_key)
            self.attachments.delete(storage_ref)
            if existing:
                return self._attachment_metadata(existing)
            raise RequestError("Attachment could not be stored", "LCP-500", 500)
        self.store.insert_audit(
            tenant_id=self.auth.tenant_for(owner_id) or self.config.routing_tenant_id,
            actor_id=owner_id,
            action="attachment.uploaded",
            resource_type="attachment",
            resource_id=attachment_id,
            metadata={"lead_id": lead_id, "purpose": purpose, "size_bytes": len(body), "sha256": digest},
        )
        row = self.store.get_attachment(attachment_id)
        assert row is not None
        return self._attachment_metadata(row)

    @staticmethod
    def _attachment_metadata(row: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "attachment_id": row["attachment_id"],
            "purpose": row["purpose"], "filename": row["filename"],
            "content_type": row["content_type"], "size_bytes": row["size_bytes"],
            "sha256": row["sha256"], "storage_ref": row["storage_ref"], "created_at": row["created_at"],
            "residency": row["residency"],
            "malware_scan": {
                "status": row["scan_status"],
                "engine": row["scan_engine"],
                "scanned_at": row["scanned_at"],
            },
            "encryption": row["encryption"],
        }
        if row["expires_at"] is not None:
            metadata["expires_at"] = row["expires_at"]
        return metadata

    def download_attachment(self, attachment_id: str, *, headers: dict[str, str]) -> tuple[bytes, dict[str, str]]:
        row = self.store.get_attachment(attachment_id)
        if not row or row["status"] != "AVAILABLE":
            raise RequestError("Attachment not found", "LCP-003", 404)
        if is_expired(parse_iso_datetime(row["expires_at"])):
            self.store.expire_attachment(attachment_id)
            self._process_attachment_deletions()
            raise RequestError("Attachment not found", "LCP-003", 404)
        sender_id = self.auth.authenticate(sender_id=header(headers, "X-LCP-Sender-Id"), headers=headers, body=b"", mutating=False)
        authorized = (
            sender_id == row["owner_id"]
            or self.auth.has_scope(sender_id, "platform:admin")
            or self.store.has_delivered_post_for_buyer(row["lead_id"], sender_id)
        )
        if not authorized:
            self.store.insert_audit(
                tenant_id=self.auth.tenant_for(row["owner_id"]) or self.config.routing_tenant_id,
                actor_id=sender_id,
                action="attachment.download_denied",
                resource_type="attachment",
                resource_id=attachment_id,
                metadata={"lead_id": row["lead_id"]},
            )
            raise RequestError("Attachment not found", "LCP-003", 404)
        self.store.insert_audit(
            tenant_id=self.auth.tenant_for(row["owner_id"]) or self.config.routing_tenant_id,
            actor_id=sender_id,
            action="attachment.downloaded",
            resource_type="attachment",
            resource_id=attachment_id,
            metadata={"lead_id": row["lead_id"]},
        )
        try:
            content = self.attachments.read(row["storage_ref"], expected_sha256=row["sha256"])
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-500", 500) from exc
        return content, {"Content-Type": row["content_type"], "Content-Disposition": f'attachment; filename="{row["filename"]}"'}

    def _validate_test_header(self, headers: dict[str, str]) -> None:
        marker = header(headers, "X-LCP-Test")
        is_test = marker is not None and marker.lower() == "true"
        if self.config.test_mode and not is_test:
            raise RequestError("Sandbox requests require X-LCP-Test: true", "LCP-013")
        if not self.config.test_mode and is_test:
            raise RequestError("Test messages must use a sandbox endpoint", "LCP-013")

    # ─── Read APIs ─────────────────────────────────────────────────────────

    def erase_lead(self, lead_id: str, *, actor_id: str = "operator") -> None:
        if not self.store.erase_lead(lead_id, actor_id=actor_id):
            raise RequestError("Lead not found", "LCP-003", 404)
        # Deletion is best effort here but durable in attachment_deletion_jobs;
        # the worker retries failures on subsequent process_once calls.
        self._process_attachment_deletions()

    def authorize_lead_read(self, lead_id: str, sender_id: str) -> None:
        row = self.store.get_lead(lead_id)
        if not row:
            raise RequestError("Lead not found", "LCP-003", 404)
        if sender_id == row["sender_id"]:
            return
        if self.auth.has_scope(sender_id, "platform:admin"):
            return
        # A ping, event, pending post, or failed delivery never grants a read
        # view over the lead's commercial or attachment metadata.
        if self.store.has_delivered_post_for_buyer(lead_id, sender_id):
            return
        raise RequestError("Lead not found", "LCP-003", 404)

    def lead_status(self, lead_id: str, requester_id: str | None = None) -> dict[str, Any]:
        row = self.store.get_lead(lead_id)
        if not row:
            raise RequestError("Lead not found", "LCP-003", 404)
        envelope = self.store.decode_envelope(row["envelope_json"])
        role = "internal"
        if requester_id is not None:
            if requester_id == row["sender_id"]:
                role = "publisher"
            elif self.auth.has_scope(requester_id, "platform:admin"):
                role = "admin"
            elif self.store.has_delivered_post_for_buyer(lead_id, requester_id):
                role = "buyer"
            else:
                raise RequestError("Lead not found", "LCP-003", 404)

        events = self.store.list_events(lead_id)
        decisions = self.store.list_match_decisions(lead_id)
        payables = self.store.payable_for_lead(lead_id)
        attachments = self.store.list_attachments(lead_id)
        if role == "buyer":
            winning_offer_ids = {
                delivery["offer_id"]
                for delivery in self.store.deliveries_for_lead(lead_id)
                if delivery["kind"] == "post"
                and delivery["buyer_id"] == requester_id
                and delivery["status"] == "DELIVERED"
            }
            decisions = [decision for decision in decisions if decision["buyer_id"] == requester_id]
            payables = [payable for payable in payables if payable["buyer_id"] == requester_id]
            filtered_events = []
            for event in events:
                payload = event["lcp"].get("payload", {})
                details = payload.get("details", {})
                sender = event["lcp"].get("message", {}).get("sender_id")
                if sender == requester_id or details.get("offer_id") in winning_offer_ids:
                    filtered_events.append(payload)
            events_view = filtered_events
        else:
            events_view = [event["lcp"]["payload"] for event in events]

        result: dict[str, Any] = {
            "lead_id": lead_id,
            "status": row["status"],
            "channel": envelope["lcp"]["payload"].get("channel"),
            "view": role,
            "events": events_view,
            "match_decisions": decisions,
            "payable_records": payables,
            "attachments": [self._attachment_metadata(attachment) for attachment in attachments],
        }
        if row["expires_at"] is not None:
            result["expires_at"] = row["expires_at"]
        if row["consent_expires_at"] is not None:
            result["consent_expires_at"] = row["consent_expires_at"]
        return result

    def capabilities(self) -> dict[str, Any]:
        return {
            "lcp_versions": ["1.0.0"],
            "message_types": ["lead", "call", "ping", "bid", "post", "ack", "event"],
            "verticals": [
                {"id": path.stem, "schema_version": "1.0.0"}
                for path in (self.validator.vertical_root.glob("*.json"))
            ],
            "countries": [],
            "auth_methods": ["bearer", "hmac"],
            "events": ["DELIVERED", "ACCEPTED", "REJECTED", "DISPUTED", "REFUNDED", "EXPIRED", "CONVERTED", "ARCHIVED", "CALL_OFFERED", "CALL_CONNECTED", "CALL_ENDED", "CALL_OUTCOME"],
            "attachments": {"upload": True, "authenticated_download": True, "max_bytes": self.config.max_attachment_bytes},
            "conformance_level": "L3",
        }

    def public_offers(self, vertical: str | None = None) -> dict[str, Any]:
        offers = []
        for offer in self.store.list_offers(
            vertical=vertical,
            tenant_id=self.config.routing_tenant_id,
        ):
            public = dict(offer)
            public.pop("webhook_url", None)
            public.pop("extensions", None)
            offers.append(public)
        return {"offers": offers}

    def schema(self, name: str) -> dict[str, Any]:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RequestError("Schema not found", "LCP-007", 404)
        if name.startswith("verticals/"):
            path = self.validator.vertical_root / f"{name.removeprefix('verticals/')}.json"
        else:
            path = self.validator.schema_root / f"{name}.json"
        if not path.exists():
            raise RequestError("Schema not found", "LCP-007", 404)
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _body(envelope: dict[str, Any]) -> bytes:
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def _validate_transport(self, message: dict[str, Any], headers: dict[str, str]) -> None:
        test_header = next(
            (value for key, value in headers.items() if key.lower() == "x-lcp-test"),
            None,
        )
        header_is_test = test_header is not None and test_header.lower() == "true"
        message_is_test = bool(message.get("test", False))
        if test_header is not None and header_is_test != message_is_test:
            raise RequestError("X-LCP-Test must match envelope test", "LCP-013", 400)
        if self.config.test_mode and not message_is_test:
            raise RequestError("Sandbox accepts only test messages", "LCP-013", 400)
        if not self.config.test_mode and message_is_test:
            raise RequestError("Test messages must use a sandbox endpoint", "LCP-013", 400)
        if self.config.test_mode and not header_is_test:
            raise RequestError("Sandbox requests require X-LCP-Test: true", "LCP-013", 400)

    def _validate_attachment_references(self, payload: dict[str, Any], owner_id: str) -> None:
        for attachment in payload.get("attachments", []):
            attachment_id = attachment.get("attachment_id")
            row = self.store.get_attachment(str(attachment_id)) if attachment_id else None
            if not row or row["status"] != "AVAILABLE":
                raise RequestError("Referenced attachment is not available", "LCP-003")
            if row["owner_id"] != owner_id or row["lead_id"] != payload.get("lead_id"):
                raise RequestError("Referenced attachment is not owned by this publisher lead", "LCP-002", 401)
            for field in ("filename", "content_type", "size_bytes", "sha256", "storage_ref", "residency", "encryption"):
                if field in attachment and str(attachment[field]) != str(row[field]):
                    raise RequestError("Referenced attachment metadata does not match stored content", "LCP-100")

    def _validate_envelope(self, envelope: dict[str, Any]) -> None:
        try:
            self.validator.require_valid_envelope(envelope)
        except ValidationError as exc:
            raise RequestError(
                "LCP validation failed",
                "LCP-100",
                400,
                details=exc.errors,
            ) from exc


# sqlite3.Row is intentionally only used as a structural type in this module.
sqlite3_row = Any

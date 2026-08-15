"""Production-oriented LCP platform and routing orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any
import httpx

from .auth import Authenticator
from .config import PlatformConfig
from .matching import MatchResult, match_offer
from .messages import (
    build_ack,
    build_event,
    build_ping,
    build_post,
    webhook_headers,
)
from .storage import Store, now_iso
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


class Platform:
    """Coordinates persistence, validation, matching, and delivery."""

    def __init__(self, config: PlatformConfig, store: Store | None = None):
        self.config = config
        self.store = store or Store(config.database_path)
        self.validator = SchemaValidator(config.schema_root)
        self.auth = Authenticator(self.store, config)
        self._retry_delays = (1, 5, 30, 120, 600)

    def close(self) -> None:
        self.store.close()

    # ─── Administrative configuration ──────────────────────────────────────

    def upsert_credential(self, sender_id: str, **kwargs: Any) -> None:
        self.store.upsert_credential(sender_id, **kwargs)

    def upsert_offer(self, offer: dict[str, Any]) -> None:
        self.validator.require_valid_offer(offer)
        self.store.upsert_offer(offer)

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
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body or self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
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
            if self._body(json.loads(existing["envelope_json"])) != self._body(envelope):
                raise RequestError(
                    "Idempotency key was reused with a different message",
                    "LCP-005",
                    409,
                )
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="DUPLICATE",
                lead_id=existing["lead_id"],
            )

        payload = envelope["lcp"]["payload"]
        offers = self.store.list_offers(vertical=payload.get("attributes", {}).get("vertical"))
        matches: list[tuple[dict[str, Any], MatchResult]] = []
        for offer in offers:
            result = match_offer(offer, payload)
            if result.matched and not self._capacity_available(offer):
                result = MatchResult(False, result.reasons + ("capacity_exceeded",))
            matches.append((offer, result))
        matching_offers = [offer for offer, result in matches if result.matched]
        direct_offers = [
            offer for offer in matching_offers if offer.get("routing_mode", "auction") == "direct"
        ]
        if direct_offers:
            lead_exclusivity = payload.get("exclusivity", {})
            if lead_exclusivity.get("exclusivity", "shared") == "exclusive":
                matching_offers = direct_offers[:1]
            else:
                matching_offers = direct_offers[: max(1, int(lead_exclusivity.get("max_buyers", 1)))]
        if not self.store.insert_lead(envelope, status="NEW"):
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
        for offer in matching_offers:
            if offer.get("routing_mode", "auction") == "direct":
                if self._create_direct_post(envelope, offer):
                    created_direct_posts += 1
            elif self._create_ping(envelope, offer):
                created_pings += 1
        if created_direct_posts:
            self.store.update_lead_status(payload["lead_id"], "POSTED")
        elif created_pings:
            self.store.update_lead_status(payload["lead_id"], "PINGED")

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
        payload = envelope["lcp"]["payload"]
        ping = self.store.get_ping(payload["ping_id"])
        if not ping:
            raise RequestError("Unknown ping_id", "LCP-003", 404)
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body or self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
        )
        if sender_id != ping["buyer_id"]:
            raise RequestError("Buyer is not authorized for this ping", "LCP-002", 401)
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

    def process_once(self) -> None:
        """Process due webhook attempts and expired auction windows."""
        self._process_deliveries()
        for ping in self.store.list_expired_open_pings():
            self.store.expire_ping(ping["ping_id"])
            if self.store.all_pings_terminal(ping["lead_id"]):
                self._route_lead(ping["lead_id"])

    def _create_direct_post(self, lead_envelope: dict[str, Any], offer: dict[str, Any]) -> bool:
        secret = self.auth.secret_for(offer["buyer_id"])
        webhook_url = offer.get("webhook_url")
        if not secret or not webhook_url:
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
        self.store.insert_delivery(
            lead_id=lead_envelope["lcp"]["payload"]["lead_id"],
            ping_id=None,
            offer_id=offer["offer_id"],
            buyer_id=offer["buyer_id"],
            kind="post",
            envelope=post,
            webhook_url=webhook_url,
        )
        return True

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
        return True

    def _create_ping(self, lead_envelope: dict[str, Any], offer: dict[str, Any]) -> bool:
        secret = self.auth.secret_for(offer["buyer_id"])
        if not secret:
            # A buyer without a bilateral secret cannot receive a safe ping.
            return False
        ping, expires_at = build_ping(
            lead_envelope,
            offer,
            platform_id=self.config.platform_id,
            buyer_secret=secret,
        )
        self.validator.require_valid_envelope(ping)
        self.store.insert_ping(
            ping,
            lead_id=lead_envelope["lcp"]["payload"]["lead_id"],
            offer_id=offer["offer_id"],
            buyer_id=offer["buyer_id"],
            expires_at=expires_at,
        )
        webhook_url = offer.get("webhook_url")
        if webhook_url:
            self.store.insert_delivery(
                lead_id=lead_envelope["lcp"]["payload"]["lead_id"],
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
        if not row or row["status"] in {"ACCEPTED", "REJECTED", "EXPIRED", "DUPLICATE"}:
            return
        bids = self.store.list_bids_for_lead(lead_id)
        pings = {ping["ping_id"]: ping for ping in self.store.list_pings(lead_id)}
        candidates: list[tuple[sqlite3_row, sqlite3_row, dict[str, Any]]] = []
        for bid in bids:
            ping = pings.get(bid["ping_id"])
            offer = self.store.get_offer(bid["offer_id"])
            if not ping or not offer or bid["decision"] != "accept":
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
        lead_payload = json.loads(row["envelope_json"])["lcp"]["payload"]
        exclusivity = lead_payload.get("exclusivity", {})
        max_winners = 1 if exclusivity.get("exclusivity", "shared") == "exclusive" else max(
            1, int(exclusivity.get("max_buyers", 1))
        )
        winners = candidates[:max_winners]
        if not winners:
            self.store.update_lead_status(lead_id, "EXPIRED")
            return

        lead_envelope = json.loads(row["envelope_json"])
        delivered = 0
        for bid, ping, offer in winners:
            if not offer.get("webhook_url"):
                continue
            post = build_post(
                lead_envelope,
                offer,
                platform_id=self.config.platform_id,
                price_cents=bid["bid_price_cents"],
                buyer_reference=json.loads(bid["envelope_json"])["lcp"]["payload"].get("buyer_reference"),
                correlation_id=ping["message_id"],
            )
            self.validator.require_valid_envelope(post)
            self.store.insert_delivery(
                lead_id=lead_id,
                ping_id=ping["ping_id"],
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                kind="post",
                envelope=post,
                webhook_url=offer["webhook_url"],
            )
            self.store.mark_ping_won(ping["ping_id"])
            delivered += 1
        self.store.update_lead_status(lead_id, "POSTED" if delivered else "EXPIRED")

    def _process_deliveries(self) -> None:
        for delivery in self.store.list_due_deliveries():
            self._deliver(delivery)

    def _deliver(self, delivery: Any) -> None:
        envelope = json.loads(delivery["envelope_json"])
        secret = self.auth.secret_for(delivery["buyer_id"])
        attempts = int(delivery["attempts"]) + 1
        if not secret:
            self.store.mark_delivery(
                delivery["delivery_id"],
                status="FAILED",
                attempts=attempts,
                next_attempt_at=now_iso(),
                last_error="No active buyer HMAC secret",
            )
            return
        try:
            body, headers = webhook_headers(envelope, secret=secret)
            with httpx.Client(timeout=self.config.webhook_timeout_seconds) as client:
                response = client.post(delivery["webhook_url"], content=body, headers=headers)
            if response.status_code == 409 or 200 <= response.status_code < 300:
                self.store.mark_delivery(
                    delivery["delivery_id"],
                    status="DELIVERED",
                    attempts=attempts,
                    next_attempt_at=now_iso(),
                )
                if delivery["kind"] == "post":
                    self._queue_delivery_event(delivery, envelope)
                return
            error = f"HTTP {response.status_code}: {response.text[:500]}"
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
        )

    def _queue_delivery_event(self, delivery: Any, post: dict[str, Any]) -> None:
        event = build_event(
            delivery["lead_id"],
            "DELIVERED",
            platform_id=self.config.platform_id,
            receiver_id=delivery["buyer_id"],
            correlation_id=post["lcp"]["message"]["id"],
            details={"delivery_id": delivery["delivery_id"]},
            test=post["lcp"]["message"].get("test", False),
        )
        self.validator.require_valid_envelope(event)
        self.store.insert_event(delivery["lead_id"], "DELIVERED", event)
        self.store.insert_delivery(
            lead_id=delivery["lead_id"],
            ping_id=delivery["ping_id"],
            offer_id=delivery["offer_id"],
            buyer_id=delivery["buyer_id"],
            kind="event",
            envelope=event,
            webhook_url=delivery["webhook_url"],
        )

    # ─── Read APIs ─────────────────────────────────────────────────────────

    def lead_status(self, lead_id: str) -> dict[str, Any]:
        row = self.store.get_lead(lead_id)
        if not row:
            raise RequestError("Lead not found", "LCP-003", 404)
        envelope = json.loads(row["envelope_json"])
        return {
            "lead_id": lead_id,
            "status": row["status"],
            "channel": envelope["lcp"]["payload"].get("channel"),
            "events": [event["lcp"]["payload"] for event in self.store.list_events(lead_id)],
            "match_decisions": self.store.list_match_decisions(lead_id),
        }

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
            "events": ["DELIVERED", "ACCEPTED", "REJECTED", "DISPUTED", "REFUNDED", "EXPIRED", "CONVERTED", "ARCHIVED"],
            "conformance_level": "L3",
        }

    def public_offers(self, vertical: str | None = None) -> dict[str, Any]:
        offers = []
        for offer in self.store.list_offers(vertical=vertical):
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

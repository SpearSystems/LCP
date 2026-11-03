"""Production-oriented LCP platform and routing orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from secrets import token_urlsafe
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5
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
from .attestation import (
    AgentAttestationVerifier,
    AttestationFailure,
    AttestationShapeError,
    check_attestation_shape,
    token_digest,
)
from .auth import Authenticator, header, signature_for
from .crypto import EnvelopeCipher
from .extensions import extension_problems, redact_extension_pii
from .mapping import MappingError, MappingRegistry, PublisherNormalizer, source_digest
from .config import PlatformConfig
from .database import create_store
from .matching import (
    MatchResult,
    ProfileValidationError,
    ServiceAreaCatalog,
    match_offer,
)
from .messages import (
    BID_FX_FIELDS,
    bid_fx_evidence,
    build_ack,
    build_event,
    build_ping,
    build_post,
    hash_negotiation_token,
    new_negotiation_token,
    pair_phone_hash,
    webhook_headers,
)
from .pii import has_plaintext_contact
from .security import SecurityPolicyError, validate_egress_host, validate_webhook_url
from .storage import (
    EventCursorError,
    EventIdempotencyConflict,
    InvalidStatusTransition,
    Store,
    decode_event_cursor,
    encode_event_cursor,
    envelope_expiry,
    format_iso_datetime,
    is_expired,
    now_iso,
    parse_iso_datetime,
)
from .validation import (
    E164_PATTERN,
    ISO_3166_1_ALPHA2,
    MESSAGE_TYPES,
    NEGOTIATION_MESSAGE_TYPES,
    SUPPORTED_LCP_VERSIONS,
    SubscriptionValidationError,
    ValidationError,
    SchemaValidator,
    email_form_entry_provenance,
    event_matches_filters,
    negotiation_payload_pii_errors,
    validate_subscription_envelope,
)


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


# LEP-0001 (accepted 2026-11-04, W2-01) fixes both batch caps at acceptance:
# 100 items and a 1 MiB body per request. Exceeding either is a whole-request
# refusal, never a per-item rejection — the request is not a batch this
# platform agreed to process. Raising a cap needs the W2-05 benchmark and a
# revised record, so these are protocol constants, not deployment settings.
BATCH_MAX_ITEMS = 100
BATCH_MAX_BODY_BYTES = 1024 * 1024

# LEP-0009 (D8): bounded negotiation. The default and the hard cap are
# protocol constants, not deployment knobs — raising the cap is deferred
# question Q7 and needs measured evidence.
NEGOTIATION_DEFAULT_MAX_ROUNDS = 3
NEGOTIATION_HARD_MAX_ROUNDS = 5
# The LEP's audit-event names, keyed by the state the message left behind.
_NEGOTIATION_AUDIT_ACTIONS = {
    "OPEN": "negotiation.opened",
    "COUNTERED": "negotiation.countered",
    "ACCEPTED": "negotiation.accepted",
    "REFUSED": "negotiation.refused",
    "EXPIRED": "negotiation.expired",
    "REDEEMED": "negotiation.redeemed",
}
_FULL_SEMVER_RE = re.compile(r"\A(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")

_EVENT_STATUS_TARGETS = {
    "ACCEPTED": "ACCEPTED",
    "REJECTED": "REJECTED",
    "DISPUTED": "DISPUTED",
    "REFUNDED": "REFUNDED",
    "CONVERTED": "CONVERTED",
    "ARCHIVED": "ARCHIVED",
    "EXPIRED": "EXPIRED",
}


def _semver_tuple(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _lcp_version_supported(version: str) -> bool:
    # §8 version-string membership applied at the intake boundary: compare
    # PARSED versions against the advertised supported set, never string
    # literals or prefixes (§8). A version is supported when the set carries
    # an element with the same MAJOR and a MINOR >= the envelope's — §6:
    # "Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes)", so
    # PATCH upticks ride the minor they belong to. The envelope schema has
    # already pinned the ^\d+\.\d+\.\d+$ shape when this runs.
    major, minor, _ = _semver_tuple(version)
    return any(
        advertised_major == major and advertised_minor >= minor
        for advertised_major, advertised_minor, _ in map(
            _semver_tuple, SUPPORTED_LCP_VERSIONS
        )
    )


_PUBLISHER_EVENT_TYPES = {
    "REJECTED",
    "DISPUTED",
    "CONSENT_WITHDRAWN",
    "ERASURE_REQUEST",
    # LEP-0006 Track A (F5/T6 pattern): v1.1 price feedback. A publisher
    # reports what a ping actually sold for, including external sales; the
    # report is an attestation keyed to one lead and asserts NO lifecycle
    # transition, so it MUST NOT join _EVENT_STATUS_TARGETS.
    "FINAL_PRICING",
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
    # LEP-0003 §4 (F5/T6): v1.1 reporting direction. A delivered buyer with
    # proper event:submit scope must be able to report first contacts; the
    # wildcard-default path is not a conformance-grade route.
    "CONTACTED",
}
# LEP-0003 §4 ingest MUSTs: keyed-HMAC attestations validated on format and
# count only - the platform cannot verify a reported digest without the
# plaintext (trust model stated in the record).
_CONTACTED_HASH_RE = re.compile(r"\A[a-f0-9]{64}\Z")
_CONTACTED_REPORT_LIMIT = 32
# SPEC §4 class rule: the `event` message class carries no consumer PII
# (`event` row, PII — None). `details` is an open object, so — exactly like
# LEP-0003 §4's report rules — the rule is enforced at ingest, not in schema:
# a plaintext contact value anywhere in `details` is a validation rejection,
# for every event name and both reporting directions (issue #198). The shapes
# themselves live in `pii.py` because LEP-0006 Track B's extension guard
# (B.3.4) enforces the same rule on `lead`/`post` extension values.
# LEP-0006 Track A ingest bounds for the recommended FINAL_PRICING shape
# (SPEC §4). Minor units only; the ceiling is a typo guard (1,000,000 major
# units), and a call's contact duration cannot exceed a day.
_FINAL_PRICING_MAX_PRICE_CENTS = 100_000_000
_FINAL_PRICING_MAX_DURATION_SECONDS = 86_400
_CURRENCY_RE = re.compile(r"\A[A-Z]{3}\Z")

# D3/v1.2: capabilities() states the core schema version it validates against
# by reading the repository's SDK_VERSION (the package version the schemas and
# SDKs are built from). Never a wire field — the advertisement is the signal.
_SDK_VERSION_PATH = Path(__file__).resolve().parents[3] / "SDK_VERSION"

# D7/v1.2: an FX rate is a plain decimal — no sign, exponent, grouping or
# leading-zero integer part — with a mandatory fractional part of 6-12 digits,
# at least six *significant* digits below 1, and a value in [0.000001,
# 1000000] (LEP-0010 §4.1). `schemas/post.json` and `schemas/bid.json` pin the
# same normative pattern. Comparison uses exact decimal arithmetic; only final
# presentation rounds half-up.
_FX_RATE_RE = re.compile(r"\A(?:[1-9][0-9]*\.[0-9]{6,12}|0\.[0-9]*[1-9][0-9]{5,11})\Z")
_FX_RATE_MAX = Decimal(1_000_000)
_FX_METADATA_FIELDS = BID_FX_FIELDS

# LEP-0010 §4.2: ISO 4217 minor-unit exponents are load-bearing —
# settlement_minor = native_minor × 10^(exp_settlement − exp_native) ÷ rate.
# Only the currencies that differ from the ISO 4217 default of two are
# enumerated; every other alpha-3 code resolves to two. Dividing minor units
# without the exponent factor is the 100× USD→JPY overcharge the LEP forbids.
_FX_MINOR_UNIT_EXPONENTS = {
    **dict.fromkeys(
        (
            "BIF",
            "CLP",
            "DJF",
            "GNF",
            "ISK",
            "JPY",
            "KMF",
            "KRW",
            "PYG",
            "RWF",
            "UGX",
            "UYI",
            "VND",
            "VUV",
            "XAF",
            "XOF",
            "XPF",
        ),
        0,
    ),
    **dict.fromkeys(("BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"), 3),
}
_FX_DEFAULT_MINOR_UNIT_EXPONENT = 2


def _minor_unit_exponent(currency: str) -> int:
    """ISO 4217 minor-unit exponent for a validated alpha-3 code."""
    return _FX_MINOR_UNIT_EXPONENTS.get(currency, _FX_DEFAULT_MINOR_UNIT_EXPONENT)


def _read_sdk_version() -> str:
    try:
        return _SDK_VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Platform:
    """Coordinates persistence, validation, matching, and delivery."""

    def __init__(self, config: PlatformConfig, store: Store | None = None):
        self.config = config
        self.store = store or create_store(config)
        self.validator = SchemaValidator(config.schema_root)
        self.auth = Authenticator(self.store, config)
        # W4-03: versioned service-area datasets are loaded lazily and cached
        # until a file changes; an absent directory is an empty catalog.
        self.service_areas = ServiceAreaCatalog(config.service_area_directory)
        # LEP-0008: issuer-JWKS resolver + §5 verifier. The resolver's
        # transport is injectable (tests never touch the network).
        self.attestation = AgentAttestationVerifier(config)
        self.attachments = build_attachment_store(
            config,
            cipher=EnvelopeCipher(config.pii_encryption_key),
        )
        self.attachment_scanner = build_malware_scanner(config)
        self.mapping_registry = MappingRegistry(
            self.store.list_mappings(active_only=False)
        )
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
            metadata={
                "scopes": kwargs.get("scopes")
                if kwargs.get("scopes") is not None
                else ["*"]
            },
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
        # Same receiver policy as wire intake: a normalized envelope carrying
        # extensions must already satisfy B.2/B.3.4 before it can be returned
        # for submission.
        self._validate_extension_policy(payload)
        self.store.insert_mapping_application(
            mapping=mapping,
            source_record_id=str(payload.get("external_id"))
            if payload.get("external_id")
            else None,
            lead_id=str(payload["lead_id"]),
            source_digest=source_digest(source_record),
        )
        self.store.insert_audit(
            tenant_id=self.auth.tenant_for(publisher_id)
            or self.config.routing_tenant_id,
            actor_id=publisher_id,
            action="mapping.applied",
            resource_type="lead",
            resource_id=str(payload["lead_id"]),
            metadata={
                "mapping_id": mapping["mapping_id"],
                "version": mapping["version"],
            },
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
            metadata={
                "buyer_id": offer["buyer_id"],
                "active": offer.get("active", True),
            },
        )

    def delete_offer(self, offer_id: str) -> None:
        if not self.store.delete_offer(offer_id):
            raise RequestError("Offer not found", "LCP-003", 404)
        self.store.insert_audit(
            tenant_id=self.config.routing_tenant_id,
            actor_id="operator",
            action="offer.delete",
            resource_type="offer",
            resource_id=offer_id,
            metadata={},
        )

    # ─── Requirement profiles (W4-01/W4-02) ────────────────────────────────

    def create_requirement_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        """Write one immutable profile version, refusing an invalid profile."""
        profile = dict(profile)
        profile.setdefault("tenant_id", self.config.routing_tenant_id)
        try:
            record = self.store.create_requirement_profile_version(profile)
        except ProfileValidationError as exc:
            raise RequestError(str(exc), "LCP-100", details={"path": exc.path}) from exc
        except ValueError as exc:
            raise RequestError(str(exc), "LCP-100", 409) from exc
        self._audit_profile("requirement_profile.create", record)
        return record

    def activate_requirement_profile(
        self, profile_id: str, version: str, *, effective_from: str | None = None
    ) -> dict[str, Any]:
        record = self._profile_write(
            "requirement_profile.activate",
            profile_id,
            version,
            effective_from=effective_from,
        )
        return record

    def rollback_requirement_profile(
        self, profile_id: str, version: str, *, effective_from: str | None = None
    ) -> dict[str, Any]:
        record = self._profile_write(
            "requirement_profile.rollback",
            profile_id,
            version,
            effective_from=effective_from,
        )
        return record

    def deactivate_requirement_profile(
        self, profile_id: str, *, effective_to: str | None = None
    ) -> list[str]:
        try:
            versions = self.store.deactivate_requirement_profile(
                profile_id,
                tenant_id=self.config.routing_tenant_id,
                effective_to=effective_to,
            )
        except ValueError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        if not versions:
            raise RequestError(
                f"no active requirement profile: {profile_id}", "LCP-003", 404
            )
        self.store.insert_audit(
            tenant_id=self.config.routing_tenant_id,
            actor_id="operator",
            action="requirement_profile.deactivate",
            resource_type="requirement_profile",
            resource_id=profile_id,
            metadata={"versions": versions},
        )
        return versions

    def list_requirement_profiles(
        self, profile_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.store.list_requirement_profiles(
            profile_id, tenant_id=self.config.routing_tenant_id
        )

    def _profile_write(
        self,
        action: str,
        profile_id: str,
        version: str,
        *,
        effective_from: str | None,
    ) -> dict[str, Any]:
        try:
            record = self.store.activate_requirement_profile(
                profile_id,
                version,
                tenant_id=self.config.routing_tenant_id,
                effective_from=effective_from,
            )
        except ValueError as exc:
            raise RequestError(str(exc), "LCP-003", 404) from exc
        self._audit_profile(action, record)
        return record

    def _audit_profile(self, action: str, record: dict[str, Any]) -> None:
        self.store.insert_audit(
            tenant_id=str(record["tenant_id"]),
            actor_id="operator",
            action=action,
            resource_type="requirement_profile",
            resource_id=str(record["profile_id"]),
            metadata={
                "version": record["version"],
                "status": record["status"],
                "effective_from": record["effective_from"],
                "effective_to": record["effective_to"],
                "service_area_ref": record.get("service_area_ref"),
            },
        )

    # ─── Inbound messages ──────────────────────────────────────────────────

    def ingest(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        """Single-item intake front door: envelope, transport, auth, throttle.

        Everything after authentication is ``_ingest_lead``, which batch
        submission loops over — one intake path, not a second ingestion engine
        (LEP-0001 W2-02).
        """
        headers = headers or {}
        self._validate_envelope(envelope)
        message = envelope["lcp"]["message"]
        if message["type"] not in {"lead", "call"}:
            raise RequestError(
                "Only lead and call messages can enter intake", "LCP-006"
            )
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
            raise RequestError(
                "Authenticated sender does not match message sender", "LCP-002", 401
            )
        if not self.store.consume_rate_limit(
            sender_id,
            limit=self.config.rate_limit_per_minute,
        ):
            raise RequestError("Sender rate limit exceeded", "LCP-011", 429)
        return self._ingest_lead(envelope, message=message, sender_id=sender_id)

    def _ingest_lead(
        self,
        envelope: dict[str, Any],
        *,
        message: dict[str, Any],
        sender_id: str,
        compare_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The authenticated single-lead path: dedupe → routing → ack.

        Callers have already validated the envelope, the transport flags, the
        receiver, the sender identity, and the rate limit. Batch submission
        reaches exactly this code with its one authenticated sender, so
        per-item parity of validation, idempotency, and audit is structural
        rather than re-stated (LEP-0001 W2-02).

        ``compare_payload`` scopes the LCP-005 reuse guard to the item's
        content. A batch item's envelope is synthesized per request (its
        message id is derived from the batch id and its timestamp is the
        batch's), so two requests replaying the same item deliberately carry
        different envelopes; the item's identity is its own idempotency key
        plus its lead payload (LEP-0001: "replays return the original result").
        """
        # LEP-0003 W6-07: the entry provenance is recorded first — before the
        # idempotency comparison (a replay must compare the same derived bytes)
        # and before matching (an exclusion predicate must see the declared
        # source_type) — and it refuses, never mislabels, when the gate is off.
        self._record_email_form_entry(envelope, sender_id=sender_id)
        existing = self.store.get_lead_by_idempotency(
            sender_id, message["idempotency_key"]
        )
        if existing:
            existing_envelope = self.store.decode_envelope(existing["envelope_json"])
            same_message = (
                existing_envelope["lcp"]["payload"] == compare_payload
                if compare_payload is not None
                else self._body(existing_envelope) == self._body(envelope)
            )
            if not same_message:
                raise RequestError(
                    "Idempotency key was reused with a different message",
                    "LCP-005",
                    409,
                )
            self._enqueue_audit(
                tenant_id=self.auth.tenant_for(sender_id)
                or self.config.routing_tenant_id,
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

        # LEP-0008 §5 pinned intake slot: after auth/rate-limit and BOTH
        # dedup early-return paths (the LCP-005 409 and the DUPLICATE ack
        # never re-verify and never fetch keys — §7 "one verification"),
        # before insert_lead: a failed verification MUST NOT persist PII.
        self._verify_agent_attestation(envelope, message, sender_id)

        payload = envelope["lcp"]["payload"]
        # LEP-0006 Track B §B.2/§B.3.4: registered-shape and PII receiver
        # rules, before matching or persistence — a rejected lead stores
        # nothing and creates no pings/posts.
        self._validate_extension_policy(payload)
        lead_expired = is_expired(envelope_expiry(envelope))
        offers = (
            []
            if lead_expired
            else self.store.list_offer_candidates(
                payload,
                tenant_id=self.config.routing_tenant_id,
            )
        )
        matches: list[tuple[dict[str, Any], MatchResult]] = []
        self._validate_attachment_references(payload, sender_id)
        for offer in offers:
            result = match_offer(
                offer,
                payload,
                sender_id=message["sender_id"],
                profiles=self.store,
                service_areas=self.service_areas,
            )
            if result.matched and not self._capacity_available(offer):
                result = MatchResult(
                    False,
                    result.reasons + ("capacity_exceeded",),
                    profile=result.profile,
                    predicates=result.predicates,
                    service_area=result.service_area,
                )
            matches.append((offer, result))
        matching_offers = [offer for offer, result in matches if result.matched]
        direct_offers = [
            offer
            for offer in matching_offers
            if offer.get("routing_mode", "auction") == "direct"
        ]
        if direct_offers:
            direct_offers.sort(key=self._pacing_priority)
            # No per-pass budget slice (LEP-0003 §5 item 1): direct awards are
            # decided atomically at the single write-time point inside
            # _create_direct_post, counting distinct awarded buyers.
            matching_offers = direct_offers
        lead_exclusive = (payload.get("exclusivity") or {}).get(
            "exclusivity", "shared"
        ) == "exclusive"
        sender_tenant = self.auth.tenant_for(sender_id) or self.config.routing_tenant_id
        initial_status = "EXPIRED" if lead_expired else "NEW"
        if not self.store.insert_lead(
            envelope, status=initial_status, tenant_id=sender_tenant
        ):
            existing = self.store.get_lead_by_idempotency(
                sender_id, message["idempotency_key"]
            )
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="DUPLICATE",
                lead_id=existing["lead_id"] if existing else payload["lead_id"],
            )

        for offer, result in matches:
            self._record_match_evaluation(
                lead_id=payload["lead_id"], offer=offer, result=result
            )

        created_pings = 0
        created_direct_posts = 0
        if not lead_expired:
            for offer in matching_offers:
                if offer.get("routing_mode", "auction") == "direct":
                    if self._create_direct_post(envelope, offer):
                        created_direct_posts += 1
                        if lead_exclusive:
                            break
                elif self._create_ping(envelope, offer):
                    created_pings += 1
        if created_direct_posts:
            self.store.update_lead_status(
                payload["lead_id"], "POSTED", reason="direct_delivery"
            )
        elif created_pings:
            self.store.update_lead_status(payload["lead_id"], "PINGED")
        self.store.complete_routing_job(payload["lead_id"])
        self._enqueue_audit(
            tenant_id=sender_tenant,
            actor_id=sender_id,
            action="lead.routed",
            resource_type="lead",
            resource_id=payload["lead_id"],
            metadata={
                "message_type": message["type"],
                "ping_count": created_pings,
                "post_count": created_direct_posts,
            },
        )

        return build_ack(
            envelope,
            sender_id=self.config.platform_id,
            status="RECEIVED",
            lead_id=payload["lead_id"],
        )

    # ─── Batch intake (LEP-0001, execution plan W2) ────────────────────────

    def submit_batch(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        """Bounded batch intake — a loop over the single-item path.

        Wire shape (LEP-0001 ``### Batch submission``)::

            {"lcp": {"version": "1.2.0",
                     "message": {"type": "batch", ...},
                     "payload": {"request_id": "...",
                                 "items": [{"item_id": "...",
                                            "idempotency_key": "...",
                                            "payload": {<lead payload>}}]}}}

        Each item's ``payload`` is a **lead payload** — the ``lead.json`` root
        object, never an envelope. The per-item envelope is synthesized from
        the batch envelope (same sender/receiver/timestamp/test, a
        deterministic ``message.id`` derived from the batch id and the item id,
        and the item's own ``idempotency_key``) and then runs ``_ingest_lead``
        unchanged, so the wire type adds dispatch and per-item metadata, not a
        second ingestion engine (W2-02). A replay of the same item collides
        with the same persisted dedupe row ``POST /v1/lcp/leads`` would.

        Request-level failures reject the whole request before any item runs:
        the body above ``BATCH_MAX_BODY_BYTES`` (``413``), more than
        ``BATCH_MAX_ITEMS`` items (``413``), a malformed wrapper, a
        non-``batch`` message, or an unsupported protocol version
        (``LCP-100``/``LCP-006``/``LCP-007``), and the usual authentication,
        receiver, transport, and scope refusals. An item whose *lead payload*
        is invalid is not a request-level failure — it is that item's receipt.
        Everything else is per item: each item is validated, throttled,
        deduped, and persisted by ``_ingest_lead``, so one item's failure never
        rolls back or blocks its siblings — there is no cross-item transaction
        (W2-02).

        The response is ``207`` with the LEP's per-item receipt, one per input
        item, **in input order**::

            {"request_id": "...",
             "summary": {"total": n, "accepted": a, "duplicate": d,
                         "rejected": r, "error": e},
             "results": [{"item_id": ..., "status": "accepted"
                          | "duplicate" | "rejected" | "error",
                          "lead_id": ..., "error"?: {...}},
                         ...]}

        ``accepted`` is the single-item ``RECEIVED`` ack, ``duplicate`` the
        single-item ``DUPLICATE`` ack replaying the item's original ``lead_id``
        (existing ``LCP-005`` semantics at item granularity), ``rejected`` a
        per-item validation/throttle/key-conflict refusal (``error`` present),
        and ``error`` an unexpected per-item fault that the sender may retry on
        its own. This receipt is *not* ``ack.status``: it does not extend,
        reuse, or emit that enum, and it never acknowledges a ``post``
        delivery (LEP-0001 F5, D2).

        Throttle accounting matches single-item intake: every item consumes one
        sender token, so a batch cannot outrun ``rate_limit_per_minute``.
        """
        headers = headers or {}
        body = raw_body if raw_body is not None else self._body(envelope)
        if len(body) > BATCH_MAX_BODY_BYTES:
            raise RequestError(
                f"Batch request body exceeds {BATCH_MAX_BODY_BYTES} bytes",
                "LCP-001",
                413,
                details=[
                    f"a batch request body is capped at {BATCH_MAX_BODY_BYTES} "
                    "bytes (1 MiB, LEP-0001 W2-01); split the batch and resubmit"
                ],
            )
        items, request_id, message = self._validate_batch_envelope(envelope)
        self._validate_transport(message, headers)
        if message["receiver_id"] != self.config.platform_id:
            raise RequestError("Message receiver is not this platform", "LCP-002", 401)
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=body,
            idempotency_key=message["idempotency_key"],
            mutating=True,
            required_scope="lead:submit",
        )
        if sender_id != message["sender_id"]:
            raise RequestError(
                "Authenticated sender does not match message sender", "LCP-002", 401
            )

        results = [
            self._batch_item_receipt(
                item,
                index=index,
                sender_id=sender_id,
                version=envelope["lcp"]["version"],
                batch_message=message,
            )
            for index, item in enumerate(items)
        ]
        summary: dict[str, int] = {
            "total": len(results),
            "accepted": 0,
            "duplicate": 0,
            "rejected": 0,
            "error": 0,
        }
        for receipt in results:
            summary[receipt["status"]] += 1
        return {"request_id": request_id, "summary": summary, "results": results}

    def _validate_batch_envelope(
        self, envelope: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        """Validate the batch wrapper and return ``(items, request_id, message)``.

        Only the wrapper is checked here: the envelope schema, ``schemas/batch.json``
        (per-item metadata and the 100-item cap), the closed ``message.type``,
        and version negotiation. Each item's lead payload gets the full
        single-lead treatment inside ``_batch_item_receipt``, so an invalid
        lead payload is that item's receipt — never a whole-request schema
        failure (LEP-0001: "one bad item must not fail the batch").
        """
        lcp: Any = envelope.get("lcp") if isinstance(envelope, dict) else None
        message: Any = lcp.get("message") if isinstance(lcp, dict) else None
        if isinstance(message, dict) and message.get("type") != "batch":
            raise RequestError("Only batch messages can enter batch intake", "LCP-006")
        payload: Any = lcp.get("payload") if isinstance(lcp, dict) else None
        items: Any = payload.get("items") if isinstance(payload, dict) else None
        # W2-01 caps are fixed at acceptance: exceeding either is a
        # whole-request refusal, checked ahead of schema validation so the cap
        # answers 413 rather than the schema's 400.
        if isinstance(items, list) and len(items) > BATCH_MAX_ITEMS:
            raise RequestError(
                f"Batch exceeds {BATCH_MAX_ITEMS} items",
                "LCP-001",
                413,
                details=[
                    f"a batch request carries at most {BATCH_MAX_ITEMS} items "
                    f"(got {len(items)}); split the batch and resubmit"
                ],
            )
        self._validate_envelope(envelope)
        return payload["items"], payload["request_id"], message

    def _batch_item_receipt(
        self,
        item: dict[str, Any],
        *,
        index: int,
        sender_id: str,
        version: str,
        batch_message: dict[str, Any],
    ) -> dict[str, Any]:
        """Process one batch item and return its receipt; never raises.

        The item is a lead payload plus its own ``idempotency_key``. The lead
        envelope it would have arrived in is synthesized from the batch
        envelope — same sender, receiver, timestamp and test flag; a
        deterministic message id from the batch id and ``item_id``; the item's
        key — and then handed to the single-lead path. Identity, transport,
        receiver and authentication are the batch's (already established);
        schema, lifecycle intake rules, idempotency, throttling and audit stay
        per item.
        """
        item_id = item["item_id"]
        item_payload = item["payload"]
        claimed_lead_id = item_payload.get("lead_id")
        lead_id = claimed_lead_id if isinstance(claimed_lead_id, str) else None
        idempotency_key = item["idempotency_key"]
        message: dict[str, Any] = {
            "id": str(uuid5(NAMESPACE_URL, f"{batch_message['id']}/{item_id}")),
            "type": "lead",
            "timestamp": batch_message["timestamp"],
            "sender_id": batch_message["sender_id"],
            "receiver_id": batch_message["receiver_id"],
            "correlation_id": None,
            "idempotency_key": idempotency_key,
            "test": bool(batch_message.get("test", False)),
        }
        envelope: dict[str, Any] = {
            "lcp": {"version": version, "message": message, "payload": item_payload}
        }
        receipt: dict[str, Any] = {
            "item_id": item_id,
            "status": "rejected",
            "lead_id": lead_id,
        }
        try:
            self._validate_envelope(envelope)
            if not self.store.consume_rate_limit(
                sender_id, limit=self.config.rate_limit_per_minute
            ):
                raise RequestError("Sender rate limit exceeded", "LCP-011", 429)
            ack = self._ingest_lead(
                envelope=envelope,
                message=message,
                sender_id=sender_id,
                compare_payload=item_payload,
            )
            ack_payload = ack["lcp"]["payload"]
            receipt["lead_id"] = ack_payload.get("lead_id", lead_id)
            receipt["status"] = (
                "duplicate" if ack_payload.get("status") == "DUPLICATE" else "accepted"
            )
            return receipt
        except RequestError as exc:
            error: dict[str, Any] = {"code": exc.code, "message": str(exc)}
            if exc.details is not None:
                error["details"] = exc.details
            receipt["error"] = error
            self._audit_batch_item(
                sender_id=sender_id,
                lead_id=lead_id,
                idempotency_key=idempotency_key,
                index=index,
                batch_message_id=batch_message["id"],
                status="rejected",
                error_code=exc.code,
            )
        except Exception:
            # Per-item isolation: an unexpected fault is this item's receipt,
            # never a 500 for the request and never a rollback of siblings.
            receipt["status"] = "error"
            receipt["error"] = {
                "code": "LCP-500",
                "message": "Internal error while processing this item",
            }
            self._audit_batch_item(
                sender_id=sender_id,
                lead_id=lead_id,
                idempotency_key=idempotency_key,
                index=index,
                batch_message_id=batch_message["id"],
                status="error",
                error_code="LCP-500",
            )
        return receipt

    def _audit_batch_item(
        self,
        *,
        sender_id: str,
        lead_id: str | None,
        idempotency_key: str,
        index: int,
        batch_message_id: str,
        status: str,
        error_code: str,
    ) -> None:
        """One audit row for an item that produced no lead-side row.

        Accepted and duplicate items are audited by ``_ingest_lead`` itself
        (``lead.routed``/``lead.duplicate``); this covers the two receipts that
        never reach it, so every processed item has exactly one ``lead.*`` row.
        An audit failure must not escalate a per-item refusal into a request
        fault, so it is contained here.
        """
        try:
            self._enqueue_audit(
                tenant_id=self.auth.tenant_for(sender_id)
                or self.config.routing_tenant_id,
                actor_id=sender_id,
                action=f"lead.{status}",
                resource_type="lead",
                resource_id=lead_id or idempotency_key or f"batch-item-{index}",
                metadata={
                    "source": "batch",
                    "batch_message_id": batch_message_id,
                    "error_code": error_code,
                },
            )
        except Exception:  # noqa: BLE001 - isolated per item, see docstring
            return

    def _validate_extension_policy(self, payload: dict[str, Any]) -> None:
        """LEP-0006 Track B receiver rules for ``extensions`` (§B.2/§B.3.4).

        The schema already enforces the namespace format and the ``lcp.core.*``
        reservation. What the core cannot know — the registered
        ``{org}.form.questions`` shape, the container-size policy bound, and
        the PII class rule — is enforced here. Reject (``LCP-100``) rather
        than scrub: the lead record must not persist free-text contacts that
        hash-keyed suppression and erasure cannot reach. Absent or empty =
        bit-for-bit v1.0 behavior.
        """
        extensions = payload.get("extensions")
        if not isinstance(extensions, dict) or not extensions:
            return
        problems = extension_problems(
            extensions, max_total_bytes=self.config.max_extension_bytes
        )
        if not problems:
            return
        raise RequestError(
            "Lead extensions failed the receiver policy: " + "; ".join(problems),
            "LCP-100",
            details={"field": problems[0].split(" ", 1)[0]},
        )

    def _record_email_form_entry(
        self, envelope: dict[str, Any], *, sender_id: str
    ) -> None:
        """LEP-0003 W6-07 (SPEC §9-gated): record a captureless email/form entry.

        The gate has two halves, both deployment-side: the sender must be a
        declared email/form relay (``LCP_EMAIL_FORM_PUBLISHER_IDS``) — the
        entry surface the platform was told about — *and* the flag
        (``LCP_FEATURE_EMAIL_FORM_ENTRY``) must be on. A lead that satisfies
        the surface but arrives while the flag is off is REFUSED: recording it
        under the normalizer's ``publisher`` default would declare a surface
        nobody observed, and this platform fails closed on provenance (§9).
        The derivation itself (declared value, consent-consistent acquisition
        method, never a client-observed field) is
        ``validation.email_form_entry_provenance``.
        """
        if sender_id not in self.config.email_form_publisher_ids:
            return
        payload = envelope["lcp"]["payload"]
        recorded = email_form_entry_provenance(payload)
        if recorded is None:
            return
        if not self.config.feature_email_form_entry:
            raise RequestError(
                "Email-form entry emission is not enabled on this receiver",
                "LCP-100",
                400,
                details=[
                    "provenance.source_type",
                    "this deployment does not advertise features.email_form_entry, "
                    "so a captureless email/form relay entry is refused rather "
                    "than recorded under a source_type it did not observe",
                ],
            )
        provenance = payload["provenance"]
        provenance["source_type"] = recorded["source_type"]
        # A declared acquisition method is the publisher's statement; only an
        # absent one is filled from the consent recorded on the lead.
        provenance.setdefault("acquisition_method", recorded["acquisition_method"])

    def _verify_agent_attestation(
        self,
        envelope: dict[str, Any],
        message: dict[str, Any],
        sender_id: str,
    ) -> None:
        """LEP-0008 §5/§8: run the 13-step procedure for agent-origin leads,
        write the frozen §Privacy audit row (``{outcome, reason, iss_host,
        kid, tok_sha256_16}`` — never token bytes), and apply the deployment
        mode. Enforce rejects per the §8 mapping (shape → 400 LCP-100 schema
        class; verification failure → 401 LCP-002 with ``details.reason``;
        availability-only failure → 500 LCP-500). Observe verifies and records
        every outcome but still accepts — the Rollout Phase 2 soft path, and
        not an L3-honest mode (§9)."""
        payload = envelope["lcp"]["payload"]
        provenance = payload.get("provenance") or {}
        agent = (
            provenance.get("agent")
            if provenance.get("source_type") == "agent"
            else None
        )
        if not isinstance(agent, dict):
            # §5: the procedure does not run for non-agent leads or an
            # absent provenance.agent block (presence semantics unchanged).
            return
        observe = self.config.attestation_mode == "observe"
        token = str(agent.get("attestation", ""))

        def audit(outcome: str, reason: str | None, metadata: dict[str, Any]) -> None:
            self._enqueue_audit(
                tenant_id=self.auth.tenant_for(sender_id)
                or self.config.routing_tenant_id,
                actor_id=sender_id,
                action=f"attestation.{outcome}",
                resource_type="lead",
                resource_id=str(payload.get("lead_id", "")),
                metadata={"outcome": outcome, "reason": reason, **metadata},
            )

        try:
            check_attestation_shape(token)
        except AttestationShapeError as exc:
            if not observe:
                # §8 row 2: shape is the schema layer's class — LCP-100/400
                # with the attestation pointer (§2 pattern + bundle regen
                # lands in the wire-lane PR; this gate reproduces the wire
                # outcome at the pinned intake slot until it does).
                raise RequestError(
                    "LCP validation failed", "LCP-100", 400, details=[str(exc)]
                ) from exc
            # Observe: verify + audit + accept — zero new rejections on the
            # wire (Rollout Phase 2).
            audit(
                "observe",
                "format",
                {"iss_host": None, "kid": None, "tok_sha256_16": token_digest(token)},
            )
            return
        try:
            binding = self.attestation.verify(
                token,
                receiver_id=str(message["receiver_id"]),
                envelope_agent_id=str(agent.get("agent_id", "")),
                sender_id=sender_id,
                scopes=lambda scope: self.auth.has_scope(sender_id, scope),
            )
        except AttestationFailure as failure:
            audit("observe" if observe else "reject", failure.reason, failure.metadata)
            if observe:
                return
            if failure.reason == "key_unavailable":
                # §8: the receiver's own infrastructure answer — retryable,
                # fail-closed; enforce mode MUST NOT silently substitute an
                # accept when key resolution fails.
                raise RequestError(
                    "Agent attestation verification is unavailable",
                    "LCP-500",
                    500,
                    details={"reason": failure.reason},
                ) from failure
            # Identity axis (docs/err-auth-triage.md §3 precedent): an
            # unverifiable attestation is a failing assertion of WHO is
            # acting; LCP-002 with details.reason disambiguates (§8).
            raise RequestError(
                "Agent attestation failed verification",
                "LCP-002",
                401,
                details={"reason": failure.reason},
            ) from failure
        audit("accept", None, binding.metadata)

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
            raise RequestError(
                "Bid correlation does not match the ping", "LCP-003", 400
            )
        if not self.store.consume_rate_limit(
            sender_id,
            limit=self.config.rate_limit_per_minute,
        ):
            raise RequestError("Sender rate limit exceeded", "LCP-011", 429)
        # LEP-0009 C7: a token replaces bid_price_cents. Redemption is gated by
        # the feature switch exactly like minting, and runs before the ordinary
        # ping-status check: a token after its negotiation expiry answers
        # `negotiation_closed`, never the generic auction `expired`.
        if "negotiation_binding_token" in payload:
            return self._redeem_negotiation_token(
                envelope,
                message=message,
                payload=payload,
                sender_id=sender_id,
                ping=ping,
                ping_envelope=ping_envelope,
                raw_body=raw_body,
            )
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

    # ─── LEP-0009: negotiation (W4-08 / W5-01…W5-03) ───────────────────────
    #
    # One table per negotiation, one audit row per message, one hashed-token
    # table. The state machine is OPEN -> COUNTERED -> ACCEPTED -> REDEEMED,
    # with REFUSED / EXPIRED terminal. Every rule below fails closed: a
    # refusal never guesses a price, never burns a round, and never reopens.

    def _require_negotiation(self) -> None:
        """LEP-0009 C3: with the switch off the surface does not exist."""
        if not self.config.feature_negotiation:
            raise RequestError(
                "Unknown message type for this deployment",
                "LCP-006",
                400,
                details=[
                    "quote/counter require LCP_FEATURE_NEGOTIATION (LEP-0009 C3); "
                    "this deployment does not advertise or accept them"
                ],
            )

    def _advertises_negotiation(self, peer_id: str) -> bool:
        """LEP-0009 C2: parsed semver major == 1, minor >= 2 (never a prefix)."""
        for element in self.auth.lcp_versions_for(peer_id):
            match = _FULL_SEMVER_RE.match(str(element).strip())
            if match and int(match.group(1)) == 1 and int(match.group(2)) >= 2:
                return True
        return False

    @staticmethod
    def _message_digest(raw_body: bytes | None, envelope: dict[str, Any]) -> str:
        body = (
            raw_body
            if raw_body is not None
            else json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        )
        return hashlib.sha256(body).hexdigest()

    def _negotiation_payload_guard(
        self, message_type: str, payload: dict[str, Any]
    ) -> None:
        problems = negotiation_payload_pii_errors(
            payload, self.validator.payload_fields(message_type)
        )
        if problems:
            raise RequestError(
                "Negotiation payload is not ping-safe",
                "LCP-100",
                400,
                details=problems,
            )

    def _negotiation_preflight(
        self, envelope: dict[str, Any], expected_type: str
    ) -> None:
        """The C4 probe, ahead of schema validation.

        The schema's allowlist already refuses the field, but the record
        requires the refusal to name the PII axis too; running the probe
        first keeps both codes in one response instead of collapsing the
        consumer-shaped field into the generic validation message
        (LEP-0009 invalid example E).
        """
        lcp = envelope.get("lcp") if isinstance(envelope, dict) else None
        message = lcp.get("message") if isinstance(lcp, dict) else None
        payload = lcp.get("payload") if isinstance(lcp, dict) else None
        if isinstance(message, dict) and message.get("type") == expected_type:
            # C3 first: with the switch off the type does not exist here, so
            # even a malformed/PII-bearing payload answers LCP-006, not a
            # validation detail that would confirm the surface.
            self._require_negotiation()
            if isinstance(payload, dict):
                self._negotiation_payload_guard(expected_type, payload)

    def _negotiation_field_error(
        self, field: str, expected: Any, actual: Any
    ) -> NoReturn:
        raise RequestError(
            f"payload.{field} does not match the negotiation",
            "LCP-100",
            400,
            details=[
                f"payload.{field}: expected {expected!r}, got {actual!r} "
                "(LEP-0009; no silent correction)"
            ],
        )

    def _negotiation_object(
        self,
        row: Any,
        *,
        outcome: str,
        price_cents: int | None = None,
        rounds_remaining: int | None = None,
        binding_token: str | None = None,
        binding_expires_at: str | None = None,
    ) -> dict[str, Any]:
        obj: dict[str, Any] = {
            "negotiation_id": str(row["negotiation_id"]),
            "outcome": outcome,
            "currency": str(row["currency"]),
            "rounds_remaining": int(
                row["rounds_remaining"]
                if rounds_remaining is None
                else rounds_remaining
            ),
        }
        if price_cents is not None:
            obj["price_cents"] = int(price_cents)
        if binding_token is not None:
            obj["binding_token"] = binding_token
            obj["binding_expires_at"] = binding_expires_at
        return obj

    def _negotiation_ack(
        self,
        envelope: dict[str, Any],
        *,
        status: str,
        negotiation: dict[str, Any],
        rejection_reason: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return build_ack(
            envelope,
            sender_id=self.config.platform_id,
            status=status,
            rejection_reason=rejection_reason,
            errors=errors,
            negotiation=negotiation,
        )

    def _record_negotiation_message(
        self,
        row: Any,
        envelope: dict[str, Any],
        *,
        actor_role: str,
        decision: str,
        price_cents: int | None,
        state_before: str,
        state_after: str,
        rejection_reason: str | None,
        ack: dict[str, Any],
        audit_action: str,
        body_hash: str,
        updates: dict[str, Any] | None = None,
    ) -> None:
        """Persist one message: state update, one round row, one audit row."""
        message = envelope["lcp"]["message"]
        negotiation_id = str(row["negotiation_id"])
        if updates:
            self.store.update_negotiation(negotiation_id, **updates)
        # C7: the binding token is returned exactly once, in the live ack. The
        # persisted ack keeps the outcome/price for replay but never the token
        # bytes — storage holds a hash only ("never logs or echoes again").
        persisted_ack = json.loads(json.dumps(ack))
        persisted_negotiation = (
            persisted_ack.get("lcp", {}).get("payload", {}).get("negotiation")
        )
        if isinstance(persisted_negotiation, dict):
            persisted_negotiation.pop("binding_token", None)
        self.store.insert_negotiation_round(
            negotiation_id=negotiation_id,
            message_id=str(message["id"]),
            message_type=str(message["type"]),
            actor_id=str(message["sender_id"]),
            actor_role=actor_role,
            decision=decision,
            price_cents=price_cents,
            rounds_remaining_after=int(
                (updates or {}).get("rounds_remaining", row["rounds_remaining"])
            ),
            state_after=state_after,
            rejection_reason=rejection_reason,
            idempotency_key=str(message["idempotency_key"]),
            body_hash=body_hash,
            ack=persisted_ack,
        )
        # Audit metadata carries ids/prices/rounds only — never token bytes.
        self.store.insert_audit(
            tenant_id=str(row["tenant_id"]),
            actor_id=str(message["sender_id"]),
            action=audit_action,
            resource_type="negotiation",
            resource_id=negotiation_id,
            metadata={
                "ping_id": str(row["ping_id"]),
                "offer_id": str(row["offer_id"]),
                "buyer_id": str(row["buyer_id"]),
                "actor_role": actor_role,
                "before_state": state_before,
                "after_state": state_after,
                "decision": decision,
                "price_cents": price_cents,
                "rounds_remaining": int(
                    (updates or {}).get("rounds_remaining", row["rounds_remaining"])
                ),
                "message_id": str(message["id"]),
                **({"rejection_reason": rejection_reason} if rejection_reason else {}),
            },
        )

    def _negotiation_replay(
        self, negotiation_id: str, envelope: dict[str, Any], *, body_hash: str
    ) -> dict[str, Any] | None:
        """Same key + same bytes -> the original ack; different bytes -> LCP-005."""
        key = str(envelope["lcp"]["message"]["idempotency_key"])
        row = self.store.negotiation_round_for_key(negotiation_id, key)
        if row is None:
            return None
        if str(row["body_hash"]) != body_hash:
            raise RequestError(
                "Idempotency key reused with different content", "LCP-005", 409
            )
        stored = json.loads(str(row["ack_json"]))
        if not isinstance(stored, dict):
            raise RequestError("Stored acknowledgement is unreadable", "LCP-500", 500)
        return stored

    def submit_quote(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        """Open a negotiation: the platform states its ask for a pinged buyer."""
        headers = headers or {}
        self._negotiation_preflight(envelope, "quote")
        self._validate_envelope(envelope)
        message = envelope["lcp"]["message"]
        if message["type"] != "quote":
            raise RequestError("Quote endpoint requires a quote message", "LCP-006")
        self._require_negotiation()
        self._validate_transport(message, headers)
        if message["sender_id"] != self.config.platform_id:
            raise RequestError("A quote is a platform-role message", "LCP-002", 401)
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body if raw_body is not None else self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
            required_scope="negotiation:send",
        )
        if sender_id != self.config.platform_id:
            raise RequestError(
                "Authenticated sender does not match the platform", "LCP-002", 401
            )
        if not self.store.consume_rate_limit(
            sender_id, limit=self.config.rate_limit_per_minute
        ):
            raise RequestError("Sender rate limit exceeded", "LCP-011", 429)
        if not self._advertises_negotiation(message["receiver_id"]):
            # C2: emitting toward a peer whose parsed lcp_versions does not
            # open the v1.2 gate is a capability violation, not a best effort.
            raise RequestError(
                "Receiver does not advertise the negotiation capability",
                "LCP-006",
                400,
                details=[
                    f"{message['receiver_id']!r} advertises no parsed 1.x "
                    "element with minor >= 2; quote/counter MUST NOT be emitted "
                    "(LEP-0009 C2)"
                ],
            )
        payload = envelope["lcp"]["payload"]
        self._negotiation_payload_guard("quote", payload)
        ping = self.store.get_ping(str(payload["ping_id"]))
        if not ping:
            raise RequestError("Unknown ping_id", "LCP-002", 401)
        if str(ping["buyer_id"]) != str(message["receiver_id"]):
            raise RequestError("Buyer is not authorized for this ping", "LCP-002", 401)
        ping_envelope = self.store.decode_envelope(ping["envelope_json"])
        ping_message = ping_envelope["lcp"]["message"]
        ping_payload = ping_envelope["lcp"]["payload"]
        if message.get("correlation_id") != ping_message["id"]:
            raise RequestError(
                "Quote correlation does not name the ping being priced",
                "LCP-003",
                400,
            )
        if str(payload["offer_id"]) != str(ping["offer_id"]):
            self._negotiation_field_error(
                "offer_id", ping["offer_id"], payload["offer_id"]
            )
        ping_currency = str(ping_payload.get("currency", ""))
        if str(payload["currency"]) != ping_currency:
            self._negotiation_field_error(
                "currency", ping_currency, payload["currency"]
            )
        ping_floor = int(ping_payload.get("floor_price_cents", 0))
        if int(payload["floor_price_cents"]) != ping_floor:
            self._negotiation_field_error(
                "floor_price_cents", ping_floor, payload["floor_price_cents"]
            )
        max_rounds = payload.get("max_rounds", NEGOTIATION_DEFAULT_MAX_ROUNDS)
        if (
            not _is_int(max_rounds)
            or not 1 <= max_rounds <= NEGOTIATION_HARD_MAX_ROUNDS
        ):
            raise RequestError(
                "max_rounds is outside the bounded round range",
                "LCP-100",
                400,
                details=[
                    f"payload.max_rounds: {max_rounds!r} outside 1.."
                    f"{NEGOTIATION_HARD_MAX_ROUNDS} (LEP-0009 C5; hard cap)"
                ],
            )
        if int(payload["rounds_remaining"]) != max_rounds:
            self._negotiation_field_error(
                "rounds_remaining", max_rounds, payload["rounds_remaining"]
            )
        expires_at = parse_iso_datetime(payload["expires_at"])
        timestamp = parse_iso_datetime(message["timestamp"])
        ping_expiry = parse_iso_datetime(ping_payload.get("expires_at")) or (
            parse_iso_datetime(ping["expires_at"])
        )
        if expires_at is None:
            raise RequestError("payload.expires_at is not a timestamp", "LCP-100", 400)
        if ping_expiry is not None and expires_at > ping_expiry:
            self._negotiation_field_error(
                "expires_at",
                format_iso_datetime(ping_expiry),
                payload["expires_at"],
            )
        if (
            timestamp is None
            or expires_at <= timestamp
            or expires_at <= datetime.now(timezone.utc)
        ):
            raise RequestError(
                "payload.expires_at must be in the future of message.timestamp",
                "LCP-100",
                400,
                details=["payload.expires_at: an absolute expiry needs a window"],
            )
        negotiation_id = str(payload["negotiation_id"])
        body_hash = self._message_digest(raw_body, envelope)
        existing = self.store.get_negotiation(negotiation_id)
        if existing is not None:
            replay = self._negotiation_replay(
                negotiation_id, envelope, body_hash=body_hash
            )
            if replay is not None:
                return replay
            raise RequestError(
                "A negotiation with this id already exists", "LCP-004", 422
            )
        price_cents = int(payload["price_cents"])
        if price_cents < ping_floor:
            # Refused, never clamped: the platform cannot claim a price the
            # buyer did not state, and no row or token is written.
            return self._negotiation_ack(
                envelope,
                status="REJECTED",
                rejection_reason="below_floor_price",
                errors=[
                    {
                        "code": "LCP-100",
                        "message": "price_cents is below the restated floor",
                        "field": "payload.price_cents",
                    }
                ],
                negotiation={
                    "negotiation_id": negotiation_id,
                    "outcome": "refused",
                    "price_cents": price_cents,
                    "currency": ping_currency,
                    "rounds_remaining": int(payload["rounds_remaining"]),
                },
            )
        if str(ping["status"]) != "OPEN":
            return self._negotiation_ack(
                envelope,
                status="REJECTED",
                rejection_reason="negotiation_expired",
                negotiation={
                    "negotiation_id": negotiation_id,
                    "outcome": "expired",
                    "price_cents": price_cents,
                    "currency": ping_currency,
                    "rounds_remaining": int(payload["rounds_remaining"]),
                },
            )
        tenant_id = (
            self.auth.tenant_for(str(message["receiver_id"]))
            or self.config.routing_tenant_id
        )
        inserted = self.store.insert_negotiation(
            negotiation_id=negotiation_id,
            tenant_id=tenant_id,
            ping_id=str(ping["ping_id"]),
            lead_id=str(ping["lead_id"]),
            offer_id=str(ping["offer_id"]),
            buyer_id=str(ping["buyer_id"]),
            platform_id=self.config.platform_id,
            currency=ping_currency,
            floor_price_cents=ping_floor,
            max_rounds=max_rounds,
            rounds_remaining=max_rounds,
            platform_price_cents=price_cents,
            expires_at=format_iso_datetime(expires_at),
            test=bool(message.get("test", False)),
        )
        if not inserted:
            replay = self._negotiation_replay(
                negotiation_id, envelope, body_hash=body_hash
            )
            if replay is not None:
                return replay
            raise RequestError(
                "A negotiation with this id already exists", "LCP-004", 422
            )
        row = self.store.get_negotiation(negotiation_id)
        if row is None:  # pragma: no cover - insert succeeded a moment ago
            raise RequestError("Negotiation not found", "LCP-003", 404)
        ack = self._negotiation_ack(
            envelope,
            status="ACCEPTED",
            negotiation=self._negotiation_object(row, outcome="opened"),
        )
        self._record_negotiation_message(
            row,
            envelope,
            actor_role="platform",
            decision="quote",
            price_cents=price_cents,
            state_before="OPEN",
            state_after="OPEN",
            rejection_reason=None,
            ack=ack,
            audit_action="negotiation.opened",
            body_hash=body_hash,
        )
        return ack

    def submit_counter(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        """Answer the standing message: counter, accept, or refuse."""
        headers = headers or {}
        self._negotiation_preflight(envelope, "counter")
        self._validate_envelope(envelope)
        message = envelope["lcp"]["message"]
        if message["type"] != "counter":
            raise RequestError("Counter endpoint requires a counter message", "LCP-006")
        self._require_negotiation()
        self._validate_transport(message, headers)
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body if raw_body is not None else self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
            required_scope="negotiation:send",
        )
        if not self.store.consume_rate_limit(
            sender_id, limit=self.config.rate_limit_per_minute
        ):
            raise RequestError("Sender rate limit exceeded", "LCP-011", 429)
        if not self._advertises_negotiation(
            message["receiver_id"]
            if sender_id == self.config.platform_id
            else sender_id
        ):
            # C2: the peer that is not this platform must have advertised the
            # capability; a platform-role counter checks the buyer receiver.
            raise RequestError(
                "Counterparty does not advertise the negotiation capability",
                "LCP-006",
                400,
                details=[
                    "no parsed 1.x element with minor >= 2; quote/counter are "
                    "accepted/emitted only between v1.2+ peers (LEP-0009 C2)"
                ],
            )
        payload = envelope["lcp"]["payload"]
        self._negotiation_payload_guard("counter", payload)
        negotiation_id = str(payload["negotiation_id"])
        row = self.store.get_negotiation(negotiation_id)
        if row is None:
            # Unknown and retired ids answer identically: no existence oracle.
            return self._negotiation_ack(
                envelope,
                status="REJECTED",
                rejection_reason="unknown_negotiation",
                negotiation={
                    "negotiation_id": negotiation_id,
                    "outcome": "refused",
                    "currency": str(payload["currency"]),
                    "rounds_remaining": int(payload["rounds_remaining"]),
                },
            )
        is_platform = sender_id == self.config.platform_id
        if not is_platform and sender_id != str(row["buyer_id"]):
            raise RequestError(
                "Buyer is not authorized for this negotiation", "LCP-002", 401
            )
        if is_platform and str(row["platform_id"]) != self.config.platform_id:
            raise RequestError(
                "Platform is not authorized for this negotiation", "LCP-002", 401
            )
        tenant_id = self.auth.tenant_for(sender_id)
        if tenant_id is not None and tenant_id != str(row["tenant_id"]):
            raise RequestError(
                "Negotiation is not visible in this tenant", "LCP-002", 401
            )
        counterpart = str(row["buyer_id"]) if is_platform else str(row["platform_id"])
        if str(message["receiver_id"]) != counterpart:
            raise RequestError(
                "Counter receiver is not the other party of this negotiation",
                "LCP-002",
                401,
            )
        body_hash = self._message_digest(raw_body, envelope)
        replay = self._negotiation_replay(negotiation_id, envelope, body_hash=body_hash)
        if replay is not None:
            return replay
        if (
            self.store.negotiation_round_for_message(negotiation_id, str(message["id"]))
            is not None
        ):
            # Same message id under a different key is a conflicting reuse,
            # not a second turn in the negotiation.
            raise RequestError(
                "Message id already recorded with a different idempotency key",
                "LCP-005",
                409,
            )
        actor_role = "platform" if is_platform else "buyer"
        if str(payload["ping_id"]) != str(row["ping_id"]):
            self._negotiation_field_error("ping_id", row["ping_id"], payload["ping_id"])
        if str(payload["offer_id"]) != str(row["offer_id"]):
            self._negotiation_field_error(
                "offer_id", row["offer_id"], payload["offer_id"]
            )
        if str(payload["currency"]) != str(row["currency"]):
            self._negotiation_field_error(
                "currency", row["currency"], payload["currency"]
            )
        if int(payload["floor_price_cents"]) != int(row["floor_price_cents"]):
            self._negotiation_field_error(
                "floor_price_cents",
                row["floor_price_cents"],
                payload["floor_price_cents"],
            )
        if str(payload["expires_at"]) != str(row["expires_at"]):
            self._negotiation_field_error(
                "expires_at", row["expires_at"], payload["expires_at"]
            )
        if int(payload["rounds_remaining"]) != int(row["rounds_remaining"]):
            # Cheap ordering/replay guard: a stale echo fails instead of
            # corrupting the round count.
            self._negotiation_field_error(
                "rounds_remaining",
                row["rounds_remaining"],
                payload["rounds_remaining"],
            )
        ping = self.store.get_ping(str(row["ping_id"]))
        if ping is None:
            raise RequestError("Unknown ping_id", "LCP-003", 404)
        ping_message_id = self.store.decode_envelope(ping["envelope_json"])["lcp"][
            "message"
        ]["id"]
        answered = self.store.negotiation_round_for_message(
            negotiation_id, str(message.get("correlation_id") or "")
        )
        if answered is None:
            if message.get("correlation_id") != ping_message_id:
                raise RequestError(
                    "Counter correlation does not name a message in this negotiation",
                    "LCP-003",
                    400,
                )
            answered_actor = str(row["platform_id"])
            answered_price = int(row["platform_price_cents"])
        else:
            if str(answered["actor_id"]) == sender_id:
                # Strict alternation: one sender never answers itself.
                raise RequestError(
                    "A counter must answer the other party's message",
                    "LCP-004",
                    422,
                )
            answered_actor = str(answered["actor_id"])
            answered_price = (
                int(answered["price_cents"])
                if answered["price_cents"] is not None
                else 0
            )
        state = str(row["status"])
        timestamp = parse_iso_datetime(message["timestamp"])
        expires_at = parse_iso_datetime(row["expires_at"])
        decision = str(payload["decision"])
        stated_price = int(payload["price_cents"]) if "price_cents" in payload else None

        def refuse(
            reason: str,
            *,
            state_after: str = "REFUSED",
            audit_action: str = "negotiation.refused",
            errors: list[dict[str, Any]] | None = None,
            updates: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            fields: dict[str, Any] = {}
            if state_after != state and state_after in {"REFUSED", "EXPIRED"}:
                fields["status"] = state_after
                if state_after == "REFUSED":
                    # Exactly one reason is stored; a later message naming a
                    # closed id changes no state and keeps the original.
                    fields["rejection_reason"] = reason
            if updates:
                fields.update(updates)
            ack = self._negotiation_ack(
                envelope,
                status="REJECTED",
                rejection_reason=reason,
                errors=errors,
                negotiation=self._negotiation_object(
                    row,
                    outcome="refused" if state_after == "REFUSED" else "expired",
                    price_cents=stated_price,
                ),
            )
            self._record_negotiation_message(
                row,
                envelope,
                actor_role=actor_role,
                decision=decision,
                price_cents=stated_price,
                state_before=state,
                state_after=state_after,
                rejection_reason=reason,
                ack=ack,
                audit_action=audit_action,
                body_hash=body_hash,
                updates=fields,
            )
            return ack

        # C8: absolute expiry, judged by the message instant AND wall clock.
        # A late message never mutates a terminal state.
        current = datetime.now(timezone.utc)
        if expires_at is not None and (
            (timestamp is not None and timestamp >= expires_at) or current >= expires_at
        ):
            return refuse(
                "negotiation_expired",
                state_after="EXPIRED",
                audit_action="negotiation.expired",
            )
        if state in {"REFUSED", "EXPIRED"}:
            return refuse("negotiation_closed", state_after=state)
        if state in {"ACCEPTED", "REDEEMED"}:
            # The state table's illegal-transition rows: ACCEPTED -> COUNTERED,
            # a second accept, and REDEEMED -> anything are LCP-004.
            raise RequestError(
                f"Invalid negotiation transition {state} -> {decision}",
                "LCP-004",
                422,
            )
        if decision == "refuse":
            reason = str(payload["rejection_reason"])
            ack = self._negotiation_ack(
                envelope,
                status="ACCEPTED",
                negotiation=self._negotiation_object(
                    row, outcome="refused", price_cents=None
                ),
            )
            self._record_negotiation_message(
                row,
                envelope,
                actor_role=actor_role,
                decision=decision,
                price_cents=None,
                state_before=state,
                state_after="REFUSED",
                rejection_reason=reason,
                ack=ack,
                audit_action="negotiation.refused",
                body_hash=body_hash,
                updates={"status": "REFUSED", "rejection_reason": reason},
            )
            return ack
        if decision == "accept":
            if stated_price is None or stated_price != answered_price:
                self._negotiation_field_error(
                    "price_cents", answered_price, stated_price
                )
            if stated_price < int(row["floor_price_cents"]):
                return refuse(
                    "below_floor_price",
                    errors=[
                        {
                            "code": "LCP-100",
                            "message": "price_cents is below the restated floor",
                            "field": "payload.price_cents",
                        }
                    ],
                )
            token = new_negotiation_token()
            token_hash = hash_negotiation_token(token)
            self.store.insert_negotiation_token(
                token_hash=token_hash,
                negotiation_id=negotiation_id,
                tenant_id=str(row["tenant_id"]),
                buyer_id=str(row["buyer_id"]),
                price_cents=stated_price,
                currency=str(row["currency"]),
                expires_at=str(row["expires_at"]),
            )
            ack = self._negotiation_ack(
                envelope,
                status="ACCEPTED",
                negotiation=self._negotiation_object(
                    row,
                    outcome="accepted",
                    price_cents=stated_price,
                    binding_token=token,
                    binding_expires_at=str(row["expires_at"]),
                ),
            )
            self._record_negotiation_message(
                row,
                envelope,
                actor_role=actor_role,
                decision=decision,
                price_cents=stated_price,
                state_before=state,
                state_after="ACCEPTED",
                rejection_reason=None,
                ack=ack,
                audit_action="negotiation.accepted",
                body_hash=body_hash,
                updates={
                    "status": "ACCEPTED",
                    "accepted_price_cents": stated_price,
                    "binding_expires_at": str(row["expires_at"]),
                    "token_hash": token_hash,
                },
            )
            return ack

        # decision == "counter"
        if stated_price is None:  # schema requires it; defensive
            self._negotiation_field_error("price_cents", 0, None)
        if stated_price < int(row["floor_price_cents"]):
            return refuse(
                "below_floor_price",
                errors=[
                    {
                        "code": "LCP-100",
                        "message": "price_cents is below the restated floor",
                        "field": "payload.price_cents",
                    }
                ],
            )
        standing = int(row["platform_price_cents"])
        if is_platform:
            # C6: the platform may restate or raise its ask, never lower it.
            if stated_price < standing:
                return refuse("non_monotone_counter")
            ack = self._negotiation_ack(
                envelope,
                status="ACCEPTED",
                negotiation=self._negotiation_object(
                    row, outcome="countered", price_cents=stated_price
                ),
            )
            self._record_negotiation_message(
                row,
                envelope,
                actor_role=actor_role,
                decision=decision,
                price_cents=stated_price,
                state_before=state,
                state_after=state,
                rejection_reason=None,
                ack=ack,
                audit_action="negotiation.countered",
                body_hash=body_hash,
                updates={"platform_price_cents": stated_price},
            )
            return ack
        if int(row["rounds_remaining"]) <= 0:
            return refuse("round_limit_exceeded")
        previous = row["buyer_price_cents"]
        if previous is not None and stated_price <= int(previous):
            return refuse("non_monotone_counter")
        if stated_price >= standing:
            # Consequence 1: at or above the standing ask closes at the
            # COUNTER price, never silently downgraded to the ask.
            token = new_negotiation_token()
            token_hash = hash_negotiation_token(token)
            self.store.insert_negotiation_token(
                token_hash=token_hash,
                negotiation_id=negotiation_id,
                tenant_id=str(row["tenant_id"]),
                buyer_id=str(row["buyer_id"]),
                price_cents=stated_price,
                currency=str(row["currency"]),
                expires_at=str(row["expires_at"]),
            )
            ack = self._negotiation_ack(
                envelope,
                status="ACCEPTED",
                negotiation=self._negotiation_object(
                    row,
                    outcome="accepted",
                    price_cents=stated_price,
                    binding_token=token,
                    binding_expires_at=str(row["expires_at"]),
                ),
            )
            self._record_negotiation_message(
                row,
                envelope,
                actor_role=actor_role,
                decision=decision,
                price_cents=stated_price,
                state_before=state,
                state_after="ACCEPTED",
                rejection_reason=None,
                ack=ack,
                audit_action="negotiation.accepted",
                body_hash=body_hash,
                updates={
                    "status": "ACCEPTED",
                    "buyer_price_cents": stated_price,
                    "accepted_price_cents": stated_price,
                    "binding_expires_at": str(row["expires_at"]),
                    "token_hash": token_hash,
                },
            )
            return ack
        remaining = int(row["rounds_remaining"]) - 1
        ack = self._negotiation_ack(
            envelope,
            status="ACCEPTED",
            negotiation=self._negotiation_object(
                row,
                outcome="countered",
                price_cents=stated_price,
                rounds_remaining=remaining,
            ),
        )
        self._record_negotiation_message(
            row,
            envelope,
            actor_role=actor_role,
            decision=decision,
            price_cents=stated_price,
            state_before=state,
            state_after="COUNTERED",
            rejection_reason=None,
            ack=ack,
            audit_action="negotiation.countered",
            body_hash=body_hash,
            updates={
                "status": "COUNTERED",
                "rounds_remaining": remaining,
                "buyer_price_cents": stated_price,
            },
        )
        return ack

    def _redeem_negotiation_token(
        self,
        envelope: dict[str, Any],
        *,
        message: dict[str, Any],
        payload: dict[str, Any],
        sender_id: str,
        ping: Any,
        ping_envelope: dict[str, Any],
        raw_body: bytes | None,
    ) -> dict[str, Any]:
        """C7: first redemption creates the bid; a same-key replay is identical."""
        self._require_negotiation()
        token = str(payload["negotiation_binding_token"])
        if str(payload.get("decision")) != "accept":
            raise RequestError("A redemption bid must decision accept", "LCP-100", 400)
        body_hash = self._message_digest(raw_body, envelope)
        token_row = self.store.get_negotiation_token(hash_negotiation_token(token))
        if token_row is None:
            # Unknown hash answers exactly like a closed negotiation: never
            # "invalid token", which would confirm token existence. No
            # negotiation object is invented for an id this bid cannot name.
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="REJECTED",
                rejection_reason="negotiation_closed",
            )
        if str(token_row["buyer_id"]) != sender_id:
            raise RequestError(
                "Token is not authorized for this sender", "LCP-002", 401
            )
        tenant_id = self.auth.tenant_for(sender_id)
        if tenant_id is not None and tenant_id != str(token_row["tenant_id"]):
            raise RequestError("Token is not visible in this tenant", "LCP-002", 401)
        negotiation_id = str(token_row["negotiation_id"])
        row = self.store.get_negotiation(negotiation_id)
        if row is None:
            return build_ack(
                envelope,
                sender_id=self.config.platform_id,
                status="REJECTED",
                rejection_reason="negotiation_closed",
            )
        if str(row["ping_id"]) != str(payload["ping_id"]):
            self._negotiation_field_error("ping_id", row["ping_id"], payload["ping_id"])
        if str(row["buyer_id"]) != sender_id:
            raise RequestError("Token is not authorized for this buyer", "LCP-002", 401)
        price_cents = int(token_row["price_cents"])
        if "currency" in payload and str(payload["currency"]) != str(
            token_row["currency"]
        ):
            self._negotiation_field_error(
                "currency", token_row["currency"], payload["currency"]
            )
        if (
            "bid_price_cents" in payload
            and int(payload["bid_price_cents"]) != price_cents
        ):
            self._negotiation_field_error(
                "bid_price_cents", price_cents, payload["bid_price_cents"]
            )
        # Same envelope key + same bytes is a replay, not a reuse: return the
        # original acknowledgement byte for byte (as a dict) and write nothing.
        if (
            token_row["redeemed_at"] is not None
            and str(token_row["redemption_idempotency_key"])
            == str(message["idempotency_key"])
            and str(token_row["redemption_body_hash"]) == body_hash
        ):
            stored = json.loads(str(token_row["redemption_ack_json"]))
            if isinstance(stored, dict):
                return stored
        timestamp = parse_iso_datetime(message["timestamp"])
        token_expiry = parse_iso_datetime(token_row["expires_at"])
        current = datetime.now(timezone.utc)
        expired = token_expiry is not None and (
            (timestamp is not None and timestamp >= token_expiry)
            or current >= token_expiry
        )
        if expired or str(row["status"]) != "ACCEPTED":
            return self._negotiation_ack(
                envelope,
                status="REJECTED",
                rejection_reason="negotiation_closed",
                negotiation=self._negotiation_object(
                    row, outcome="refused", price_cents=price_cents
                ),
            )
        effective = json.loads(json.dumps(envelope))
        effective_payload = effective["lcp"]["payload"]
        effective_payload["bid_price_cents"] = price_cents
        effective_payload.setdefault("currency", str(token_row["currency"]))
        effective_payload.pop("negotiation_binding_token", None)
        ack = self._negotiation_ack(
            effective,
            status="RECEIVED",
            negotiation=self._negotiation_object(
                row, outcome="redeemed", price_cents=price_cents
            ),
        )
        redeemed = self.store.redeem_negotiation_token(
            token_hash=str(token_row["token_hash"]),
            negotiation_id=negotiation_id,
            buyer_id=sender_id,
            envelope=effective,
            ping=ping,
            idempotency_key=str(message["idempotency_key"]),
            body_hash=body_hash,
            redemption_ack=ack,
        )
        if not redeemed:
            refreshed = self.store.get_negotiation_token(str(token_row["token_hash"]))
            if (
                refreshed is not None
                and refreshed["redeemed_at"] is not None
                and str(refreshed["redemption_idempotency_key"])
                == str(message["idempotency_key"])
                and str(refreshed["redemption_body_hash"]) == body_hash
            ):
                return json.loads(str(refreshed["redemption_ack_json"]))
            return self._negotiation_ack(
                effective,
                status="REJECTED",
                rejection_reason="negotiation_closed",
                negotiation=self._negotiation_object(
                    row, outcome="refused", price_cents=price_cents
                ),
            )
        self.store.insert_audit(
            tenant_id=str(row["tenant_id"]),
            actor_id=sender_id,
            action="negotiation.redeemed",
            resource_type="negotiation",
            resource_id=negotiation_id,
            metadata={
                "ping_id": str(row["ping_id"]),
                "offer_id": str(row["offer_id"]),
                "buyer_id": str(row["buyer_id"]),
                "actor_role": "buyer",
                "before_state": "ACCEPTED",
                "after_state": "REDEEMED",
                "decision": "accept",
                "price_cents": price_cents,
                "rounds_remaining": int(row["rounds_remaining"]),
                "message_id": str(message["id"]),
            },
        )
        return ack

    def _expire_negotiations(self) -> None:
        """C8 sweep: idempotent, one audit row per negotiation it moves."""
        for row in self.store.claim_expired_negotiations():
            self.store.insert_audit(
                tenant_id=str(row["tenant_id"]),
                actor_id=self.config.platform_id,
                action="negotiation.expired",
                resource_type="negotiation",
                resource_id=str(row["negotiation_id"]),
                metadata={
                    "ping_id": str(row["ping_id"]),
                    "offer_id": str(row["offer_id"]),
                    "buyer_id": str(row["buyer_id"]),
                    "before_state": str(row["status"]),
                    "after_state": "EXPIRED",
                    "expires_at": str(row["expires_at"]),
                },
            )

    # D2/v1.2 — PROCESSED acknowledgement. A post is the only delivery a buyer
    # can acknowledge this way, and PROCESSED is terminal-success
    # (ACCEPTED -> PROCESSED): it never re-opens a payable, never rewrites
    # pricing, and never becomes a precondition for SOLD.
    _ACK_TERMINAL_STATUSES = frozenset({"REJECTED", "EXPIRED", "SOLD"})

    def submit_ack(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        headers = headers or {}
        self._validate_envelope(envelope)
        message = envelope["lcp"]["message"]
        if message["type"] != "ack":
            raise RequestError("Ack endpoint requires an ack message", "LCP-006", 400)
        self._validate_transport(message, headers)
        if message["receiver_id"] != self.config.platform_id:
            raise RequestError("Message receiver is not this platform", "LCP-002", 401)
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body if raw_body is not None else self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
        )
        payload = envelope["lcp"]["payload"]
        if payload.get("status") != "PROCESSED":
            raise RequestError(
                "Only PROCESSED acknowledgements are accepted by this endpoint",
                "LCP-006",
                400,
            )
        lead_id = payload.get("lead_id")
        if not isinstance(lead_id, str) or not lead_id:
            raise RequestError("An ack for a post must carry lead_id", "LCP-003", 400)
        delivery = next(
            (
                row
                for row in self.store.deliveries_for_lead(lead_id)
                if row["message_id"] == payload.get("original_message_id")
            ),
            None,
        )
        # PROCESSED answers a delivered post; a lead/ping/event (or a post
        # this platform never delivered) is not a legal target.
        if (
            delivery is None
            or delivery["kind"] != "post"
            or delivery["buyer_id"] != sender_id
        ):
            if delivery is not None and delivery["buyer_id"] != sender_id:
                raise RequestError(
                    "Buyer is not authorized for this delivery", "LCP-002", 401
                )
            transition = InvalidStatusTransition(
                "PROCESSED is only valid for a post delivery this buyer received"
            )
            raise RequestError(str(transition), "LCP-004", 422) from transition

        lead_row = self.store.get_lead(lead_id)
        current = str(delivery["status"])
        terminal = self._ACK_TERMINAL_STATUSES
        if current in terminal or (
            lead_row is not None and str(lead_row["status"]) in terminal
        ):
            transition = InvalidStatusTransition(
                f"Invalid ack transition {current} -> PROCESSED"
            )
            raise RequestError(str(transition), "LCP-004", 422) from transition
        # Idempotent: a repeat returns the deterministic original ack and
        # writes nothing (no second audit, no second status change).
        if current != "PROCESSED":
            if current != "DELIVERED":
                transition = InvalidStatusTransition(
                    f"A post must be delivered before PROCESSED (current: {current})"
                )
                raise RequestError(str(transition), "LCP-004", 422) from transition
            self.store.mark_delivery(
                delivery["delivery_id"],
                status="PROCESSED",
                attempts=int(delivery["attempts"]),
                next_attempt_at=now_iso(),
            )
            self.store.insert_audit(
                tenant_id=self.auth.tenant_for(sender_id)
                or self.config.routing_tenant_id,
                actor_id=sender_id,
                action="post.processed",
                resource_type="delivery",
                resource_id=delivery["delivery_id"],
                metadata={
                    "lead_id": lead_id,
                    "offer_id": delivery["offer_id"],
                    "message_id": delivery["message_id"],
                },
            )
        return self._processed_ack(
            delivery, lead_id=lead_id, test=bool(message.get("test"))
        )

    def _processed_ack(
        self, delivery: Any, *, lead_id: str, test: bool
    ) -> dict[str, Any]:
        """The ack this platform returns for a PROCESSED post delivery."""
        post_message = self.store.decode_envelope(delivery["envelope_json"])["lcp"][
            "message"
        ]
        # Deterministic id: a repeated PROCESSED returns the original
        # acknowledgement, not a new one.
        message_id = str(uuid5(NAMESPACE_URL, f"lcp:processed:{post_message['id']}"))
        return {
            "lcp": {
                "version": SUPPORTED_LCP_VERSIONS[-1],
                "message": {
                    "id": message_id,
                    "type": "ack",
                    "timestamp": now_iso(),
                    "sender_id": self.config.platform_id,
                    "receiver_id": delivery["buyer_id"],
                    "correlation_id": post_message["id"],
                    "idempotency_key": (
                        f"{self.config.platform_id}-ack-{post_message['id']}-PROCESSED"
                    ),
                    "test": test,
                },
                "payload": {
                    "original_message_id": post_message["id"],
                    "status": "PROCESSED",
                    "lead_id": lead_id,
                },
            }
        }

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
            result = match_offer(
                offer,
                payload,
                sender_id=row["sender_id"],
                profiles=self.store,
                service_areas=self.service_areas,
            )
            if result.matched and not self._capacity_available(offer):
                result = MatchResult(
                    False,
                    result.reasons + ("capacity_exceeded",),
                    profile=result.profile,
                    predicates=result.predicates,
                    service_area=result.service_area,
                )
            matches.append((offer, result))
            self._record_match_evaluation(lead_id=lead_id, offer=offer, result=result)
        matching_offers = [offer for offer, result in matches if result.matched]
        direct_offers = [
            offer
            for offer in matching_offers
            if offer.get("routing_mode", "auction") == "direct"
        ]
        if direct_offers:
            direct_offers.sort(key=self._pacing_priority)
            # No per-pass budget slice (LEP-0003 §5 item 1); the single
            # write-time decision inside _create_direct_post counts
            # distinct awarded buyers.
            matching_offers = direct_offers
        exclusive = (payload.get("exclusivity") or {}).get(
            "exclusivity", "shared"
        ) == "exclusive"
        created_pings = 0
        created_posts = 0
        for offer in matching_offers:
            if offer.get("routing_mode", "auction") == "direct":
                if self._create_direct_post(envelope, offer):
                    created_posts += 1
                    if exclusive:
                        break
            elif not self.store.has_ping_for_offer(lead_id, offer["offer_id"]):
                if self._create_ping(envelope, offer):
                    created_pings += 1
        if created_posts:
            self.store.update_lead_status(lead_id, "POSTED", reason="direct_delivery")
        elif created_pings:
            self.store.update_lead_status(lead_id, "PINGED")

    def process_once(self) -> None:
        """Process retention, durable routing jobs, webhooks, and auction expiry."""
        # W4-05: the deferred history/decision/audit writes drain here, off the
        # intake path. Flushing first keeps the trail current before the
        # retention sweep decides what is expired.
        self.store.flush_history()
        # W4-06: batched, interval-gated envelope redaction + history deletion.
        self.store.sweep_retention()
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
        # LEP-0009 C8: absolute expiry sweep (idempotent), then the non-PII
        # retention purge for terminal rows past their window.
        self._expire_negotiations()
        self.store.purge_expired_negotiations()
        # I8/W4-02: the match audit is retention bounded like decision history.
        self.store.purge_match_audits(
            format_iso_datetime(
                datetime.now(timezone.utc)
                - timedelta(days=self.config.match_audit_retention_days)
            )
        )
        self._process_attachment_deletions()
        self._process_routing_jobs()
        self._process_deliveries()
        self._process_event_subscriptions()
        self._expire_unacknowledged_posts()
        self._process_attachment_deletions()
        # LEP-0003 §5 item 3: the auction-close re-route claims each expired
        # ping exactly once (conditional UPDATE under the store transaction;
        # FOR UPDATE SKIP LOCKED on Postgres) instead of racing an unlocked
        # list_expired_open_pings sweep across replicas.
        for ping in self.store.claim_expired_open_pings():
            if self.store.all_pings_terminal(ping["lead_id"]):
                self._route_lead(ping["lead_id"])

    def _extensions_for_delivery(
        self, lead_payload: dict[str, Any], buyer_id: str
    ) -> dict[str, Any] | None:
        """LEP-0006 Track B §B.1/§B.3.4: extensions that may ride a post.

        Three delivery decisions, in order:

        * absent/empty -> ``None`` (bit-for-bit v1.0 post);
        * receiver not advertising parsed-semver >= 1.1 -> ``None``; ``lead``
          and ``post`` are closed objects, so a v1.1 field sent to a v1.0
          receiver fails its strict allowlist (capability-gated, not
          ignore-gated);
        * plaintext contact spans -> redacted deterministically and logged
          per lead. Ingest already rejects them, so this is the delivery-side
          backstop the record requires, never a per-recipient exception.
        """
        extensions = lead_payload.get("extensions")
        if not isinstance(extensions, dict) or not extensions:
            return None
        if not self.auth.advertises_slot_protocol(buyer_id):
            return None
        clean, redacted = redact_extension_pii(extensions)
        if redacted:
            self.store.insert_audit(
                tenant_id=self.config.routing_tenant_id,
                actor_id=self.config.platform_id,
                action="lead.extensions_redacted",
                resource_type="lead",
                resource_id=str(lead_payload.get("lead_id", "")),
                metadata={"buyer_id": buyer_id, "fields": redacted},
            )
        return clean

    def _create_direct_post(
        self, lead_envelope: dict[str, Any], offer: dict[str, Any]
    ) -> bool:
        secret = self.auth.secret_for(offer["buyer_id"])
        webhook_url = offer.get("webhook_url")
        if not secret or not webhook_url:
            return False
        lead_payload = lead_envelope["lcp"]["payload"]
        post = build_post(
            lead_envelope,
            offer,
            platform_id=self.config.platform_id,
            price_cents=offer["floor_price_cents"],
            buyer_reference=None,
            correlation_id=lead_envelope["lcp"]["message"]["id"],
            contacted_hashes=self._contacted_hashes_for(
                lead_payload, offer["buyer_id"]
            ),
            extensions=self._extensions_for_delivery(lead_payload, offer["buyer_id"]),
            duplicate_resubmission=self.store.has_prior_post_delivery(
                lead_payload["lead_id"], offer["buyer_id"]
            ),
        )
        self.validator.require_valid_envelope(post)
        # Direct awards pass the SAME single write-time decision point as
        # the auction path (LEP-0003 §5 item 1); the advisory
        # has_delivery_for_offer pre-read is gone.
        outcome = self.store.try_award_post(
            lead_id=lead_payload["lead_id"],
            ping_id=None,
            offer_id=offer["offer_id"],
            buyer_id=offer["buyer_id"],
            envelope=post,
            webhook_url=webhook_url,
            max_buyers=self._slot_budget(lead_payload),
        )
        if outcome != "awarded":
            return False
        self._record_pending_payable(lead_envelope, offer, offer["floor_price_cents"])
        return True

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
        last_day = (
            (local.replace(day=28) + timedelta(days=4)).replace(day=1)
            - timedelta(days=1)
        ).day
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

    def _enqueue_audit(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Queue an intake audit row instead of writing it on the request path.

        W4-05: the lead is already committed, so the audit trail must never be
        able to slow or fail intake. The worker flushes; ``list_audit_events``
        flushes too, so a reader still sees read-your-write. A saturated queue
        is recorded as ``history.write_drop_risk``/``history_queue.dropped``
        rather than raised (see ``Store.enqueue_history``).
        """
        self.store.enqueue_history(
            lambda: self.store.insert_audit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata=metadata,
            )
        )

    def _record_match_evaluation(
        self, *, lead_id: str, offer: dict[str, Any], result: MatchResult
    ) -> None:
        """Queue the decision row and its explainable audit row together.

        W4-05: both rows are history, not routing state — nothing downstream
        of intake reads them back — so they go to the bounded history queue
        and are flushed by the worker. One closure keeps the pair ordered and
        stops a half-written decision/audit pair from a torn queue drain.
        """

        def write() -> None:
            self.store.insert_match_decision(
                lead_id=lead_id,
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                matched=result.matched,
                reasons=result.reasons,
            )
            self.store.insert_match_audit(
                lead_id=lead_id,
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                matched=result.matched,
                reasons=result.reasons,
                profile=result.profile,
                predicates=result.predicates,
                service_area=result.service_area,
            )

        self.store.enqueue_history(write)

    def _capacity_available(self, offer: dict[str, Any]) -> bool:
        now = datetime.now(timezone.utc)
        if offer.get("daily_cap"):
            day_start = now.strftime("%Y-%m-%dT00:00:00Z")
            if (
                self.store.count_offer_deliveries(offer["offer_id"], since=day_start)
                >= offer["daily_cap"]
            ):
                return False
        if offer.get("hourly_cap"):
            hour_start = now.strftime("%Y-%m-%dT%H:00:00Z")
            if (
                self.store.count_offer_deliveries(offer["offer_id"], since=hour_start)
                >= offer["hourly_cap"]
            ):
                return False
        if (
            offer.get("monthly_maximum_payable")
            and offer.get("monthly_quota_policy") == "hard_cap"
        ):
            summary = self.store.payable_summary(
                offer["offer_id"], self._month_key(offer, now)
            )
            if summary["payable"] + summary["pending"] >= int(
                offer["monthly_maximum_payable"]
            ):
                return False
        return True

    # ─── LEP-0003 slot accounting helpers ──────────────────────────────────

    @staticmethod
    def _slot_budget(lead_payload: dict[str, Any]) -> int:
        """Slot budget in UNITS OF DISTINCT AWARDED BUYERS (LEP-0003 §1).

        ``exclusive`` is exactly the one-slot special case; a ``shared`` lead
        with ``max_buyers`` has that budget; the undeclared case is normed to
        1, matching the platform's historical implicit cap
        (``max(1, int(exclusivity.get("max_buyers", 1)))``).
        """
        exclusivity = lead_payload.get("exclusivity") or {}
        if exclusivity.get("exclusivity", "shared") == "exclusive":
            return 1
        return max(1, int(exclusivity.get("max_buyers", 1)))

    def _remaining_slots(self, lead_payload: dict[str, Any], lead_id: str) -> int:
        return self._slot_budget(lead_payload) - self.store.count_awarded_buyers(
            lead_id
        )

    def _contacted_hashes_for(
        self, lead_payload: dict[str, Any], buyer_id: str
    ) -> list[str] | None:
        """Per-pair re-keying of this lead's contact history (LEP-0003 §3).

        "The platform may therefore re-key only from the contact values it
        delivered itself - never from ``CONTACTED`` report digests." The only
        plaintext the reference platform holds for a lead is the delivered
        ``consumer.phone`` (verbatim preimage rule); a stored attestation is
        re-keyable only when it equals the digest the reporting buyer
        computed under its own pair secret over that value. Attestations the
        platform cannot tie to held plaintext stay stored and are never
        forwarded. ``None`` = v1.0 bytes: capability-gated egress, no
        reports, nothing re-keyable, or a phone-less lead (this platform
        hashes phone only; the email-form re-key path ships with the
        dedup-hash wave - SPEC §9 pin + published contact-hash vectors -
        so no email-form digest is ever emitted here).
        """
        if not self.auth.advertises_slot_protocol(buyer_id):
            return None
        lead_id = lead_payload.get("lead_id")
        if not lead_id:
            return None
        stored = self.store.list_contacted_digests(lead_id)
        if not stored:
            return None
        phone = (lead_payload.get("consumer") or {}).get("phone")
        if not phone:
            return None
        buyer_secret = self.auth.secret_for(buyer_id)
        if not buyer_secret:
            return None
        rekeyed: list[str] = []
        target = None
        for row in stored:
            reporter_secret = self.auth.secret_for(row["reported_by"])
            if not reporter_secret:
                continue
            if row["digest"] == pair_phone_hash(reporter_secret, phone):
                if target is None:
                    target = pair_phone_hash(buyer_secret, phone)
                if target not in rekeyed:
                    rekeyed.append(target)
        return rekeyed or None

    def _create_ping(
        self, lead_envelope: dict[str, Any], offer: dict[str, Any]
    ) -> bool:
        secret = self.auth.secret_for(offer["buyer_id"])
        if not secret:
            # A buyer without a bilateral secret cannot receive a safe ping.
            return False
        lead_id = lead_envelope["lcp"]["payload"]["lead_id"]
        if self.store.has_ping_for_offer(lead_id, offer["offer_id"]):
            return False
        advertised = self.auth.advertises_slot_protocol(offer["buyer_id"])
        remaining = self._remaining_slots(lead_envelope["lcp"]["payload"], lead_id)
        if advertised and remaining <= 0:
            # LEP-0003 §1: "The platform MUST NOT send a ping with
            # slots_available: 0 - zero remaining means stop pinging, not
            # ping with zero."
            return False
        ping, expires_at = build_ping(
            lead_envelope,
            offer,
            platform_id=self.config.platform_id,
            buyer_secret=secret,
            slots_available=remaining if advertised else None,
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

    def _settlement_price(
        self,
        amount_cents: Any,
        currency: Any,
        pricing: Any,
        settlement_currency: Any,
        *,
        counterpart: Any = None,
        now: datetime | None = None,
    ) -> tuple[Decimal | None, str | None]:
        """D7: one price expressed in settlement minor units, or a reason.

        Same currency compares directly. A cross-currency price needs a
        present, well-formed, non-stale rate from a single source; missing,
        stale, malformed, expired, or mixed-source metadata returns
        ``(None, reason)`` and the caller excludes the offer from ranking.
        Exact decimal arithmetic is used for the auction — never a rounded or
        clamped value.
        """
        if not _is_int(amount_cents) or amount_cents < 0:
            return None, "fx_amount_malformed"
        native = str(currency or "USD").upper()
        settlement = str(settlement_currency or "USD").upper()
        if not _CURRENCY_RE.fullmatch(native):
            return None, "fx_currency_malformed"
        if not _CURRENCY_RE.fullmatch(settlement):
            return None, "fx_settlement_currency_malformed"
        if native == settlement:
            return Decimal(amount_cents), None
        metadata = pricing if isinstance(pricing, dict) else {}
        if any(not metadata.get(field) for field in _FX_METADATA_FIELDS):
            return None, "fx_rate_missing"
        rate_text = metadata.get("fx_rate")
        if not isinstance(rate_text, str) or not _FX_RATE_RE.fullmatch(rate_text):
            return None, "fx_rate_malformed"
        try:
            rate = Decimal(rate_text)
        except InvalidOperation:
            return None, "fx_rate_malformed"
        if rate <= 0:
            return None, "fx_rate_malformed"
        if rate > _FX_RATE_MAX:
            return None, "fx_rate_malformed"
        source = metadata.get("fx_source")
        if not isinstance(source, str) or not source.strip():
            return None, "fx_source_missing"
        if isinstance(counterpart, dict):
            other_source = counterpart.get("fx_source")
            if other_source is not None and other_source != source:
                return None, "fx_source_mixed"
        reference = now or datetime.now(timezone.utc)
        timestamp = parse_iso_datetime(metadata.get("fx_timestamp"))
        if timestamp is None:
            return None, "fx_timestamp_malformed"
        if (reference - timestamp).total_seconds() > self.config.fx_max_age_seconds:
            return None, "fx_rate_stale"
        expires_at = parse_iso_datetime(metadata.get("fx_expires_at"))
        if expires_at is None:
            return None, "fx_expires_at_malformed"
        if expires_at <= reference:
            return None, "fx_rate_expired"
        # LEP-0010 §4.2: `fx_rate` is a MAJOR-unit quote while the amount is
        # MINOR units, so the exponent difference is part of the arithmetic:
        #   settlement_minor = native_minor × 10^(exp_settlement − exp_native) ÷ rate
        # Dropping the factor is the 100× USD→JPY overcharge the LEP forbids.
        exponent_delta = _minor_unit_exponent(settlement) - _minor_unit_exponent(native)
        scaled = Decimal(amount_cents) * (Decimal(10) ** exponent_delta)
        return scaled / rate, None

    def _record_non_comparable_bid(self, bid: Any, reason: str) -> None:
        """Attach the exclusion reason to the auction decision record."""
        self.store.insert_audit(
            tenant_id=self.config.routing_tenant_id,
            actor_id=self.config.platform_id,
            action="bid.non_comparable",
            resource_type="bid",
            resource_id=str(bid["bid_id"]),
            metadata={
                "lead_id": bid["lead_id"],
                "offer_id": bid["offer_id"],
                "buyer_id": bid["buyer_id"],
                "currency": bid["currency"],
                "reason": reason,
            },
        )

    def _route_lead(self, lead_id: str) -> None:
        row = self.store.get_lead(lead_id)
        if not row or row["status"] in {
            "ACCEPTED",
            "REJECTED",
            "EXPIRED",
            "DUPLICATE",
            "ERASED",
        }:
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
        candidates: list[
            tuple[sqlite3_row, sqlite3_row, dict[str, Any], Decimal, dict[str, Any]]
        ] = []
        converted_sources: set[str] = set()
        now = datetime.now(timezone.utc)
        for bid in bids:
            ping = pings.get(bid["ping_id"])
            offer = self.store.get_offer(bid["offer_id"])
            if not ping or not offer or bid["decision"] != "accept":
                continue
            if (
                ping["buyer_id"] != offer["buyer_id"]
                or bid["buyer_id"] != offer["buyer_id"]
            ):
                continue
            if not self._capacity_available(offer):
                continue
            bid_payload = self.store.decode_envelope(bid["envelope_json"])["lcp"][
                "payload"
            ]
            settlement_price, reason = self._settlement_price(
                bid["bid_price_cents"],
                bid["currency"],
                bid_payload,
                offer.get("currency", "USD"),
                counterpart=offer,
                now=now,
            )
            if reason is not None:
                self._record_non_comparable_bid(bid, reason)
                continue
            assert settlement_price is not None
            converted = (
                str(bid["currency"]).upper()
                != str(offer.get("currency", "USD")).upper()
            )
            if settlement_price < Decimal(int(offer["floor_price_cents"])):
                # LEP-0010 §6: a converted candidate excluded under the floor
                # records WHY. "Your AUD bid was below a USD floor after
                # conversion" is exactly the surprise the record removes; a
                # same-currency floor exclusion keeps the existing silent
                # behaviour (the LEP permits either, and no regression is
                # introduced by leaving it).
                if converted:
                    self._record_non_comparable_bid(bid, "price_below_floor")
                continue
            if converted:
                converted_sources.add(str(bid_payload.get("fx_source")))
            # LEP-0003 §5 item 4: award-eligible ping state. The OPEN-ping
            # requirement previously lived only in submit_bid, so a
            # post-expiry bid that reached the store by another path stayed
            # award-eligible. The platform's expiry predicate is
            # ``expires_at <= now``, so under canonical whole-second
            # timestamps (LEP-0002) a bid received at or after the ping's
            # expiry boundary is post-expiry and refused; bids received
            # strictly before it - including the auction-close path where
            # the sweep expires the ping and then re-routes - remain
            # awardable. Erased/rejected pings never are. Canonical
            # whole-second timestamps compare lexicographically.
            if ping["status"] not in {"OPEN", "EXPIRED", "WON"}:
                continue
            if (bid["received_at"] or "") >= (ping["expires_at"] or ""):
                continue
            candidates.append((bid, ping, offer, settlement_price, bid_payload))
        # D7: rates from different providers are not a common basis. Mixed
        # sources make every converted offer non-comparable (never pick one
        # source arbitrarily); same-currency offers keep comparing directly.
        if len(converted_sources) > 1:
            comparable: list[
                tuple[sqlite3_row, sqlite3_row, dict[str, Any], Decimal, dict[str, Any]]
            ] = []
            for candidate in candidates:
                candidate_bid, _, candidate_offer, _, _ = candidate
                if (
                    str(candidate_bid["currency"]).upper()
                    != str(candidate_offer.get("currency", "USD")).upper()
                ):
                    self._record_non_comparable_bid(candidate_bid, "fx_source_mixed")
                else:
                    comparable.append(candidate)
            candidates = comparable
        candidates.sort(
            key=lambda item: (
                -item[3],
                item[0]["estimated_contact_seconds"]
                if item[0]["estimated_contact_seconds"] is not None
                else 2**31,
                item[0]["received_at"],
                item[0]["buyer_id"],
            )
        )
        lead_payload = self.store.decode_envelope(row["envelope_json"])["lcp"][
            "payload"
        ]
        budget = self._slot_budget(lead_payload)
        exclusive = (lead_payload.get("exclusivity") or {}).get(
            "exclusivity", "shared"
        ) == "exclusive"
        if not candidates:
            self.store.update_lead_status(lead_id, "EXPIRED")
            return

        lead_envelope = self.store.decode_envelope(row["envelope_json"])
        delivered = 0
        for bid, ping, offer, settlement_price, bid_payload in candidates:
            if not offer.get("webhook_url"):
                continue
            # Presentation rounds half-up only here, after the auction has
            # ranked on the exact settlement price. The rounded cents never
            # feed back into ranking.
            settlement_cents = int(
                settlement_price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            post = build_post(
                lead_envelope,
                offer,
                platform_id=self.config.platform_id,
                price_cents=settlement_cents,
                buyer_reference=bid_payload.get("buyer_reference"),
                correlation_id=ping["message_id"],
                contacted_hashes=self._contacted_hashes_for(
                    lead_payload, offer["buyer_id"]
                ),
                extensions=self._extensions_for_delivery(
                    lead_payload, offer["buyer_id"]
                ),
                duplicate_resubmission=self.store.has_prior_post_delivery(
                    lead_payload["lead_id"], offer["buyer_id"]
                ),
                # D7/LEP-0010 §2: the converted award echoes the buyer's
                # native amount, the settlement currency, and the winning
                # bid's four fx_* values verbatim; `price_cents` above stays
                # in settlement minor units. A same-currency award carries no
                # FX evidence (its bid's metadata, if any, is inert).
                fx=bid_fx_evidence(bid_payload, price_cents=int(bid["bid_price_cents"]))
                if str(bid["currency"]).upper() != str(offer["currency"]).upper()
                else None,
            )
            self.validator.require_valid_envelope(post)
            # LEP-0003 §5 item 1: the remaining-budget check and the post
            # write occur in ONE transaction at this single decision point,
            # counting distinct awarded buyers (§1 Units). The per-pass
            # ``winners = candidates[:max_winners]`` slice and the advisory
            # ``has_delivery_for_offer`` read-then-insert pair are gone:
            # they let two overlapping passes deliver 4 posts against
            # max_buyers 2 (dossier rows 14-15), and a POSTED re-entry
            # re-budgeted from zero.
            outcome = self.store.try_award_post(
                lead_id=lead_id,
                ping_id=ping["ping_id"],
                offer_id=offer["offer_id"],
                buyer_id=offer["buyer_id"],
                envelope=post,
                webhook_url=offer["webhook_url"],
                max_buyers=budget,
            )
            if outcome == "awarded":
                self._record_pending_payable(lead_envelope, offer, settlement_cents)
                self.store.mark_ping_won(ping["ping_id"])
                delivered += 1
                if exclusive:
                    break
            elif outcome == "duplicate_offer":
                # The award already exists (re-entry / concurrent pass):
                # keep the historical winner marking, mint nothing new.
                self.store.mark_ping_won(ping["ping_id"])
                delivered += 1
                if exclusive:
                    break
        self.store.update_lead_status(lead_id, "POSTED" if delivered else "EXPIRED")

    def _process_attachment_deletions(self) -> None:
        for job in self.store.list_due_attachment_deletions():
            attempts = int(job["attempts"]) + 1
            try:
                self.attachments.delete(job["storage_ref"])
            except (
                Exception
            ) as exc:  # deletion is durable; retry without exposing storage details
                if attempts >= self.config.max_delivery_attempts:
                    status = "FAILED"
                    next_attempt = now_iso()
                else:
                    status = "RETRY"
                    delay = self._retry_delays[
                        min(attempts - 1, len(self._retry_delays) - 1)
                    ]
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
                            raise SecurityPolicyError(
                                "Webhook response exceeded the configured limit"
                            )
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

    def _expire_unacknowledged_posts(self, *, now: datetime | None = None) -> None:
        """SPEC §9: a post its consumer never acknowledges expires the lead.

        The window runs from award to the receiver's acknowledgement and
        defaults to 30 seconds. Only unacknowledged deliveries are in scope — a
        2xx acknowledgement is the response the platform observes and the event
        that makes a post payable, so an acknowledged post is never expired
        here. §9 makes re-offer MAY; this platform does not re-offer because the
        awarded copy already consumed its slot (LEP-0003), so the expiry is
        terminal and the operator can route the lead again deliberately.
        """
        reference = now or datetime.now(timezone.utc)
        created_before = (
            reference - timedelta(seconds=self.config.post_response_timeout_seconds)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        for delivery in self.store.list_unacknowledged_post_deliveries(created_before):
            lead_id = delivery["lead_id"]
            attempts = int(delivery["attempts"])
            self.store.mark_delivery(
                delivery["delivery_id"],
                status="FAILED",
                attempts=attempts,
                next_attempt_at=now_iso(),
                last_error="Post response timeout",
            )
            self.store.record_dead_letter(
                queue_type="delivery",
                resource_id=delivery["delivery_id"],
                lead_id=lead_id,
                attempts=attempts,
                last_error="Post response timeout",
            )
            if not self.store.expire_lead(lead_id):
                continue
            self.store.insert_audit(
                tenant_id=self.config.routing_tenant_id,
                actor_id=self.config.platform_id,
                action="lead.post_response_timeout",
                resource_type="lead",
                resource_id=lead_id,
                metadata={
                    "delivery_id": delivery["delivery_id"],
                    "offer_id": delivery["offer_id"],
                    "timeout_seconds": self.config.post_response_timeout_seconds,
                },
            )

    def _queue_delivery_event(self, delivery: Any, post: dict[str, Any]) -> None:
        event = build_event(
            delivery["lead_id"],
            "DELIVERED",
            platform_id=self.config.platform_id,
            receiver_id=delivery["buyer_id"],
            correlation_id=post["lcp"]["message"]["id"],
            details={
                "delivery_id": delivery["delivery_id"],
                "offer_id": delivery["offer_id"],
            },
            test=post["lcp"]["message"].get("test", False),
        )
        self.validator.require_valid_envelope(event)
        if not self.store.insert_event(
            delivery["lead_id"],
            "DELIVERED",
            event,
            record_stream=self.config.feature_events,
        ):
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
        seconds = (
            int(seconds_value) if isinstance(seconds_value, (int, float)) else None
        )
        call_status = details.get("call_status", details.get("status"))
        if rules.get("require_call_answered") and call_status not in {
            "answered",
            "connected",
        }:
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

    @staticmethod
    def _validate_event_details_pii(payload: dict[str, Any]) -> None:
        """SPEC §4 class rule: ``event.details`` carries no consumer PII.

        "Event ``details`` MUST NOT carry consumer PII — contact values
        travel only as per-pair keyed digests where a message defines them."
        ``details`` is ``additionalProperties: true`` by design, so the rule
        is a receiver ingest MUST (same boundary as LEP-0003 §4's report
        rules): every event name and both reporting directions are covered,
        because #198 measured the plaintext surviving on
        ``CONSENT_WITHDRAWN`` while only ``CONTACTED`` was validated.

        The walk is iterative, not recursive: ``details`` nesting is
        attacker-controlled and a deep object must not raise ``RuntimeError``
        through (a 500 instead of a validation rejection). Shape checks only —
        no DLP inference, no normalization of the value it refuses.
        """
        stack: list[tuple[Any, str]] = [(payload.get("details"), "details")]
        while stack:
            node, path = stack.pop()
            if isinstance(node, dict):
                stack.extend((value, f"{path}.{key}") for key, value in node.items())
                continue
            if isinstance(node, list):
                stack.extend(
                    (value, f"{path}[{index}]") for index, value in enumerate(node)
                )
                continue
            if not isinstance(node, str):
                continue
            value = node.strip()
            if has_plaintext_contact(value):
                raise RequestError(
                    "Event details must not carry plaintext contact values "
                    f"({path}); contact values travel only as per-pair "
                    "keyed digests",
                    "LCP-100",
                    details={"field": path},
                )

    @staticmethod
    def _validate_final_pricing_details(payload: dict[str, Any]) -> None:
        """LEP-0006 Track A ingest bounds for the recommended `details` shape.

        "Malformed or out-of-bound reports are validation rejections, not
        stores." Absent fields pass — the shape is SHOULD-level guidance and
        a publisher that lost the auction may report a price without a
        ``ping_id``. Present fields must still be well-formed: integer minor
        units (A-2: a float-dollar price is the migration trap this refuses),
        an explicit uppercase currency, and the typo/duration ceilings.
        """
        details = payload.get("details")
        if not isinstance(details, dict):
            return
        rejections: list[str] = []
        price = details.get("winning_price_cents")
        if price is not None and not (
            _is_int(price) and 0 < price <= _FINAL_PRICING_MAX_PRICE_CENTS
        ):
            rejections.append(
                "winning_price_cents must be an integer in 1.."
                f"{_FINAL_PRICING_MAX_PRICE_CENTS}"
            )
        currency = details.get("currency")
        if currency is not None and not (
            isinstance(currency, str) and _CURRENCY_RE.match(currency)
        ):
            rejections.append("currency must be an uppercase ISO 4217 alpha-3")
        duration = details.get("duration_seconds")
        if duration is not None and not (
            _is_int(duration) and 0 <= duration <= _FINAL_PRICING_MAX_DURATION_SECONDS
        ):
            rejections.append(
                "duration_seconds must be an integer in 0.."
                f"{_FINAL_PRICING_MAX_DURATION_SECONDS}"
            )
        ping_id = details.get("ping_id")
        if ping_id is not None and not isinstance(ping_id, str):
            rejections.append("ping_id must be a string")
        sale_scope = details.get("sale_scope")
        if sale_scope is not None and not isinstance(sale_scope, str):
            rejections.append("sale_scope must be a string")
        sold_at = details.get("sold_at")
        if sold_at is not None and parse_iso_datetime(sold_at) is None:
            rejections.append("sold_at must be an RFC 3339 date-time")
        if rejections:
            raise RequestError(
                "FINAL_PRICING details are malformed: " + "; ".join(rejections),
                "LCP-100",
            )

    @staticmethod
    def _validate_contact_report(payload: dict[str, Any]) -> None:
        """LEP-0003 §4 ingest MUSTs (the report rule cannot be drafted in the
        open ``event.details`` object, so it is enforced at ingest).

        "Receivers MUST reject ``CONTACTED`` reports whose
        ``details.contacted_hashes`` contains an entry failing
        ``^[a-f0-9]{64}$`` or exceeds **32 entries per report**, and MUST
        treat oversized or malformed reports as a validation rejection, not
        a store." ``[]`` asserts nothing and "MUST NOT be sent as an event
        detail" (§4 report-direction absent-vs-empty rule); absent = nothing
        to report and is not an error. Beyond shape and count the entries are
        authenticated-sender, per-lead attestations - the platform cannot
        verify a digest without the plaintext, and does not try.
        """
        details = payload.get("details")
        if not isinstance(details, dict) or "contacted_hashes" not in details:
            return
        hashes = details["contacted_hashes"]
        if (
            not isinstance(hashes, list)
            or not hashes
            or len(hashes) > _CONTACTED_REPORT_LIMIT
            or not all(
                isinstance(entry, str) and _CONTACTED_HASH_RE.match(entry)
                for entry in hashes
            )
        ):
            raise RequestError(
                "CONTACTED report details.contacted_hashes must be a list of "
                "1 to 32 lowercase 64-hex per-pair digests",
                "LCP-100",
            )

    def _record_contacted_attestations(
        self, payload: dict[str, Any], *, lead_id: str, sender_id: str
    ) -> None:
        """Merge a validated CONTACTED report into the lead's contacted SET.

        At-least-once semantics (LEP-0003 §4): duplicate reports of the same
        digests merge by set union and cannot grow the set; the accumulation
        is a set, not a log, exactly like the wire array's "duplicates are
        tolerated but meaningless" rule.
        """
        details = payload.get("details")
        if not isinstance(details, dict):
            return
        hashes = details.get("contacted_hashes")
        if not isinstance(hashes, list) or not hashes:
            return
        self.store.record_contacted_digests(
            lead_id,
            [str(entry) for entry in hashes],
            reported_by=sender_id,
            reported_at=str(payload.get("timestamp") or now_iso()),
        )

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
            raise RequestError(
                "Sender is not authorized for this lead event", "LCP-002", 401
            )

        existing_event = self.store.get_event(message["id"])
        if existing_event:
            try:
                same_event = (
                    existing_event["lead_id"] == payload["lead_id"]
                    and existing_event["event_name"] == event_name
                    and self.store.decode_envelope(existing_event["envelope_json"])
                    == envelope
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

        if event_name == "CONTACTED":
            self._validate_contact_report(payload)
        # SPEC §4 class rule, every event name and both directions (#198).
        # Runs after the CONTACTED report rule so that report keeps its own
        # published rejection semantics.
        self._validate_event_details_pii(payload)
        if event_name == "FINAL_PRICING":
            self._validate_final_pricing_details(payload)

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
            inserted_event = self.store.insert_event(
                payload["lead_id"],
                event_name,
                envelope,
                record_stream=self.config.feature_events,
            )
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
        elif event_name == "CONTACTED":
            self._record_contacted_attestations(
                payload, lead_id=payload["lead_id"], sender_id=sender_id
            )

        details = payload.get("details", {})
        offer_id = details.get("offer_id") if isinstance(details, dict) else None
        post_delivery = next(
            (
                delivery
                for delivery in deliveries
                if delivery["kind"] == "post"
                and (not offer_id or delivery["offer_id"] == offer_id)
                and (
                    is_admin
                    or sender_id == delivery["buyer_id"]
                    or sender_id == row["sender_id"]
                )
            ),
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
                    status, reason = (
                        event_name.lower(),
                        f"lifecycle_{event_name.lower()}",
                    )
                elif event_name in {"CALL_OUTCOME", "CALL_ENDED", "CALL_CONNECTED"}:
                    status, reason, seconds = self._evaluate_call_outcome(
                        offer, details
                    )
                elif event_name in {"DELIVERED", "ACCEPTED"}:
                    status, reason = "payable", "buyer_acceptance"
                if status:
                    post_payload = post["lcp"]["payload"]
                    self.store.record_payable(
                        offer_id=offer["offer_id"],
                        lead_id=payload["lead_id"],
                        buyer_id=offer["buyer_id"],
                        month_key=self._month_key(offer),
                        channel=post_payload.get(
                            "channel", "call" if "call" in post_payload else "lead"
                        ),
                        status=status,
                        price_cents=int(post_payload.get("price_cents", 0)),
                        currency=str(
                            post_payload.get("currency", offer.get("currency", ""))
                        ),
                        reason=reason,
                        call_seconds=seconds,
                    )
        return build_ack(
            envelope,
            sender_id=self.config.platform_id,
            status="RECEIVED",
            lead_id=payload["lead_id"],
        )

    # ─── Event subscriptions (LEP-0001 W3) ────────────────────────────────

    def _require_events(self) -> None:
        if not self.config.feature_events:
            # Same switch discipline as batch/negotiation: off means the
            # route does not exist, not a per-request rejection.
            raise RequestError("Endpoint not found", "LCP-001", 404)

    def authenticate_read(self, headers: dict[str, str], *, required_scope: str) -> str:
        """Authenticate a read surface (no body, no mutating idempotency key)."""
        return self.auth.authenticate(
            sender_id=header(headers, "X-LCP-Sender-Id"),
            headers=headers,
            body=b"",
            mutating=False,
            required_scope=required_scope,
        )

    @staticmethod
    def _subscription_error(exc: SubscriptionValidationError) -> RequestError:
        details: dict[str, Any] = {"reason": exc.reason}
        if exc.field:
            details["field"] = exc.field
        if exc.reason == "receiver_mismatch":
            return RequestError(str(exc), "LCP-002", 401, details=details)
        if exc.reason == "schema_version_unsupported":
            return RequestError(str(exc), "LCP-007", 422, details=details)
        if exc.reason == "unknown_message_type":
            return RequestError(str(exc), "LCP-006", 400, details=details)
        return RequestError(str(exc), "LCP-100", 400, details=details)

    def subscribe_events(
        self,
        envelope: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> dict[str, Any]:
        """LEP-0001 W3-01: register a tenant-scoped event subscription.

        Registration is authenticated like any mutating message; the
        subscription is bound to the authenticated sender's tenant. The
        per-subscription secret is returned exactly once, here, and never in
        a list/read response.
        """
        self._require_events()
        headers = headers or {}
        try:
            message, normalized = validate_subscription_envelope(
                envelope,
                platform_id=self.config.platform_id,
                advertised_events=set(self._advertised_event_names()),
                allow_private=self.config.allow_insecure_webhooks,
            )
        except SubscriptionValidationError as exc:
            raise self._subscription_error(exc) from exc
        # The named allowlist/filter refusals above run first so an attempted
        # consumer predicate is never reported as a generic schema failure;
        # the ordinary envelope/schema validator then pins the rest of the
        # §2 shape (UUID id, timestamp, closed payload keys).
        self._validate_envelope(envelope)
        self._validate_transport(message, headers)
        sender_id = self.auth.authenticate(
            sender_id=message["sender_id"],
            headers=headers,
            body=raw_body if raw_body is not None else self._body(envelope),
            idempotency_key=message["idempotency_key"],
            mutating=True,
            required_scope="event:subscribe",
        )
        if sender_id != message["sender_id"]:
            raise RequestError(
                "Authenticated sender does not match message sender", "LCP-002", 401
            )
        tenant_id = self.auth.tenant_for(sender_id) or self.config.routing_tenant_id
        payload = envelope["lcp"]["payload"]
        replay_from = payload.get("replay_from")
        if replay_from is not None:
            if not isinstance(replay_from, str) or not replay_from:
                raise RequestError(
                    "replay_from must be an event cursor",
                    "LCP-100",
                    400,
                    details={"field": "replay_from", "reason": "cursor_invalid"},
                )
            cursor_sequence = self._resolve_event_cursor(replay_from, tenant_id)
        else:
            # A fresh subscription starts at the registration boundary: only
            # events with a later sequence are delivered.
            cursor_sequence = self.store.latest_event_stream_sequence(tenant_id)

        existing = self.store.find_event_subscription_by_idempotency(
            sender_id, message["idempotency_key"]
        )
        if existing is not None:
            same = (
                json.loads(existing["event_types_json"]) == normalized["event_types"]
                and existing["callback_url"] == normalized["callback_url"]
                and json.loads(existing["filter_json"]) == normalized["filter"]
                and existing["replay_from"] == replay_from
            )
            if not same:
                raise RequestError(
                    "Idempotency key was reused with different subscription content",
                    "LCP-005",
                    409,
                )
            return self._subscription_view(existing)

        secret = token_urlsafe(32)
        subscription_id = f"sub_{uuid4().hex}"
        row = self.store.insert_event_subscription(
            subscription_id=subscription_id,
            tenant_id=tenant_id,
            subscriber_id=sender_id,
            idempotency_key=message["idempotency_key"],
            event_types=normalized["event_types"],
            callback_url=normalized["callback_url"],
            filter_values=normalized["filter"],
            secret=secret,
            replay_from=replay_from if isinstance(replay_from, str) else None,
            cursor_sequence=cursor_sequence,
        )
        self.store.insert_audit(
            tenant_id=tenant_id,
            actor_id=sender_id,
            action="subscription.created",
            resource_type="event_subscription",
            resource_id=subscription_id,
            metadata={
                "event_types": normalized["event_types"],
                "filter_keys": sorted(normalized["filter"]),
            },
        )
        return self._subscription_view(row, secret=secret)

    def list_event_subscriptions(
        self, *, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self._require_events()
        sender_id = self.authenticate_read(
            headers or {}, required_scope="event:subscribe"
        )
        tenant_id = self.auth.tenant_for(sender_id) or self.config.routing_tenant_id
        rows = self.store.list_event_subscriptions(tenant_id)
        return {"subscriptions": [self._subscription_view(row) for row in rows]}

    def disable_event_subscription(
        self, subscription_id: str, *, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        self._require_events()
        sender_id = self.authenticate_read(
            headers or {}, required_scope="event:subscribe"
        )
        tenant_id = self.auth.tenant_for(sender_id) or self.config.routing_tenant_id
        row = self.store.get_event_subscription(subscription_id)
        if row is None:
            raise RequestError("Subscription not found", "LCP-003", 404)
        if row["tenant_id"] != tenant_id:
            raise RequestError("Subscription belongs to another tenant", "LCP-002", 403)
        self.store.deactivate_event_subscription(subscription_id, tenant_id=tenant_id)
        self.store.insert_audit(
            tenant_id=tenant_id,
            actor_id=sender_id,
            action="subscription.disabled",
            resource_type="event_subscription",
            resource_id=subscription_id,
        )
        updated = self.store.get_event_subscription(subscription_id) or row
        return self._subscription_view(updated)

    def events_page(
        self, query: str, *, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """LEP-0001 W3-03: tenant-scoped read/replay from a cursor."""
        self._require_events()
        sender_id = self.authenticate_read(headers or {}, required_scope="event:read")
        tenant_id = self.auth.tenant_for(sender_id) or self.config.routing_tenant_id
        params = parse_qs(query)
        supplied = params.get("cursor", [None])[0]
        limit = self._event_page_limit(params)
        after_sequence = (
            0 if supplied is None else self._resolve_event_cursor(supplied, tenant_id)
        )
        rows = self.store.list_event_stream(
            tenant_id=tenant_id,
            after_sequence=after_sequence,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = [self.store.decode_envelope(row["envelope_json"]) for row in rows]
        next_cursor = encode_event_cursor(int(rows[-1]["sequence"])) if rows else None
        return {
            "events": events,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    @staticmethod
    def _event_page_limit(params: dict[str, list[str]]) -> int:
        raw = params.get("limit", ["100"])[0]
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            raise RequestError(
                "limit must be an integer",
                "LCP-100",
                400,
                details={"field": "limit", "reason": "limit_invalid"},
            ) from None
        if not 1 <= limit <= 500:
            raise RequestError(
                "limit must be between 1 and 500",
                "LCP-100",
                400,
                details={"field": "limit", "reason": "limit_invalid"},
            )
        return limit

    def _resolve_event_cursor(self, cursor: str, tenant_id: str) -> int:
        """Decode a cursor; expired and unknown cursors are explicit errors.

        Sequences are allocated per tenant, so a cursor always reads the
        caller's own stream and cannot name another tenant's event.
        """
        try:
            sequence = decode_event_cursor(cursor)
        except EventCursorError as exc:
            raise RequestError(
                str(exc),
                "LCP-100",
                400,
                details={"field": "cursor", "reason": exc.reason},
            ) from exc
        row = self.store.get_event_stream_event_by_sequence(tenant_id, sequence)
        if row is None:
            oldest = self.store.oldest_event_stream_sequence(tenant_id)
            if oldest is None or sequence < oldest:
                raise RequestError(
                    "Event cursor has expired; replay from a newer cursor",
                    "LCP-100",
                    400,
                    details={"field": "cursor", "reason": "cursor_expired"},
                )
            raise RequestError(
                "Event cursor does not name a stream event",
                "LCP-100",
                400,
                details={"field": "cursor", "reason": "cursor_unknown"},
            )
        event_at = parse_iso_datetime(row["created_at"])
        if event_at is None:
            raise RequestError(
                "Event cursor does not name a stream event",
                "LCP-100",
                400,
                details={"field": "cursor", "reason": "cursor_unknown"},
            )
        age = (datetime.now(timezone.utc) - event_at).total_seconds()
        if age > self.config.event_cursor_ttl_seconds:
            raise RequestError(
                "Event cursor has expired; replay from a newer cursor",
                "LCP-100",
                400,
                details={"field": "cursor", "reason": "cursor_expired"},
            )
        return sequence

    @staticmethod
    def _subscription_view(
        row: dict[str, Any], *, secret: str | None = None
    ) -> dict[str, Any]:
        view: dict[str, Any] = {
            "subscription_id": row["subscription_id"],
            "event_types": json.loads(row["event_types_json"]),
            "callback_url": row["callback_url"],
            "filter": json.loads(row["filter_json"]),
            "active": bool(row["active"]),
            "replay_from": row["replay_from"],
            "created_at": row["created_at"],
        }
        if secret is not None:
            view["secret"] = secret
        return view

    def _process_event_subscriptions(self) -> None:
        """Background path: fan out new events, then deliver due attempts.

        Never called from a request handler that produced an event — delivery
        is a worker concern, so intake latency is independent of subscriber
        responsiveness.
        """
        if not self.config.feature_events:
            return
        cutoff = format_iso_datetime(
            datetime.now(timezone.utc)
            - timedelta(seconds=self.config.event_cursor_ttl_seconds)
        )
        self.store.purge_event_stream(cutoff)
        for subscription in self.store.list_event_subscriptions(active_only=True):
            self._fanout_event_subscription(subscription)
        for delivery in self.store.claim_due_event_deliveries(self.worker_id):
            self._deliver_event_subscription(delivery)

    def _fanout_event_subscription(self, subscription: dict[str, Any]) -> None:
        event_types = set(json.loads(subscription["event_types_json"]))
        filters = json.loads(subscription["filter_json"])
        # ponytail: one stream page per subscription per worker tick. A deep
        # backlog drains at the worker cadence instead of holding the tick;
        # raise the page size or fan out concurrently if delivery lag matters.
        rows = self.store.list_event_stream(
            tenant_id=str(subscription["tenant_id"]),
            after_sequence=int(subscription["cursor_sequence"]),
            limit=200,
        )
        if not rows:
            return
        for row in rows:
            if row["event_type"] not in event_types:
                continue
            if not event_matches_filters(
                event_type=str(row["event_type"]),
                message_type=row["message_type"],
                vertical=row["vertical"],
                status=row["status"],
                lead_id=row["lead_id"],
                filters=filters,
            ):
                continue
            self.store.insert_event_delivery(
                subscription_id=str(subscription["subscription_id"]),
                event_id=str(row["event_id"]),
                event_type=str(row["event_type"]),
            )
        self.store.update_event_subscription_cursor(
            str(subscription["subscription_id"]),
            cursor_sequence=int(rows[-1]["sequence"]),
        )

    def _deliver_event_subscription(self, delivery: Any) -> None:
        """Sign and POST one event delivery, with retry/DLQ bookkeeping."""
        attempts = int(delivery["attempts"]) + 1
        subscription = self.store.get_event_subscription(
            str(delivery["subscription_id"])
        )
        event = self.store.get_event_stream_event(str(delivery["event_id"]))
        # Authorization is re-checked on every delivery, not only at
        # registration: an inactive subscription and a tenant mismatch both
        # stop here.
        if (
            subscription is None
            or not subscription["active"]
            or event is None
            or subscription["tenant_id"] != event["tenant_id"]
        ):
            error = "Subscription is inactive or no longer authorized for this event"
            self.store.mark_event_delivery(
                str(delivery["delivery_id"]),
                status="FAILED",
                attempts=attempts,
                next_attempt_at=now_iso(),
                last_error=error,
                worker_id=self.worker_id,
            )
            self.store.record_dead_letter(
                queue_type="event_subscription",
                resource_id=str(delivery["delivery_id"]),
                lead_id=str(event["lead_id"]) if event is not None else None,
                attempts=attempts,
                last_error=error,
            )
            return
        secret = self.store.event_subscription_secret(
            str(subscription["subscription_id"])
        )
        if not secret:
            error = "Subscription has no active HMAC secret"
            self.store.mark_event_delivery(
                str(delivery["delivery_id"]),
                status="FAILED",
                attempts=attempts,
                next_attempt_at=now_iso(),
                last_error=error,
                worker_id=self.worker_id,
            )
            self.store.record_dead_letter(
                queue_type="event_subscription",
                resource_id=str(delivery["delivery_id"]),
                lead_id=str(event["lead_id"]),
                attempts=attempts,
                last_error=error,
            )
            return
        try:
            validate_webhook_url(str(subscription["callback_url"]), self.config)
            validate_egress_host(
                urlsplit(str(subscription["callback_url"])).hostname or "",
                self.config,
            )
            envelope = self.store.decode_envelope(str(event["envelope_json"]))
            body = json.dumps(
                envelope, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            timestamp = now_iso()
            event_id = str(event["event_id"])
            # LEP-0002 canonical input over the exact bytes above:
            # <timestamp>\n<event_id>\n<body>. The timestamp signed is the one
            # emitted on X-LCP-Timestamp, never a normalized value.
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-LCP-Sender-Id": self.config.platform_id,
                "X-LCP-Event-Id": event_id,
                "X-LCP-Subscription-Id": str(subscription["subscription_id"]),
                "X-LCP-Timestamp": timestamp,
                "X-LCP-Idempotency-Key": event_id,
                "X-LCP-Signature": signature_for(secret, timestamp, event_id, body),
            }
            if envelope["lcp"]["message"].get("test"):
                headers["X-LCP-Test"] = "true"
            with httpx.Client(
                timeout=self.config.webhook_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    str(subscription["callback_url"]),
                    content=body,
                    headers=headers,
                ) as response:
                    response_bytes = 0
                    for chunk in response.iter_bytes():
                        response_bytes += len(chunk)
                        if response_bytes > self.config.max_webhook_response_bytes:
                            raise SecurityPolicyError(
                                "Webhook response exceeded the configured limit"
                            )
                    response_status = response.status_code
            if response_status == 409 or 200 <= response_status < 300:
                self.store.mark_event_delivery(
                    str(delivery["delivery_id"]),
                    status="DELIVERED",
                    attempts=attempts,
                    next_attempt_at=now_iso(),
                    worker_id=self.worker_id,
                )
                return
            # Never persist arbitrary subscriber response bodies: they may
            # contain PII and are attacker-controlled.
            error = f"HTTP {response_status} from subscriber callback"
        except Exception as exc:  # delivery failures must enter the retry queue
            error = f"{type(exc).__name__}: {exc}"

        if attempts >= self.config.max_delivery_attempts:
            status = "FAILED"
            next_attempt = now_iso()
        else:
            status = "RETRY"
            delay = self._retry_delays[min(attempts - 1, len(self._retry_delays) - 1)]
            next_attempt = format_iso_datetime(
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            )
        self.store.mark_event_delivery(
            str(delivery["delivery_id"]),
            status=status,
            attempts=attempts,
            next_attempt_at=next_attempt,
            last_error=error,
            worker_id=self.worker_id,
        )
        if status == "FAILED":
            self.store.record_dead_letter(
                queue_type="event_subscription",
                resource_id=str(delivery["delivery_id"]),
                lead_id=str(event["lead_id"]),
                attempts=attempts,
                last_error=error,
            )

    def upload_attachment(
        self, *, headers: dict[str, str], body: bytes
    ) -> dict[str, Any]:
        self._validate_test_header(headers)
        owner_id = header(headers, "X-LCP-Sender-Id")
        idempotency_key = header(headers, "X-LCP-Idempotency-Key")
        lead_id = header(headers, "X-LCP-Lead-Id")
        attachment_id = header(headers, "X-LCP-Attachment-Id") or f"att_{uuid4().hex}"
        purpose = header(headers, "X-LCP-Attachment-Purpose") or "supporting_document"
        filename = header(headers, "X-LCP-Filename") or "attachment.bin"
        content_type = (
            (header(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
        )
        digest = (header(headers, "X-LCP-Content-SHA256") or "").lower()
        expires_header = header(headers, "X-LCP-Attachment-Expires-At")
        expires_value = parse_iso_datetime(expires_header) if expires_header else None
        if expires_header and expires_value is None:
            raise RequestError(
                "Attachment expiry must be an RFC 3339 date-time", "LCP-100"
            )
        if expires_value is not None and expires_value <= datetime.now(timezone.utc):
            raise RequestError("Attachment expiry must be in the future", "LCP-100")
        if not owner_id or not idempotency_key or not lead_id or not digest:
            raise RequestError(
                "Attachment identity, lead, idempotency, and content hash headers are required",
                "LCP-003",
            )
        self.auth.authenticate(
            sender_id=owner_id,
            headers=headers,
            body=body,
            idempotency_key=idempotency_key,
            mutating=True,
            required_scope="attachment:write",
        )
        if len(body) == 0 or len(body) > self.config.max_attachment_bytes:
            raise RequestError(
                "Attachment size is outside the configured limit", "LCP-001", 413
            )
        if content_type not in self.config.allowed_attachment_content_types:
            raise RequestError("Attachment content type is not allowed", "LCP-100", 415)
        try:
            FileAttachmentStore.validate_metadata(
                attachment_id=attachment_id,
                filename=filename,
                content_type=content_type,
                size_bytes=len(body),
            )
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        if (
            not re.fullmatch(r"[a-f0-9]{64}", digest)
            or hashlib.sha256(body).hexdigest() != digest
        ):
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
            validate_residency(value)
            for value in self.config.attachment_allowed_residencies
        }:
            raise RequestError(
                "Attachment residency is not permitted by this deployment", "LCP-100"
            )
        try:
            scan = self.attachment_scanner.scan(
                body,
                filename=filename,
                content_type=content_type,
            )
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-100") from exc
        if self.config.attachment_scan_required and scan.status != "clean":
            raise RequestError(
                "Attachment did not pass the required malware scan", "LCP-100"
            )
        existing = self.store.get_attachment_by_idempotency(owner_id, idempotency_key)
        if existing:
            if existing["sha256"] != digest or existing["lead_id"] != lead_id:
                raise RequestError(
                    "Attachment idempotency key was reused with different content",
                    "LCP-005",
                    409,
                )
            return self._attachment_metadata(existing)
        existing_id = self.store.get_attachment(attachment_id)
        if existing_id:
            if (
                existing_id["owner_id"] == owner_id
                and existing_id["sha256"] == digest
                and existing_id["lead_id"] == lead_id
            ):
                return self._attachment_metadata(existing_id)
            raise RequestError(
                "Attachment ID is already assigned to another file", "LCP-005", 409
            )
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
            "attachment_id": attachment_id,
            "lead_id": lead_id,
            "owner_id": owner_id,
            "idempotency_key": idempotency_key,
            "purpose": purpose,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(body),
            "sha256": digest,
            "storage_ref": storage_ref,
            "residency": residency,
            "scan_status": scan.status,
            "scan_engine": scan.engine,
            "scanned_at": scan.scanned_at,
            "encryption": self.attachments.encryption,
            "expires_at": format_iso_datetime(expires_value) if expires_value else None,
        }
        if not self.store.insert_attachment(metadata):
            existing = self.store.get_attachment_by_idempotency(
                owner_id, idempotency_key
            )
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
            metadata={
                "lead_id": lead_id,
                "purpose": purpose,
                "size_bytes": len(body),
                "sha256": digest,
            },
        )
        row = self.store.get_attachment(attachment_id)
        assert row is not None
        return self._attachment_metadata(row)

    @staticmethod
    def _attachment_metadata(row: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "attachment_id": row["attachment_id"],
            "purpose": row["purpose"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "storage_ref": row["storage_ref"],
            "created_at": row["created_at"],
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

    def download_attachment(
        self, attachment_id: str, *, headers: dict[str, str]
    ) -> tuple[bytes, dict[str, str]]:
        row = self.store.get_attachment(attachment_id)
        if not row or row["status"] != "AVAILABLE":
            raise RequestError("Attachment not found", "LCP-003", 404)
        if is_expired(parse_iso_datetime(row["expires_at"])):
            self.store.expire_attachment(attachment_id)
            self._process_attachment_deletions()
            raise RequestError("Attachment not found", "LCP-003", 404)
        sender_id = self.auth.authenticate(
            sender_id=header(headers, "X-LCP-Sender-Id"),
            headers=headers,
            body=b"",
            mutating=False,
        )
        authorized = (
            sender_id == row["owner_id"]
            or self.auth.has_scope(sender_id, "platform:admin")
            or self.store.has_delivered_post_for_buyer(row["lead_id"], sender_id)
        )
        if not authorized:
            self.store.insert_audit(
                tenant_id=self.auth.tenant_for(row["owner_id"])
                or self.config.routing_tenant_id,
                actor_id=sender_id,
                action="attachment.download_denied",
                resource_type="attachment",
                resource_id=attachment_id,
                metadata={"lead_id": row["lead_id"]},
            )
            raise RequestError("Attachment not found", "LCP-003", 404)
        self.store.insert_audit(
            tenant_id=self.auth.tenant_for(row["owner_id"])
            or self.config.routing_tenant_id,
            actor_id=sender_id,
            action="attachment.downloaded",
            resource_type="attachment",
            resource_id=attachment_id,
            metadata={"lead_id": row["lead_id"]},
        )
        try:
            content = self.attachments.read(
                row["storage_ref"], expected_sha256=row["sha256"]
            )
        except AttachmentError as exc:
            raise RequestError(str(exc), "LCP-500", 500) from exc
        return content, {
            "Content-Type": row["content_type"],
            "Content-Disposition": f'attachment; filename="{row["filename"]}"',
        }

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

    def lead_status(
        self, lead_id: str, requester_id: str | None = None
    ) -> dict[str, Any]:
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
            decisions = [
                decision
                for decision in decisions
                if decision["buyer_id"] == requester_id
            ]
            payables = [
                payable for payable in payables if payable["buyer_id"] == requester_id
            ]
            filtered_events = []
            for event in events:
                payload = event["lcp"].get("payload", {})
                details = payload.get("details", {})
                sender = event["lcp"].get("message", {}).get("sender_id")
                if (
                    sender == requester_id
                    or details.get("offer_id") in winning_offer_ids
                ):
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
            "attachments": [
                self._attachment_metadata(attachment) for attachment in attachments
            ],
        }
        if row["expires_at"] is not None:
            result["expires_at"] = row["expires_at"]
        if row["consent_expires_at"] is not None:
            result["consent_expires_at"] = row["consent_expires_at"]
        return result

    @staticmethod
    def _advertised_event_names() -> list[str]:
        """The single event vocabulary this receiver advertises (LEP-0001 W3-01).

        Registration and filter validation read this list, so what the
        capabilities document advertises and what a subscription may name can
        never drift.
        """
        return [
            "DELIVERED",
            "ACCEPTED",
            "REJECTED",
            "DISPUTED",
            "REFUNDED",
            "EXPIRED",
            "CONVERTED",
            "ARCHIVED",
            "CALL_OFFERED",
            "CALL_CONNECTED",
            "CALL_ENDED",
            "CALL_OUTCOME",
            "CONTACTED",
            # LEP-0006 Track A: publisher-reported final pricing (external or
            # in-auction sale outcome). Analytics and bid refinement only —
            # never a payable or lifecycle signal.
            "FINAL_PRICING",
        ]

    def capabilities(self) -> dict[str, Any]:
        return {
            # LEP-0003 Compatibility (F6): every element is a FULL
            # major.minor.patch semver; peers compare parsed (major == 1 and
            # minor >= 1), never string literals or prefixes. 1.1.0
            # advertises the slot/contacted surface and CONTACTED reporting.
            # This advertisement and the intake LCP-007 negotiation read the
            # same constant (validation.SUPPORTED_LCP_VERSIONS) — issue #181.
            "lcp_versions": list(SUPPORTED_LCP_VERSIONS),
            # D3/v1.2: the core schema version this receiver validates
            # against, resolved from the package SDK_VERSION. SPEC §2 states
            # lcp.version IS the core schema version for this major line, so
            # this is an advertisement, not a new envelope field.
            "core_schema_version": _read_sdk_version(),
            # D2/v1.2: absent an offer-level exclusivity hint, a lead is
            # shared (the v1.0 default made explicit).
            "exclusivity_default": "shared",
            # v1.2 additive surfaces actually implemented by this build.
            # Add a key only when the behaviour is enabled here.
            "features": {
                "processed_ack": "1.2.0",
                "multi_currency": "1.2.0",
                # LEP-0001 compensating constraint 1: the surface is advertised
                # only when this deployment enabled it, so a peer that does not
                # read the advertisement never receives batch unsolicited.
                **({"batch": "1.2.0"} if self.config.feature_batch else {}),
                # LEP-0009 C2/C3: advertised only when this deployment enabled
                # the negotiation surface. Absence is the switch; a peer that
                # sees no key MUST NOT emit quote/counter.
                **({"negotiation": "1.2.0"} if self.config.feature_negotiation else {}),
                # LEP-0001 W3: registration, signed at-least-once delivery,
                # and cursor replay, advertised only when enabled.
                **(
                    {"event_subscriptions": "1.2.0"}
                    if self.config.feature_events
                    else {}
                ),
                # LEP-0003 W6-07: platform-recorded email/form relay entries
                # are advertised only when this deployment enabled the surface
                # (flag AND at least one declared relay publisher), so a peer
                # never mistakes an absent key for a supported entry surface.
                **(
                    {"email_form_entry": "1.2.0"}
                    if self.config.feature_email_form_entry
                    and self.config.email_form_publisher_ids
                    else {}
                ),
            },
            "message_types": [
                "lead",
                "call",
                "ping",
                "bid",
                "post",
                "ack",
                "event",
                # LEP-0001 compensating constraint 1: a message type this
                # deployment has not enabled is not advertised.
                *(["batch"] if self.config.feature_batch else []),
                # LEP-0009 C3: same rule for the negotiation types.
                *(
                    list(NEGOTIATION_MESSAGE_TYPES)
                    if self.config.feature_negotiation
                    else []
                ),
                # LEP-0001 W3: the registration type is advertised only when
                # the subscription surface is enabled.
                *(["subscription"] if self.config.feature_events else []),
            ],
            "verticals": [
                {"id": path.stem, "schema_version": "1.0.0"}
                for path in (self.validator.vertical_root.glob("*.json"))
            ],
            "countries": [],
            "auth_methods": ["bearer", "hmac"],
            # LEP-0003 §4 (F5/T6): CONTACTED is reportable by a delivered,
            # scoped buyer and must be advertised (the §8 surface the
            # compatibility gate relies on).
            "events": self._advertised_event_names(),
            # SPEC §9: endpoints MAY advertise a non-default response
            # timeout; this is the value the post sweep enforces.
            "post_response_timeout_seconds": self.config.post_response_timeout_seconds,
            "attachments": {
                "upload": True,
                "authenticated_download": True,
                "max_bytes": self.config.max_attachment_bytes,
            },
            # LEP-0008 §9 advertisement honesty: L3 means agent attestation
            # *verification* (§7). With verification implemented, the level
            # follows the enforced mode — `observe` verifies and records but
            # accepts, so it is NOT L3 (Phase 0 defaulted here until the
            # implementation merged; issue #192 was the false-claim report).
            "conformance_level": (
                "L3" if self.config.attestation_mode == "enforce" else "L2"
            ),
        }

    def request_history(
        self,
        query: str,
        *,
        sender_id: str,
        headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """LEP-0005 read surface (accepted 2026-11-03).

        Thin by design: the tenant predicate, row projection, identifier pins,
        per-pair hashing and page cut all live in ``history.history_rows`` —
        the record's single decision point. This method must not add a second
        filter; see the module docstring.
        """
        from . import history

        return history.serve(self, query, sender_id=sender_id, headers=headers)

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
            path = (
                self.validator.vertical_root / f"{name.removeprefix('verticals/')}.json"
            )
        else:
            path = self.validator.schema_root / f"{name}.json"
        if not path.exists():
            raise RequestError("Schema not found", "LCP-007", 404)
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _body(envelope: dict[str, Any]) -> bytes:
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )

    def _validate_transport(
        self, message: dict[str, Any], headers: dict[str, str]
    ) -> None:
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
            raise RequestError(
                "Test messages must use a sandbox endpoint", "LCP-013", 400
            )
        if self.config.test_mode and not header_is_test:
            raise RequestError(
                "Sandbox requests require X-LCP-Test: true", "LCP-013", 400
            )

    def _validate_attachment_references(
        self, payload: dict[str, Any], owner_id: str
    ) -> None:
        for attachment in payload.get("attachments", []):
            attachment_id = attachment.get("attachment_id")
            row = (
                self.store.get_attachment(str(attachment_id)) if attachment_id else None
            )
            if not row or row["status"] != "AVAILABLE":
                raise RequestError("Referenced attachment is not available", "LCP-003")
            if row["owner_id"] != owner_id or row["lead_id"] != payload.get("lead_id"):
                raise RequestError(
                    "Referenced attachment is not owned by this publisher lead",
                    "LCP-002",
                    401,
                )
            for field in (
                "filename",
                "content_type",
                "size_bytes",
                "sha256",
                "storage_ref",
                "residency",
                "encryption",
            ):
                if field in attachment and str(attachment[field]) != str(row[field]):
                    raise RequestError(
                        "Referenced attachment metadata does not match stored content",
                        "LCP-100",
                    )

    def _validate_envelope(self, envelope: dict[str, Any]) -> None:
        # SPEC §5/§6: message.type is a closed allowlist and a value outside
        # it is LCP-006 UNKNOWN_MESSAGE_TYPE, not the generic validation
        # catch-all (issue #131). This structural check runs ahead of schema
        # validation and re-codes exactly that one axis; every other schema
        # violation keeps LCP-100.
        lcp = envelope.get("lcp") if isinstance(envelope, dict) else None
        message = lcp.get("message") if isinstance(lcp, dict) else None
        if (
            isinstance(message, dict)
            and "type" in message
            and message["type"] not in MESSAGE_TYPES
        ):
            raise RequestError(
                f"Unknown message type {message['type']!r}",
                "LCP-006",
                400,
                details=[
                    (
                        f"{message['type']!r} is not one of the closed "
                        f"message.type allowlist {list(MESSAGE_TYPES)} "
                        "at lcp/message/type"
                    )
                ],
            )
        # SPEC §5: "LCP-009 | INVALID_PHONE | 400" over §3
        # "phone | string | E.164 format." A non-E.164 value on a published
        # consumer phone axis is the §5 distinct code, not the LCP-100
        # catch-all (issue #176; the corpus already names the expectation:
        # test-vectors/l2-lifecycle.json L2-011 "lead_invalid_phone_format"
        # pins "error_code": "LCP-009"). Same documented ordering point as
        # the #131 re-code above — this runs ahead of schema validation
        # because the schema pattern would otherwise swallow the axis into
        # LCP-100. The schema pattern stays published and un-edited: type
        # violations (a non-string phone) and every other axis keep LCP-100.
        payload = lcp.get("payload") if isinstance(lcp, dict) else None
        consumer = payload.get("consumer") if isinstance(payload, dict) else None
        if isinstance(consumer, dict):
            for phone_field in ("phone", "secondary_phone"):
                value = consumer.get(phone_field)
                if isinstance(value, str) and not E164_PATTERN.fullmatch(value):
                    raise RequestError(
                        f"consumer.{phone_field} is not a valid E.164 phone number",
                        "LCP-009",
                        400,
                        details=[
                            f"{value!r} does not match the E.164 wire format "
                            f"(§3: 'E.164 format') at "
                            f"lcp/payload/consumer/{phone_field}"
                        ],
                    )
        try:
            self.validator.require_valid_envelope(envelope)
        except ValidationError as exc:
            raise RequestError(
                "LCP validation failed",
                "LCP-100",
                400,
                details=exc.errors,
            ) from exc
        # SPEC §5: "LCP-007 | SCHEMA_VERSION_UNSUPPORTED | 422" over §6
        # "Protocol semver (MAJOR breaking / MINOR additive / PATCH fixes)".
        # The thing the text names: the envelope's lcp.version — described in
        # schemas/envelope.json as "Protocol semver (e.g. 1.0.0)" and
        # identified with the core schema version by §13 ("document that
        # protocol version = core schema version"); a future major/minor was
        # silently ACKed and processed as v1 (issue #181). Runs after schema
        # validation so a MALFORMED version stays the structural LCP-100
        # axis; only well-formed semvers outside the supported set — the one
        # constant the §8 capabilities advertisement serves — negotiate away
        # as 422.
        version = envelope["lcp"]["version"]
        if not _lcp_version_supported(version):
            raise RequestError(
                f"Protocol version {version!r} is not supported by this endpoint",
                "LCP-007",
                422,
                details=[
                    f"{version!r} is not a supported protocol version; this "
                    f"endpoint advertises parsed-semver lcp_versions "
                    f"{list(SUPPORTED_LCP_VERSIONS)} at lcp/version (§8)"
                ],
            )
        # SPEC §5: "LCP-010 | INVALID_TERRITORY | 422" over §3 "country_code
        # (ISO 3166-1 alpha-2, REQUIRED)". Pattern-valid but ISO-UNASSIGNED
        # codes (e.g. "ZZ") previously ACKed and silently matched nothing
        # (issue #182); they reject here. RULING on casing: §3 mandates no
        # intake-side normalization, so the wire value is validated as-is —
        # lowercase "au" never reaches this axis, it is the schema
        # pattern's LCP-100 violation (messy sources normalize through the
        # mapping "upper" transform). Deployment territory (offer countries,
        # §8 "countries") stays a routing concern (matching.py), never an
        # intake gate.
        location = payload.get("location") if isinstance(payload, dict) else None
        if isinstance(location, dict):
            country_code = location.get("country_code")
            if isinstance(country_code, str) and country_code not in ISO_3166_1_ALPHA2:
                raise RequestError(
                    "location.country_code is not an assigned ISO 3166-1 alpha-2 code",
                    "LCP-010",
                    422,
                    details=[
                        f"{country_code!r} is not an assigned ISO 3166-1 "
                        "alpha-2 country code (§3) at "
                        "lcp/payload/location/country_code"
                    ],
                )


# sqlite3.Row is intentionally only used as a structural type in this module.
sqlite3_row = Any

"""JSON Schema validation for the reference platform."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


MESSAGE_TYPES = (
    "lead",
    "call",
    "ping",
    "post",
    "ack",
    "event",
    "bid",
    "batch",
    # LEP-0009 (v1.2): the negotiation wire types. They are schema-known
    # unconditionally so a deployment can validate/relay them without a
    # schema migration; LCP_FEATURE_NEGOTIATION gates intake, advertisement,
    # and token minting/redemption, not the schema set (rollout step 3).
    "quote",
    "counter",
    # LEP-0001 W3 (v1.2): the event-subscription registration type. Like
    # quote/counter it is schema-known unconditionally; LCP_FEATURE_EVENTS
    # gates intake, advertisement, and delivery.
    "subscription",
)

# The closed subset added by LEP-0009. Runtime gates must name these rather
# than comparing literals in more than one place.
NEGOTIATION_MESSAGE_TYPES = ("quote", "counter")

# §6/§8: the LCP protocol versions this platform supports and advertises.
# ONE source for the §8 capabilities advertisement and the intake version
# negotiation (§5 LCP-007 SCHEMA_VERSION_UNSUPPORTED ↔ 422, issue #181) so
# what the wire endpoint accepts can never drift from what it advertises.
# Every element is a full major.minor.patch semver (§8).
SUPPORTED_LCP_VERSIONS = ("1.0.0", "1.1.0", "1.2.0")

# §3 "phone | string | E.164 format." — mirrors the published
# schemas/core.json $defs/consumer pattern (^\+[1-9]\d{1,14}$) so the
# intake-side §5 re-code (LCP-009 INVALID_PHONE ↔ 400, issue #176) checks
# the same value axis the schema pins structurally.
E164_PATTERN = re.compile(r"\+[1-9]\d{1,14}")

# ISO 3166-1 alpha-2 officially assigned code elements (249; retrieved
# 2026-11-03 from the ISO 3166-1 alpha-2 code list). User-assigned
# elements (XK, EU, XX, ...) are deliberately excluded — §3 requires an
# "ISO 3166-1 alpha-2" country code. Backs the §5 LCP-010 INVALID_TERRITORY
# ↔ 422 intake check for pattern-valid-but-unassigned codes (issue #182).
_ISO_3166_1_ALPHA2_TEXT = """
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM
IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK
LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS
ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
"""
ISO_3166_1_ALPHA2 = frozenset(_ISO_3166_1_ALPHA2_TEXT.split())

# D7/v1.2 wire surface + v1.0/v1.1 registrations from
# governance/EXTENSION-REGISTRY.md. The value states whether the registration
# declares the namespace ping-safe. A ping carries `extensions` only for a
# registered, ping-safe namespace; every other key is refused by name.
# Unregistered namespaces stay legal on `lead`/`post` (forward compatibility),
# but never on a ping: the strict allowlist is the PII guard.
EXTENSION_REGISTRY: dict[str, bool] = {
    "lcp.platform.requirements": False,
    "lcp.platform.service_area": False,
    "lcp.marketplace.final_pricing": False,
    "lcp.form.questions": False,
    "lcp.outbound.call_request": False,
    "lcp.platform.assessment": True,
    "lcp.buyer.criteria": False,
    "lcp.intake.raw_form": False,
}


# --- LEP-0001 W3: event-subscription validation -----------------------------
#
# The filter is a CLOSED key set of core enums and identifiers. Consumer,
# provenance, attributes, details, contact, and security paths are refused by
# name: a filter must never become a predicate over PII (LEP-0001 W3-04).
EVENT_FILTER_KEYS = ("event_type", "message_type", "vertical", "status", "lead_id")
# Prefixes that must never appear as filter keys or nested paths. Refusing
# these first is what makes the refusal *named*: the error says which field.
_EVENT_FILTER_FORBIDDEN_PREFIXES = (
    "consumer",
    "provenance",
    "attributes",
    "details",
    "contact",
    "security",
)


class SubscriptionValidationError(ValueError):
    """A subscription field the registration path refuses by name."""

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        reason: str = "invalid_subscription",
    ):
        self.field = field
        self.reason = reason
        super().__init__(message)


def blocked_callback_address_reason(address: str) -> str | None:
    """Why ``address`` must not be contacted, or None when public.

    Same policy as the hosted conformance runner's target guard
    (``tools/conformance_runner.py:blocked_address_reason``): the two surfaces
    an attacker can point at infrastructure must agree.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return "not an IP address"
    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_link_local:
        return "link-local"
    if parsed.is_unspecified:
        return "unspecified"
    if parsed.is_multicast:
        return "multicast"
    if parsed.is_reserved:
        return "reserved"
    if parsed.is_private:
        return "private"
    return None


def validate_callback_url(url: Any, *, allow_private: bool = False) -> str:
    """Validate a subscription callback URL, then resolve it once.

    Mirrors the conformance runner's target rule: http/https only, no embedded
    credentials, a valid port, and every resolved address public unless the
    deployment runs in sandbox mode (``LCP_ALLOW_INSECURE_WEBHOOKS``). The
    destination is resolved once here; the delivery worker re-runs the same
    paired guard before every attempt.
    """
    if not isinstance(url, str) or not url.strip():
        raise SubscriptionValidationError(
            "callback_url must be a non-empty string",
            field="callback_url",
            reason="callback_url_invalid",
        )
    url = url.strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise SubscriptionValidationError(
            f"callback_url scheme {scheme or '(none)'!r} is not allowed; use http or https",
            field="callback_url",
            reason="callback_url_invalid",
        )
    if parts.username or parts.password:
        raise SubscriptionValidationError(
            "callback_url must not contain embedded credentials",
            field="callback_url",
            reason="callback_url_invalid",
        )
    host = parts.hostname
    if not host:
        raise SubscriptionValidationError(
            "callback_url must include a hostname",
            field="callback_url",
            reason="callback_url_invalid",
        )
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise SubscriptionValidationError(
            f"callback_url has an invalid port: {exc}",
            field="callback_url",
            reason="callback_url_invalid",
        ) from None
    if not 0 < port < 65_536:
        raise SubscriptionValidationError(
            "callback_url port is out of range",
            field="callback_url",
            reason="callback_url_invalid",
        )
    try:
        addresses: set[str] = {
            str(info[4][0])
            for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise SubscriptionValidationError(
            f"callback_url host could not be resolved: {exc}",
            field="callback_url",
            reason="callback_url_unresolvable",
        ) from None
    if not addresses:
        raise SubscriptionValidationError(
            "callback_url host did not resolve to any address",
            field="callback_url",
            reason="callback_url_unresolvable",
        )
    if not allow_private:
        for address in addresses:
            reason = blocked_callback_address_reason(address)
            if reason:
                raise SubscriptionValidationError(
                    f"callback_url resolves to a {reason} address; refusing it",
                    field="callback_url",
                    reason="callback_url_private_address",
                )
    return url


def validate_event_filter(
    value: Any,
    *,
    advertised_events: set[str] | frozenset[str],
) -> dict[str, list[str]]:
    """Normalize a subscription filter to ``{key: [values]}``.

    Keys outside ``EVENT_FILTER_KEYS`` are refused by name. A consumer/PII
    path is refused with ``filter_field_pii`` so a caller can distinguish an
    attempt to predicate on PII from a merely unknown key.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SubscriptionValidationError(
            "filter must be an object",
            field="filter",
            reason="filter_invalid",
        )
    normalized: dict[str, list[str]] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            raise SubscriptionValidationError(
                "filter keys must be strings",
                field="filter",
                reason="filter_invalid",
            )
        head = key.split(".", 1)[0]
        if key not in EVENT_FILTER_KEYS:
            pii = head in _EVENT_FILTER_FORBIDDEN_PREFIXES
            raise SubscriptionValidationError(
                f"filter field {key!r} is not allowed; the closed key set is "
                f"{list(EVENT_FILTER_KEYS)} and consumer/PII paths are never filters",
                field=key,
                reason="filter_field_pii" if pii else "filter_field_not_allowed",
            )
        values = raw if isinstance(raw, list) else [raw]
        if not values:
            raise SubscriptionValidationError(
                f"filter value for {key!r} must not be empty",
                field=key,
                reason="filter_invalid",
            )
        cleaned: list[str] = []
        for item in values:
            if not isinstance(item, str) or not item:
                raise SubscriptionValidationError(
                    f"filter value for {key!r} must be a non-empty string or array of strings",
                    field=key,
                    reason="filter_invalid",
                )
            if item == "*":
                raise SubscriptionValidationError(
                    f"filter value for {key!r} must not be a wildcard",
                    field=key,
                    reason="filter_invalid",
                )
            if key == "event_type" and item not in advertised_events:
                raise SubscriptionValidationError(
                    f"filter event_type {item!r} is not advertised in capabilities().events",
                    field=key,
                    reason="unadvertised_event_type",
                )
            if item not in cleaned:
                cleaned.append(item)
        normalized[key] = cleaned
    return normalized


def validate_subscription_payload(
    payload: Any,
    *,
    advertised_events: set[str] | frozenset[str],
    allow_private: bool = False,
) -> dict[str, Any]:
    """Validate and normalize the ``subscription`` payload.

    Registration is explicit: a non-empty ``event_types`` allowlist drawn from
    the receiver's advertisement, a callback URL under the runner's SSRF rule,
    and the closed ``filter`` key set. No wildcard scope exists by default.
    """
    if not isinstance(payload, dict):
        raise SubscriptionValidationError(
            "subscription payload must be an object",
            field="payload",
            reason="subscription_payload_invalid",
        )
    event_types = payload.get("event_types")
    if not isinstance(event_types, list) or not event_types:
        raise SubscriptionValidationError(
            "event_types must be a non-empty array",
            field="event_types",
            reason="event_types_required",
        )
    normalized_types: list[str] = []
    for event_type in event_types:
        if not isinstance(event_type, str) or not event_type:
            raise SubscriptionValidationError(
                "event_types entries must be non-empty strings",
                field="event_types",
                reason="event_types_invalid",
            )
        if event_type == "*":
            raise SubscriptionValidationError(
                "event_types must be explicit; a wildcard subscription is not allowed",
                field="event_types",
                reason="event_types_wildcard",
            )
        if event_type not in advertised_events:
            raise SubscriptionValidationError(
                f"event type {event_type!r} is not advertised in capabilities().events",
                field="event_types",
                reason="unadvertised_event_type",
            )
        if event_type not in normalized_types:
            normalized_types.append(event_type)
    callback_url = validate_callback_url(
        payload.get("callback_url"), allow_private=allow_private
    )
    return {
        "event_types": normalized_types,
        "callback_url": callback_url,
        "filter": validate_event_filter(
            payload.get("filter"), advertised_events=advertised_events
        ),
    }


def event_matches_filters(
    *,
    event_type: str,
    message_type: str | None,
    vertical: str | None,
    status: str | None,
    lead_id: str | None,
    filters: dict[str, list[str]],
) -> bool:
    """Apply a normalized filter to one event-stream row."""
    values = {
        "event_type": event_type,
        "message_type": message_type,
        "vertical": vertical,
        "status": status,
        "lead_id": lead_id,
    }
    return all(
        wanted is None or values[key] in wanted
        for key, wanted in ((key, filters.get(key)) for key in EVENT_FILTER_KEYS)
    )


def validate_subscription_envelope(
    envelope: Any,
    *,
    platform_id: str,
    advertised_events: set[str] | frozenset[str],
    allow_private: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the registration envelope without the payload schema.

    The envelope fields are the ordinary §2 shape; the payload is validated by
    ``validate_subscription_payload``. Returns ``(message, normalized_payload)``.
    """
    if not isinstance(envelope, dict):
        raise SubscriptionValidationError(
            "subscription request must be an object",
            field="lcp",
            reason="subscription_envelope_invalid",
        )
    lcp = envelope.get("lcp")
    if not isinstance(lcp, dict):
        raise SubscriptionValidationError(
            "lcp is required",
            field="lcp",
            reason="subscription_envelope_invalid",
        )
    version = lcp.get("version")
    if not isinstance(version, str) or version not in SUPPORTED_LCP_VERSIONS:
        raise SubscriptionValidationError(
            f"lcp.version {version!r} is not supported",
            field="lcp.version",
            reason="schema_version_unsupported",
        )
    message = lcp.get("message")
    if not isinstance(message, dict):
        raise SubscriptionValidationError(
            "lcp.message is required",
            field="lcp.message",
            reason="subscription_envelope_invalid",
        )
    if message.get("type") != "subscription":
        raise SubscriptionValidationError(
            "subscription registration requires message.type 'subscription'",
            field="lcp.message.type",
            reason="unknown_message_type",
        )
    for field in ("id", "timestamp", "sender_id", "receiver_id", "idempotency_key"):
        value = message.get(field)
        if not isinstance(value, str) or not value:
            raise SubscriptionValidationError(
                f"lcp.message.{field} is required",
                field=f"lcp.message.{field}",
                reason="subscription_envelope_invalid",
            )
    if message["receiver_id"] != platform_id:
        raise SubscriptionValidationError(
            "Message receiver is not this platform",
            field="lcp.message.receiver_id",
            reason="receiver_mismatch",
        )
    return message, validate_subscription_payload(
        lcp.get("payload"),
        advertised_events=advertised_events,
        allow_private=allow_private,
    )


# --- v1.2 D9 / LEP-0011: the `x-lcp-pii` sensitivity class ------------------
#
# docs/PII-CLASSES.md is normative for how a field is classed; these checks are
# the load-time half of D9. They read the annotation the schemas carry and
# refuse a schema set they cannot classify, so the operator learns at deploy
# time instead of after a disclosure.

# Least to most protective. The order IS the no-downgrade definition
# (docs/PII-CLASSES.md "No downgrade"), so it MUST NOT be reordered.
PII_CLASSES = ("public", "internal", "contact", "restricted", "prohibited")
PII_KEY = "x-lcp-pii"
# `computed` is not a class: it is legal only on envelope.json's `payload`,
# whose class is the payload schema selected by `message.type`.
PII_COMPUTED = "computed"
_CORE_REF_PREFIXES = ("core.json#", "https://lcp.dev/schemas/core.json#")


def default_schema_root() -> Path:
    configured = os.environ.get("LCP_SCHEMA_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "schemas"


class ValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _schema_of(validator: Draft202012Validator) -> dict[str, Any]:
    """The loaded document (a validator may hold a boolean schema)."""

    return cast(dict[str, Any], validator.schema)


def pii_rank(value: str) -> int:
    """Position on the class ladder; a lower rank is a downgrade."""

    return PII_CLASSES.index(value)


def pii_definitions(document: Any) -> list[tuple[str, Any]]:
    """Every property definition and ``$defs`` entry, with its dotted path.

    Paths mirror what docs/PII-CLASSES.md names a field by: ``properties`` is
    transparent (``consumer.phone``), a ``$defs`` entry appears as
    ``$defs.<name>...`` and array positions as ``[i]``.
    """

    found: list[tuple[str, Any]] = []

    def walk(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, path + (f"[{index}]",))
            return
        if not isinstance(node, dict):
            return
        definitions = node.get("$defs")
        if isinstance(definitions, dict):
            for name, definition in definitions.items():
                found.append((".".join(path + ("$defs", name)), definition))
                walk(definition, path + ("$defs", name))
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, definition in properties.items():
                found.append((".".join(path + (name,)), definition))
                walk(definition, path + (name,))
        for key, value in node.items():
            if key in {"properties", "$defs"}:
                continue
            walk(value, path + (key,))

    walk(document, ())
    return found


def _pii_shape_path(path: str) -> str:
    """A core definition's path without its ``$defs.`` root, so a vertical
    declaring ``consumer.phone`` is compared with core's ``$defs.consumer``."""

    return path.removeprefix("$defs.")


def core_pii_classes(core: dict[str, Any]) -> dict[str, str]:
    """The core floor: class per field, keyed by shape path."""

    floors: dict[str, str] = {}
    for path, definition in pii_definitions(core):
        if isinstance(definition, dict):
            declared = definition.get(PII_KEY)
            if isinstance(declared, str) and declared in PII_CLASSES:
                floors.setdefault(_pii_shape_path(path), declared)
    return floors


def _pii_source_words(kind: str, source: str) -> str:
    if kind == "vertical":
        return f"vertical '{Path(source).stem}'"
    if kind == "extension":
        return f"extension '{source}'"
    return f"schema '{Path(source).stem}'"


def pii_load_errors(
    documents: list[tuple[str, str, dict[str, Any]]],
) -> list[str]:
    """D9/LEP-0011 load-time refusals for the schemas about to be used.

    ``documents`` is ``(kind, source, document)`` with ``kind`` one of
    ``core``, ``message``, ``envelope``, ``offer``, ``vertical`` or
    ``extension``. A schema set that cannot be classified does not load:

    * an unannotated property (inherited ``$ref`` sites excepted — a ``$ref``
      takes its definition's class, so ``lead.consumer`` and ``post.consumer``
      cannot drift);
    * an unknown class, or ``computed`` anywhere but ``envelope.payload``;
    * a property classed ``prohibited`` — a field LCP will not carry may not
      be added, and the class exists so the coverage check can tell
      "deliberately refused" from "not yet classified";
    * a downgrade: a vertical or extension re-declaring a core field with a
      class lower than the core's (``LCP-100`` naming the field and both
      classes). Upgrades are silent — more protection never needs permission.
    * ``ping_safe: true`` on a ``contact``/``restricted`` attribute, which the
      flag cannot buy (docs/PII-CLASSES.md "No downgrade").
    """

    core = next((doc for kind, _source, doc in documents if kind == "core"), {})
    floors = core_pii_classes(core)
    errors: list[str] = []
    for kind, source, document in documents:
        for path, definition in pii_definitions(document):
            if not isinstance(definition, dict):
                errors.append(
                    f"{source}: {path} is a boolean schema with no x-lcp-pii "
                    "class; every schema property MUST be classed (D9/LEP-0011)"
                )
                continue
            if set(definition) == {"$ref"}:
                continue
            declared = definition.get(PII_KEY)
            shape = _pii_shape_path(path)
            if declared is None:
                errors.append(
                    f"{source}: {path} has no x-lcp-pii class; every schema "
                    "property MUST be classed (D9/LEP-0011)"
                )
                continue
            if declared == PII_COMPUTED:
                if not (kind == "envelope" and path == "lcp.payload"):
                    errors.append(
                        f"{source}: {path} declares x-lcp-pii 'computed'; that "
                        "value is legal only on envelope.json's payload"
                    )
                continue
            if declared not in PII_CLASSES:
                errors.append(
                    f"{source}: {path} declares unknown x-lcp-pii class "
                    f"{declared!r} (expected one of {', '.join(PII_CLASSES)})"
                )
                continue
            if declared == "prohibited":
                errors.append(
                    f"{source}: {path} declares x-lcp-pii prohibited; a field "
                    "LCP will not carry may not be added (D9/LEP-0011)"
                )
                continue
            if kind in {"vertical", "extension"}:
                floor = floors.get(shape)
                if floor is not None and pii_rank(declared) < pii_rank(floor):
                    errors.append(
                        f"LCP-100 VALIDATION_ERROR: x-lcp-pii downgrades core "
                        f"class for {shape}: core requires {floor}, "
                        f"{_pii_source_words(kind, source)} declares {declared} "
                        f"(field {shape}, class_required {floor}, "
                        f"class_declared {declared}, source {source})"
                    )
                if definition.get("ping_safe") is True and declared in {
                    "contact",
                    "restricted",
                }:
                    errors.append(
                        f"LCP-100 VALIDATION_ERROR: {source}: {path} is tagged "
                        f"ping_safe: true but classed {declared}; ping_safe: "
                        "true requires public or internal (or a declared "
                        "contact hash projection) (D9/LEP-0011)"
                    )
    return errors


def _pii_deref(reference: str, schema: dict[str, Any], core: dict[str, Any]) -> Any:
    for prefix in _CORE_REF_PREFIXES:
        if reference.startswith(prefix):
            return _pii_pointer(core, reference[len(prefix) :])
    if reference.startswith("#"):
        return _pii_pointer(schema, reference[1:])
    return None


def _pii_pointer(document: Any, pointer: str) -> Any:
    node = document
    for token in [part for part in pointer.split("/") if part]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node.get(token)
        else:
            return None
        if node is None:
            return None
    return node


def _pii_definition(
    definition: Any, schema: dict[str, Any], core: dict[str, Any]
) -> Any:
    """Follow ``$ref`` chains so a shared shape's class is read once."""

    for _depth in range(8):
        if not isinstance(definition, dict) or "$ref" not in definition:
            return definition
        definition = _pii_deref(definition["$ref"], schema, core)
    return None


def prohibited_value_errors(
    payload: Any,
    schema: dict[str, Any],
    core: dict[str, Any],
    path: str = "payload",
) -> list[str]:
    """D9: a value in a field classed ``prohibited`` is refused, not scrubbed.

    The refusal names the field so the sender can fix it; nothing is dropped or
    masked, so the audit fact that a prohibited value reached the wire survives
    and the message is not stored. The protocol fixes this behaviour, not a
    detector: on today's core no property is classed ``prohibited``, so this
    walk refuses exactly the fields a deployment overlay or a registered
    extension declared out of bounds.
    """

    errors: list[str] = []

    def walk(value: Any, definition: Any, where: str) -> None:
        resolved = _pii_definition(definition, schema, core)
        if not isinstance(value, dict) or not isinstance(resolved, dict):
            return
        properties = resolved.get("properties")
        if not isinstance(properties, dict):
            return
        for name, child in value.items():
            child_path = f"{where}.{name}"
            child_definition = _pii_definition(properties.get(name), schema, core)
            if (
                isinstance(child_definition, dict)
                and child_definition.get(PII_KEY) == "prohibited"
            ):
                if child is not None:
                    errors.append(
                        f"LCP-100 VALIDATION_ERROR: {child_path} carries a "
                        "value for a prohibited class; LCP will not carry it "
                        f"(field {child_path})"
                    )
                continue
            walk(child, child_definition, child_path)

    walk(payload, schema, path)
    return errors


class SchemaValidator:
    def __init__(self, schema_root: str | Path | None = None):
        self.schema_root = Path(schema_root) if schema_root else default_schema_root()
        self.vertical_root = self.schema_root.parent / "verticals"
        core = self._load("core")
        resource = Resource(contents=core, specification=DRAFT202012)
        registry = (
            Registry()
            .with_resource(uri="https://lcp.dev/schemas/core.json", resource=resource)
            .with_resource(uri="core.json", resource=resource)
        )
        format_checker = FormatChecker()
        self._core = core
        self._validators = {
            name: Draft202012Validator(
                self._load(name), registry=registry, format_checker=format_checker
            )
            for name in MESSAGE_TYPES
        }
        self._envelope = Draft202012Validator(
            self._load("envelope"), registry=registry, format_checker=format_checker
        )
        self._offer = Draft202012Validator(
            self._load("offer"), registry=registry, format_checker=format_checker
        )
        self._vertical_validators: dict[str, Draft202012Validator] = {}
        for path in sorted(self.vertical_root.glob("*.json")):
            with path.open(encoding="utf-8") as handle:
                self._vertical_validators[path.stem] = Draft202012Validator(
                    json.load(handle), format_checker=format_checker
                )
        # D9/LEP-0011: refuse to start on a schema set that cannot be
        # classified, before any message arrives. `require_pii_classes` keeps
        # the walk available to a deployment loading its own extension schema
        # documents (kind "extension"), where a downgrade is equally refused.
        documents: list[tuple[str, str, dict[str, Any]]] = [
            ("core", "schemas/core.json", core),
            ("envelope", "schemas/envelope.json", _schema_of(self._envelope)),
            ("offer", "schemas/offer.json", _schema_of(self._offer)),
        ]
        documents.extend(
            ("message", f"schemas/{name}.json", _schema_of(validator))
            for name, validator in sorted(self._validators.items())
        )
        documents.extend(
            ("vertical", f"verticals/{name}.json", _schema_of(validator))
            for name, validator in sorted(self._vertical_validators.items())
        )
        self.require_pii_classes(documents)

    def require_pii_classes(
        self, documents: list[tuple[str, str, dict[str, Any]]]
    ) -> None:
        """D9 load-time refusals; raises while the schema set cannot classify."""

        errors = pii_load_errors(documents)
        if errors:
            raise ValidationError(errors)

    def _load(self, name: str) -> dict[str, Any]:
        with (self.schema_root / f"{name}.json").open(encoding="utf-8") as handle:
            return json.load(handle)

    def payload_fields(self, message_type: str) -> frozenset[str]:
        """The strict allowlist a message type's payload schema declares.

        One reader for the schema, so a PII/allowlist probe can never drift
        from the normative document (LEP-0009 C4 enforcement).
        """
        validator = self._validators.get(message_type)
        if validator is None:
            return frozenset()
        document = _schema_of(validator)
        return frozenset(document.get("properties", {}))

    @staticmethod
    def _format(error: Any) -> str:
        path = "/".join(str(part) for part in error.path)
        return f"{error.message} at {path or '(root)'}"

    def validate_envelope(self, envelope: dict[str, Any]) -> list[str]:
        errors = [self._format(error) for error in self._envelope.iter_errors(envelope)]
        if errors:
            return errors
        message = envelope["lcp"]["message"]
        errors.extend(
            self._format(error)
            for error in self._validators[message["type"]].iter_errors(
                envelope["lcp"]["payload"]
            )
        )
        if message["type"] in {"lead", "call", "post", "ping"}:
            errors.extend(
                self._vertical_errors(
                    envelope["lcp"]["payload"],
                    message_type=message["type"],
                )
            )
        if message["type"] == "ping":
            errors.extend(self._ping_extension_errors(envelope["lcp"]["payload"]))
        if not errors:
            # D9: a prohibited-classed field carrying a value is refused, never
            # scrubbed — so the message is not stored and the sender is told
            # which field to fix.
            errors.extend(
                prohibited_value_errors(
                    envelope["lcp"]["payload"],
                    _schema_of(self._validators[message["type"]]),
                    self._core,
                )
            )
        return errors

    def require_valid_envelope(self, envelope: dict[str, Any]) -> None:
        errors = self.validate_envelope(envelope)
        if errors:
            raise ValidationError(errors)

    def require_valid_offer(self, offer: dict[str, Any]) -> None:
        errors = [self._format(error) for error in self._offer.iter_errors(offer)]
        if errors:
            raise ValidationError(errors)

    def _vertical_errors(
        self,
        payload: dict[str, Any],
        *,
        message_type: str,
    ) -> list[str]:
        raw_attributes = payload.get("attributes")
        attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
        vertical = (
            payload.get("vertical")
            if message_type == "ping"
            else attributes.get("vertical")
        )
        if not isinstance(vertical, str) or not vertical:
            return ["vertical is required for vertical validation"]
        validator = self._vertical_validators.get(vertical)
        if validator is None:
            return [f"vertical schema '{vertical}' was not found"]

        vertical_attributes = dict(attributes)
        if message_type == "ping":
            vertical_attributes.setdefault("vertical", vertical)
            version_schema = validator.schema.get("properties", {}).get(
                "schema_version", {}
            )
            vertical_attributes.setdefault(
                "schema_version", version_schema.get("const", "1.0.0")
            )
        errors = [
            self._format(error) for error in validator.iter_errors(vertical_attributes)
        ]
        if message_type == "ping":
            errors.extend(self._ping_safe_errors(payload, validator.schema))
        return errors

    def _ping_extension_errors(self, payload: dict[str, Any]) -> list[str]:
        """v1.2/D7: a ping admits only registered, ping-safe namespaces.

        The schema bounds the container shape; what a namespace *is* remains a
        receiver rule (LEP-0006 §B.1). Refuse by name so the sender can see
        which namespace failed admission — never silently drop the block.
        """
        extensions = payload.get("extensions")
        if not isinstance(extensions, dict) or not extensions:
            return []
        errors: list[str] = []
        for namespace in sorted(extensions):
            ping_safe = EXTENSION_REGISTRY.get(namespace)
            if ping_safe is None:
                errors.append(
                    f"extensions.{namespace} is not a registered extension "
                    "namespace; a ping admits only registered, ping-safe "
                    "namespaces (governance/EXTENSION-REGISTRY.md)"
                )
            elif not ping_safe:
                errors.append(
                    f"extensions.{namespace} is registered but not ping-safe; "
                    "a ping admits only registrations that declare ping safety"
                )
        return errors

    def _ping_safe_errors(
        self,
        payload: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> list[str]:
        vertical = payload.get("vertical")
        attributes = payload.get("attributes", {})
        if not isinstance(vertical, str) or not isinstance(attributes, dict):
            return []
        if schema is None:
            validator = self._vertical_validators.get(vertical)
            if validator is None:
                return [
                    f"vertical schema '{vertical}' not found for ping-safe validation"
                ]
            schema = validator.schema

        errors: list[str] = []

        def walk(value: Any, definition: dict[str, Any], path: str) -> None:
            if not isinstance(value, dict):
                return
            properties = definition.get("properties", {})
            for field_name, field_value in value.items():
                if path == "attributes" and field_name in {
                    "vertical",
                    "schema_version",
                }:
                    continue
                field_definition = properties.get(field_name)
                field_path = f"{path}.{field_name}"
                if (
                    not isinstance(field_definition, dict)
                    or field_definition.get("ping_safe") is not True
                ):
                    errors.append(
                        f"{field_path} is not tagged ping_safe: true in vertical '{vertical}'"
                    )
                    continue
                if isinstance(field_definition.get("properties"), dict):
                    walk(field_value, field_definition, field_path)

        walk(attributes, schema, "attributes")
        return errors


# --- LEP-0009: negotiation payloads are ping-class --------------------------
#
# The strict allowlist in schemas/quote.json and schemas/counter.json already
# refuses an unknown field. This helper names the refusal the way the record
# requires: an unknown field is LCP-100 naming its path, and a field whose name
# is consumer-shaped (or whose value is a contact digest) additionally carries
# LCP-008 PII_IN_PING, because the ping-class prohibition applies to both new
# types. No free text field exists on the surface, so no value is ever stored.

_CONSUMER_NAME_TOKENS = frozenset(
    {
        "consumer",
        "contact",
        "phone",
        "email",
        "name",
        "first",
        "last",
        "full",
        "address",
        "postal",
        "zip",
        "dob",
        "birth",
        "birthdate",
        "ssn",
        "social",
        "license",
        "passport",
        "note",
        "message",
        "comment",
        "description",
        "reference",
    }
)
_CONTACT_DIGEST_RE = re.compile(r"\A[a-f0-9]{48,64}\Z")


def negotiation_payload_pii_errors(
    payload: dict[str, Any], allowed_fields: set[str] | frozenset[str]
) -> list[str]:
    """Return LEP-0009 C4 refusals for a quote/counter payload, if any.

    The caller has already run the JSON Schema; this walk exists so the
    refusal names the PII axis (LCP-008) beside the allowlist axis (LCP-100)
    instead of collapsing both into one generic validation message.
    """
    problems: list[str] = []
    for key, value in payload.items():
        if key not in allowed_fields:
            problems.append(
                f"LCP-100: payload.{key} is outside the strict negotiation "
                "allowlist (additionalProperties: false)"
            )
            tokens = set(re.split(r"[^a-z]+", key.lower()))
            if tokens & _CONSUMER_NAME_TOKENS:
                problems.append(
                    f"LCP-008 PII_IN_PING: payload.{key} is consumer-shaped; "
                    "negotiation carries no consumer PII and no free text (LEP-0009 C4)"
                )
        if isinstance(value, str) and _CONTACT_DIGEST_RE.match(value):
            problems.append(
                f"LCP-008 PII_IN_PING: payload.{key} carries a contact digest; "
                "no consumer identifier is hashed into a negotiation (LEP-0009 C4)"
            )
    return problems

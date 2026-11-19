"""LEP-0015 buyer delivery mapping: post in, buyer request out; response in, outcome out.

A mapping (``schemas/buyer-mapping.json``) is data, not code. This module is
the reference executor for its three pure steps:

* ``check``: the upsert refusals the schema cannot express (attribute paths
  the declared vertical versions do not define, regulated paths, egress,
  reserved or clashing headers, clashing JSON targets);
* ``render``: one post to one buyer request (static URL, headers, body);
* ``decode``: one buyer response to an LEP-0012 outcome plus decoded fields.

None of them performs I/O, logs, or keeps a value: the router owns sending,
retries and money. A failure names the target key, never the value
(LEP-0015 §6), and secret values only come from the ``secret_for`` callback.
The golden vectors in ``test-vectors/buyer-mapping.json`` run against these
functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode

from .mapping import MappingError, _normalize_e164
from .messages import minor_unit_exponent

REGULATED_KEY = "x-lcp-regulated"
# Headers the executor owns or that change framing; a mapping never sets them.
RESERVED_HEADERS = frozenset(
    {"host", "content-length", "content-type", "transfer-encoding", "connection"}
)
MONEY_MIN_CENTS = 1
MONEY_MAX_CENTS = 100_000_000
REDIRECT_MAX = 2048
REFERENCE_MAX = 128

_SEGMENT = re.compile(r'\.([A-Za-z_][A-Za-z0-9_-]*)|\["([^"\\]+)"\]|\[([0-9]+)\]')


class MappingFailure(Exception):
    """A delivery stopped before any bytes were sent (LEP-0015 §6).

    ``target`` is the body or header key; the value is never carried.
    """

    def __init__(self, target: str, reason: str):
        super().__init__(f"{target}: {reason}")
        self.target = target
        self.reason = reason


@dataclass(frozen=True)
class BuyerRequest:
    url: str
    headers: dict[str, str]
    body: bytes


def parse_path(path: str, *, rooted: bool = True) -> list[str | int]:
    """Split a mapping path into property names and array indexes.

    A rooted (source) path starts at ``message`` or ``payload``; a response
    path may start with a bare name. Anything outside the grammar raises.
    """
    head = re.match(r"[A-Za-z_][A-Za-z0-9_-]*", path)
    segments: list[str | int] = []
    position = 0
    if head:
        segments.append(head.group(0))
        position = head.end()
    if rooted and (not segments or segments[0] not in {"message", "payload"}):
        raise ValueError(f"path {path!r} must start at message or payload")
    while position < len(path):
        match = _SEGMENT.match(path, position)
        if not match:
            raise ValueError(f"path {path!r} is outside the mapping grammar")
        name, quoted, index = match.groups()
        segments.append(int(index) if index is not None else (name or quoted))
        position = match.end()
    if not segments:
        raise ValueError("empty path")
    return segments


def lookup(value: Any, segments: list[str | int]) -> tuple[bool, Any]:
    """(found, value) at ``segments``; a JSON null counts as found."""
    current = value
    for segment in segments:
        if isinstance(segment, int):
            if not isinstance(current, list) or segment >= len(current):
                return False, None
        elif not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


# ─── Upsert checks ──────────────────────────────────────────────────────────


def _contains_regulated(node: Any) -> bool:
    if isinstance(node, Mapping):
        return REGULATED_KEY in node or any(_contains_regulated(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_regulated(item) for item in node)
    return False


def _attribute_node(schema: Mapping[str, Any], segments: list[str | int]) -> tuple[bool, bool]:
    """(defined, regulated) for an attributes sub-path in one vertical schema."""
    node: Any = schema
    regulated = False
    for segment in segments:
        if isinstance(segment, int):
            node = node.get("items") if isinstance(node, Mapping) else None
        else:
            properties = node.get("properties") if isinstance(node, Mapping) else None
            node = properties.get(segment) if isinstance(properties, Mapping) else None
        if not isinstance(node, Mapping):
            return False, False
        regulated = regulated or REGULATED_KEY in node
    return True, regulated or _contains_regulated(node)


def check(
    mapping: Mapping[str, Any],
    *,
    vertical_schema: Callable[[str, str], Mapping[str, Any] | None],
    url_error: Callable[[str], str | None],
) -> list[str]:
    """Upsert refusals beyond the JSON schema (run on a schema-valid mapping).

    This platform enrols no buyer for regulated identifiers (it never
    advertises ``features.regulated_identifiers``), so any path that reaches
    an ``x-lcp-regulated`` field is refused here and again at send. A
    restricted path is allowed: the mapping only ever reads the post this
    platform built for that buyer, which already carries exactly what the
    buyer may receive.
    """
    errors: list[str] = []
    vertical = str(mapping["vertical"])
    schemas: dict[str, Mapping[str, Any]] = {}
    for version in mapping["schema_versions"]:
        schema = vertical_schema(vertical, version)
        if schema is None:
            errors.append(
                f"schema_versions: {vertical} {version} is not a vertical version this platform serves"
            )
        else:
            schemas[version] = schema

    request = mapping["request"]
    for key in ("url", "test_url"):
        if key in request:
            problem = url_error(request[key])
            if problem:
                errors.append(f"request.{key}: {problem}")
    header_names = [name.lower() for name in request.get("headers", {})]
    idempotency = request.get("idempotency")
    if idempotency:
        if idempotency["header"].lower() in header_names:
            errors.append(
                f"request.idempotency.header: {idempotency['header']} is also a static header"
            )
        header_names.append(idempotency["header"].lower())
    for name in header_names:
        if name in RESERVED_HEADERS:
            errors.append(f"request.headers: {name} is set by the platform, never by a mapping")

    body = mapping["body"]
    if request["encoding"] == "json":
        for target in body:
            parts = target.split(".")
            for end in range(1, len(parts)):
                prefix = ".".join(parts[:end])
                if prefix in body:
                    errors.append(f"body.{target}: clashes with the scalar target {prefix}")
    for target, spec in body.items():
        if "from" not in spec:
            continue
        segments = parse_path(spec["from"])
        if segments[0] != "payload" or (len(segments) > 1 and segments[1] != "attributes"):
            continue
        for version, schema in schemas.items():
            defined, regulated = _attribute_node(schema, segments[2:])
            if not defined:
                errors.append(
                    f"body.{target}: {spec['from']} is not defined by {vertical} {version}"
                )
            elif regulated:
                errors.append(
                    f"body.{target}: {spec['from']} reaches a regulated identifier "
                    "(LEP-0013) and this buyer is not enrolled for it"
                )

    counter = mapping.get("counter") or {}
    fields = mapping["response"].get("fields") or {}
    if counter.get("repost") and "counter_price" not in fields:
        errors.append("counter.repost: needs response.fields.counter_price")
    return list(dict.fromkeys(errors))


# ─── Render ─────────────────────────────────────────────────────────────────


def _transform(
    name: str, value: Any, target: str, *, currency: Any, country_code: Any
) -> Any:
    def fail(why: str) -> MappingFailure:
        return MappingFailure(target, f"transform {name} {why}")

    if name in {"trim", "lower", "upper"}:
        if not isinstance(value, str):
            raise fail("needs a string")
        return {"trim": value.strip, "lower": value.lower, "upper": value.upper}[name]()
    if name == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, (bool, int, float)):
            return json.dumps(value)
        raise fail("needs a scalar")
    if name == "integer":
        if isinstance(value, bool):
            raise fail("cannot apply to a boolean")
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
            return int(value.strip())
        raise fail("cannot apply")
    if name == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        if isinstance(value, int) and value in {0, 1}:
            return value == 1
        raise fail("cannot apply")
    if name == "e164":
        try:
            return _normalize_e164(value, country_code if isinstance(country_code, str) else None)
        except MappingError:
            raise fail("cannot apply") from None
    if name == "phone_national":
        match = re.fullmatch(r"\+1([0-9]{10})", value) if isinstance(value, str) else None
        if not match:
            raise fail("needs an E.164 +1 number")
        return match.group(1)
    if name in {"date", "date_time_utc"}:
        if not isinstance(value, str):
            raise fail("needs a string")
        if name == "date" and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                raise fail("cannot apply") from None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise fail("cannot apply") from None
        if parsed.tzinfo is None:
            raise fail("needs an RFC 3339 date-time with an offset")
        if name == "date":
            return parsed.date().isoformat()
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if name in {"cents_to_units", "units_to_cents"}:
        if not (isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency)):
            raise fail("needs the post currency")
        if isinstance(value, bool) or not isinstance(value, int):
            raise fail("needs an integer")
        factor = 10 ** minor_unit_exponent(currency)
        if name == "units_to_cents":
            return value * factor
        if value % factor:
            raise fail("would not give a whole number of units")
        return value // factor
    raise fail("is not a known transform")


def _value(
    target: str,
    spec: Mapping[str, Any],
    lcp: Mapping[str, Any],
    secret_for: Callable[[str], str | None],
) -> tuple[bool, Any]:
    if "const" in spec:
        return True, spec["const"]
    if "secret" in spec:
        secret = secret_for(spec["secret"])
        if not secret:
            raise MappingFailure(target, "secret is not configured")
        return True, f"{spec.get('prefix', '')}{secret}"
    found, value = lookup(lcp, parse_path(spec["from"]))
    if not found or value is None:
        if "default" in spec:
            return True, spec["default"]
        if spec.get("required"):
            raise MappingFailure(target, "required source is missing")
        return False, None
    payload = lcp.get("payload") or {}
    currency = payload.get("currency")
    country_code = (payload.get("location") or {}).get("country_code")
    for name in spec.get("transform", []):
        value = _transform(name, value, target, currency=currency, country_code=country_code)
    if "map" in spec:
        if isinstance(value, str) and value in spec["map"]:
            value = spec["map"][value]
        elif "map_default" in spec:
            value = spec["map_default"]
    return True, value


def _set_nested(body: dict[str, Any], target: str, value: Any) -> None:
    parts = target.split(".")
    node = body
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _form_scalar(target: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return value
    raise MappingFailure(target, "form encoding needs a scalar value")


def render(
    mapping: Mapping[str, Any],
    post: Mapping[str, Any],
    *,
    secret_for: Callable[[str], str | None],
) -> BuyerRequest | None:
    """The buyer request for one post, or ``None`` for an undeliverable test post.

    A post with ``message.test`` true goes only to ``test_url``; without one
    it is not delivered (LEP-0015 §4). Raises ``MappingFailure`` before any
    bytes exist.
    """
    lcp = post["lcp"]
    message = lcp["message"]
    request = mapping["request"]
    url = request.get("test_url") if message.get("test") else request["url"]
    if url is None:
        return None
    form = request["encoding"] == "form"
    headers: dict[str, str] = {
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json"
    }
    for name, spec in request.get("headers", {}).items():
        _, value = _value(name, spec, lcp, secret_for)
        headers[name] = value if isinstance(value, str) else json.dumps(value)
    idempotency = request.get("idempotency")
    if idempotency:
        headers[idempotency["header"]] = str(message["idempotency_key"])

    pairs: list[tuple[str, str]] = []
    nested: dict[str, Any] = {}
    for target, spec in mapping["body"].items():
        present, value = _value(target, spec, lcp, secret_for)
        if not present:
            continue
        if form:
            pairs.append((target, _form_scalar(target, value)))
        else:
            _set_nested(nested, target, value)
    if form:
        body = urlencode(pairs).encode("utf-8")
    else:
        body = json.dumps(nested, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return BuyerRequest(url=url, headers=headers, body=body)


# ─── Decode ─────────────────────────────────────────────────────────────────


def _parse(encoding: str, body: bytes | None) -> tuple[Any, bool]:
    """(document, decoded). ``None`` body = unread (over the limit)."""
    if body is None:
        return None, False
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None, False
    if not text.strip():
        return None, True
    try:
        if encoding == "form":
            return dict(parse_qsl(text, keep_blank_values=True, strict_parsing=True)), True
        return json.loads(text), True
    except ValueError:
        return None, False


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, (bool, str)) or isinstance(right, (bool, str)):
        return type(left) is type(right) and left == right
    return left == right


def _matches(when: Mapping[str, Any], status: int, document: Any) -> bool:
    if "status" in when and status not in when["status"]:
        return False
    if "path" not in when:
        return True
    found, value = lookup(document, parse_path(when["path"], rooted=False))
    if "exists" in when:
        return found is when["exists"]
    if not found:
        return False
    if "equals" in when:
        return _same(value, when["equals"])
    return any(_same(value, candidate) for candidate in when["in"])


def _money(spec: Mapping[str, Any], document: Any, currency: str) -> int | None:
    found, value = lookup(document, parse_path(spec["path"], rooted=False))
    if not found or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            amount = Decimal(str(value))
        elif isinstance(value, str) and re.fullmatch(r"[0-9]+(\.[0-9]+)?", value):
            amount = Decimal(value)
        else:
            return None
    except InvalidOperation:
        return None
    if spec["unit"] == "units":
        amount *= 10 ** minor_unit_exponent(currency)
    if amount != amount.to_integral_value():
        return None
    cents = int(amount)
    return cents if MONEY_MIN_CENTS <= cents <= MONEY_MAX_CENTS else None


def _text(spec: Mapping[str, Any] | None, document: Any) -> str | None:
    if spec is None:
        return None
    found, value = lookup(document, parse_path(spec["path"], rooted=False))
    if not found or isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value)


def decode(
    mapping: Mapping[str, Any], status: int, body: bytes | None, *, currency: str
) -> dict[str, Any]:
    """One buyer response to an LEP-0012 outcome and the decoded fields.

    ``rule`` names what decided it: a rule index, ``default``, or
    ``undecodable`` (a 2xx body that failed to decode). Money is never
    invented: a ``sold`` whose declared price fails to decode is ``pending``.
    """
    response = mapping["response"]
    fields = response.get("fields") or {}
    document, decoded = _parse(response["encoding"], body)
    if 200 <= status < 300 and not decoded:
        return {"outcome": "pending", "rule": "undecodable"}
    outcome, reason, rule = response["default"], None, "default"
    for index, candidate in enumerate(response["rules"]):
        if _matches(candidate["when"], status, document):
            outcome, reason, rule = candidate["outcome"], candidate.get("reason"), index
            break
    result: dict[str, Any] = {"outcome": outcome, "rule": rule}
    reference = _text(fields.get("buyer_reference"), document)
    if reference is not None and 0 < len(reference) <= REFERENCE_MAX:
        result["buyer_reference"] = reference
    if outcome == "sold":
        if "price" in fields:
            price = _money(fields["price"], document, currency)
            if price is None:
                result["outcome"] = "pending"
                result["rule"] = "price_undecodable"
                return result
            result["price_cents"] = price
        redirect = _text(fields.get("redirect_url"), document)
        if redirect and redirect.startswith("https://") and len(redirect) <= REDIRECT_MAX:
            result["redirect_url"] = redirect
        return result
    if outcome == "price_rejected" and "counter_price" in fields:
        counter = _money(fields["counter_price"], document, currency)
        if counter is not None:
            result["counter_price_cents"] = counter
    if outcome in {"unsold", "price_rejected"}:
        if reason is None and "reason" in fields:
            raw = _text(fields["reason"], document)
            if raw is not None:
                reason = (fields["reason"].get("map") or {}).get(
                    raw, fields["reason"].get("map_default", "other")
                )
        if reason is not None:
            result["rejection_reason"] = reason
    return result

"""LEP-0008 agent attestation verification: issuer-anchored JWS verifier,
JWKS resolver, and the frozen audit-metadata hook.

This module implements the accepted record's §5 13-step procedure exactly,
in order, stopping at the first failing step so the same forged input always
yields the same ``details.reason`` (closed vocabulary, §5/§8). Failure
attribution (§8):

* token **shape** (not three base64url segments / > 8192 bytes) is the
  schema-layer class — ``LCP-100`` / 400. The record ships that as a
  ``schemas/core.json`` pattern; until the pattern + regenerated bundles land
  (separate wire-lane change), this module enforces the identical shape gate
  at the intake slot so the wire outcome matches §8 row 2.
* every post-shape verification failure is the identity axis — ``LCP-002`` /
  401 with ``details.reason``.
* ``key_unavailable`` is the availability axis only (DNS/connect/timeout or
  an issuer 5xx) — ``LCP-500`` / 500, retryable, never fail-open in enforce
  mode; a *fetched-but-invalid* document (oversize, > 32 keys, non-JSON,
  duplicate ``kid`` under §4 option A, issuer 4xx/3xx) is sender/issuer-
  fixable and routes ``key_unknown`` / 401.

Privacy (§Privacy, revision F8): audit metadata is exactly
``{outcome, reason, iss_host, kid, tok_sha256_16}`` — the token (or any
segment of it) never enters audit rows, and nothing here logs token bytes.
``tok_sha256_16`` is the first 16 hex characters of SHA-256 over the full
compact token bytes (input definition normative).

Egress (§4 Resolution, revision F1 / re-dossier R1): every JWKS fetch runs
the **paired** shipped controls — ``validate_webhook_url`` + resolve-aware
``validate_egress_host`` — against a config view with
``allow_insecure_webhooks`` forced off: attestation resolution is HTTPS-only
and rejects private/loopback/link-local/multicast/reserved/unspecified
destinations post-resolution in every deployment mode, sandbox included. The
fetch client mirrors the shipped delivery path (``router.py`` ``_deliver``):
``follow_redirects=False`` and ``trust_env=False``.

Tests: ``tests/test_attestation_verification.py`` (record gates V1–V7, V9,
V10, V12–V15, V17–V20, V22–V24) and ``tests/test_attestation_resolver.py``
(resolver cache/bounds units). Vector-tier gates (V8/V11/V16/V21) belong to
the vector lane and are untouched here.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import hashlib
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from .config import PlatformConfig
from .security import SecurityPolicyError, validate_egress_host, validate_webhook_url

# §5/§8 — closed reason vocabulary.
REASONS = frozenset(
    {
        "format",
        "algorithm",
        "issuer_policy",
        "key_unknown",
        "key_unavailable",
        "signature",
        "expired",
        "audience",
        "claims_mismatch",
    }
)

# §3 algorithm allowlist (closed, exact case-sensitive membership; everything
# else — MACs, ``none``, whitespace, case variants — is an ``algorithm``
# failure at step 3, before any key lookup).
ALG_ALLOWLIST = ("EdDSA", "ES256")

# §2 wire shape: three non-empty base64url segments (no ``=`` padding —
# padded tokens are rejected at the schema layer, revision F6b) and the
# 8192-byte size bound.
SHAPE_PATTERN_SOURCE = r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$"
_SHAPE_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
MAX_TOKEN_BYTES = 8192

# §4 Bounds: JWKS document <= 32 keys (the byte bound is config-tunable down
# from 16 KiB; the key count is normative).
MAX_JWKS_KEYS = 32
# §4 Cache: negative-cache ceiling and 24 h hard cap are normative bounds.
NEGATIVE_CACHE_SECONDS = 30.0
MAX_CACHE_TTL_SECONDS = 86_400.0

# §4: the verification document path is pinned as literal concatenation
# ``jwks_url = iss + "/.well-known/jwks.json"`` (RFC 8615, per issuer).
JWKS_PATH = "/.well-known/jwks.json"

# §8 wire pointer for the attestation axis.
ATTESTATION_POINTER = "provenance/agent/attestation"


class AttestationShapeError(ValueError):
    """§2 token-shape failure — the schema-layer class (LCP-100 / 400)."""


class AttestationFailure(Exception):
    """A §5 step failed; carries the pinned first-failing reason and the
    frozen audit metadata known at that point (never token bytes)."""

    reason: str
    metadata: dict[str, Any]

    def __init__(
        self,
        reason: str,
        *,
        kid: str | None = None,
        iss_host: str | None = None,
        tok_sha256_16: str | None = None,
    ):
        if reason not in REASONS:  # fail loud if the vocabulary is extended
            raise ValueError(f"non-conformant attestation reason {reason!r}")
        self.reason = reason
        self.metadata = {
            "iss_host": iss_host,
            "kid": kid,
            "tok_sha256_16": tok_sha256_16,
        }
        super().__init__(reason)


class KeyUnavailableError(Exception):
    """Resolver availability failure only (§8: DNS/connect/timeout/5xx)."""


class JwksDocumentError(Exception):
    """Fetched-but-invalid document or no key for ``kid`` (§8: key_unknown)."""


@dataclass(frozen=True)
class AttestationBinding:
    """Step 13 accept: the verified binding (kid + issuer host) plus the
    token digest, in the frozen §Privacy metadata shape (outcome/reason are
    added by the audit caller)."""

    kid: str
    iss_host: str | None
    tok_sha256_16: str

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "iss_host": self.iss_host,
            "kid": self.kid,
            "tok_sha256_16": self.tok_sha256_16,
        }


def token_digest(token: str) -> str:
    """``tok_sha256_16``: SHA-256 over the full compact token bytes, first 16
    hex characters (revision F8 input definition)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def check_attestation_shape(token: str) -> None:
    """§2 shape gate — the schema-layer class. Raises AttestationShapeError
    with schema-style pointer text for a non-compact-JWS or oversize value
    (free text, ``alg:none`` detached, padded segments, 1/2/4 segments,
    > 8192 bytes)."""
    if len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
        raise AttestationShapeError(
            f"attestation is greater than the {MAX_TOKEN_BYTES} byte limit "
            f"at {ATTESTATION_POINTER}"
        )
    if not _SHAPE_PATTERN.fullmatch(token):
        raise AttestationShapeError(
            f"{token!r} does not match '{SHAPE_PATTERN_SOURCE}' "
            f"at {ATTESTATION_POINTER}"
        )


# ---------------------------------------------------------------------------
# Strict JSON parsing (§5 step 2, Parser rule; revisions F2 / R2 / R3)
# ---------------------------------------------------------------------------


class _DuplicateMemberError(ValueError):
    pass


class _NonIntegerLiteral:
    """Sentinel for any JSON float literal (fraction and/or exponent part).
    NumericDate is judged on the claims BYTES: ``1e9``, ``2.5``, ``2e9`` all
    reach parse_float and are therefore NOT base-10 integer literals, even
    where an IEEE-double parser reads ``2e9`` as integer-valued (re-dossier
    R3)."""

    __slots__ = ()


_NON_INTEGER_LITERAL = _NonIntegerLiteral()


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """RFC 8259 §5 duplicate-name rejection at EVERY nesting depth: the
    object_pairs_hook fires per object, innermost first, so a repeated
    member inside a nested claim value (``{"meta":{"tier":"gold",
    "tier":"platinum"}}``) is the same step-2 ``format`` failure as a
    top-level one. No last-wins fallback exists."""
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise _DuplicateMemberError(key)
        obj[key] = value
    return obj


def _mark_float_literal(_raw: str) -> _NonIntegerLiteral:
    return _NON_INTEGER_LITERAL


def _parse_strict_json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_members,
            parse_float=_mark_float_literal,
        )
    except (_DuplicateMemberError, UnicodeDecodeError, ValueError):
        raise _DecodeStepError("format") from None
    if not isinstance(value, dict):
        # Step 2 pins strict parse of both segments as JSON *objects*.
        raise _DecodeStepError("format")
    return value


class _DecodeStepError(Exception):
    """Internal: decode-time step failure with its pinned reason."""

    reason: str

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _SignatureStepError(Exception):
    """Internal: step-8 failure with its pinned reason (format/algorithm/
    key_unknown/signature)."""

    reason: str

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


# ---------------------------------------------------------------------------
# Egress guard (§4 Resolution) — reuse of the shipped paired controls
# ---------------------------------------------------------------------------


def validate_issuer_egress(iss: str, config: PlatformConfig) -> str:
    """Return the JWKS URL for ``iss`` after the paired egress controls, or
    raise SecurityPolicyError (mapped to ``issuer_policy`` by the verifier).

    HTTPS-only in ANY mode: the shipped class permits ``http`` under
    ``allow_insecure_webhooks`` and skips the resolve leg there; attestation
    resolution MUST NOT inherit that skip (re-dossier R1), so both legs run
    against a config view with ``allow_insecure_webhooks`` forced off. The
    guard logic itself stays in ``security.py`` — no user-info, hostname
    presence, and the post-resolution
    private/loopback/link-local/multicast/reserved/unspecified rejection
    that kills the obfuscated-literal class (``https://2130706433``,
    ``https://0x7f000001``, ``https://127.1``, ``https://localhost``) —
    exactly as shipped on the delivery path."""
    jwks_url = iss + JWKS_PATH
    strict = replace(config, allow_insecure_webhooks=False)
    validate_webhook_url(jwks_url, strict)
    validate_egress_host(urlsplit(jwks_url).hostname or "", strict)
    return jwks_url


# ---------------------------------------------------------------------------
# JWKS resolver (§4 canonical trust model: issuer-published JWKS)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchedDocument:
    status: int
    headers: Mapping[str, str]
    body: bytes


FetchTransport = Callable[[str, float, int], FetchedDocument]


def _httpx_fetch(url: str, timeout: float, max_bytes: int) -> FetchedDocument:
    """Default transport: mirrors the shipped delivery client
    (``router.py:_deliver``) — no redirects, no proxy/env interception,
    bounded streaming at the configured document cap. Any transport-level
    failure (DNS/connect/timeout) is an availability failure (§8)."""
    try:
        with (
            httpx.Client(
                timeout=timeout, follow_redirects=False, trust_env=False
            ) as client,
            client.stream("GET", url) as response,
        ):
            body = b""
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    break
            return FetchedDocument(
                status=response.status_code, headers=dict(response.headers), body=body
            )
    except httpx.TransportError as exc:
        raise KeyUnavailableError(f"JWKS fetch failed: {exc}") from exc


_MAX_AGE_PATTERN = re.compile(r"(?:^|[,;])\s*max-age\s*=\s*(\d+)", re.IGNORECASE)


class JwksResolver:
    """Issuer-JWKS resolver with the §4 rules: cache per issuer keyed on
    (iss, kid) selection, honoring HTTP ``cache-control``/``max-age`` under
    the configured TTL and the 24 h hard cap; negative cache for fetch
    failures <= 30 s; document bounds (configured size cap, <= 32 keys);
    duplicate ``kid`` invalidates the document (§4 Selection option A,
    maintainer-accepted 2026-11-03) — which also covers the ``use``/
    ``key_ops`` confusion class."""

    config: PlatformConfig
    fetch: FetchTransport
    clock: Callable[[], float]

    def __init__(
        self,
        config: PlatformConfig,
        fetch: FetchTransport | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.config = config
        self.fetch = fetch or _httpx_fetch
        # Monotonic for cache windows; NumericDate uses the wall clock.
        self.clock = clock or time.monotonic
        self._documents: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
        self._unavailable_until: dict[str, float] = {}

    def resolve(self, iss: str, kid: str) -> dict[str, Any]:
        now = self.clock()
        cached = self._documents.get(iss)
        if cached is not None and cached[0] > now:
            return self._select(cached[1], kid)
        if self._unavailable_until.get(iss, 0.0) > now:
            raise KeyUnavailableError(
                "issuer fetch is inside the negative-cache window"
            )
        # Re-run the paired egress guard on every attempted fetch (§4: the
        # MUSTs bind every outbound fetch on the verification path).
        jwks_url = validate_issuer_egress(iss, self.config)
        try:
            fetched = self.fetch(
                jwks_url,
                self.config.attestation_fetch_timeout_seconds,
                self.config.attestation_jwks_max_bytes,
            )
        except KeyUnavailableError:
            self._unavailable_until[iss] = now + NEGATIVE_CACHE_SECONDS
            raise
        if fetched.status >= 500:
            # Availability failure only: DNS/connect/timeout or issuer 5xx.
            self._unavailable_until[iss] = now + NEGATIVE_CACHE_SECONDS
            raise KeyUnavailableError(f"issuer returned HTTP {fetched.status}")
        if not 200 <= fetched.status < 300:
            # 4xx (e.g. 404 on the /.well-known path) is the issuer's
            # configuration -> fetched-but-invalid (§8, revision F4). A 3xx
            # is NEVER followed (V14 — there is no second fetch of Location)
            # and joins the same invalid-response class.
            raise JwksDocumentError(f"issuer returned HTTP {fetched.status}")
        if len(fetched.body) > self.config.attestation_jwks_max_bytes:
            raise JwksDocumentError("JWKS document exceeds the size bound")
        document = self._parse_document(fetched.body)
        ttl = self._cache_ttl(fetched.headers)
        self._documents[iss] = (now + ttl, document)
        return self._select(document, kid)

    def _parse_document(self, body: bytes) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            raise JwksDocumentError("JWKS document is not valid JSON") from None
        if not isinstance(parsed, dict) or not isinstance(parsed.get("keys"), list):
            raise JwksDocumentError("JWKS document has no keys array")
        keys: list[Any] = parsed["keys"]
        if len(keys) > MAX_JWKS_KEYS:
            raise JwksDocumentError("JWKS document exceeds the key-count bound")
        by_kid: dict[str, dict[str, Any]] = {}
        for entry in keys:
            if not isinstance(entry, dict):
                raise JwksDocumentError("JWKS key entry is not an object")
            entry_kid = entry.get("kid")
            entry_kty = entry.get("kty")
            if (
                not isinstance(entry_kid, str)
                or not entry_kid
                or not isinstance(entry_kty, str)
                or not entry_kty
            ):
                raise JwksDocumentError("JWKS key entry lacks kid/kty")
            if entry_kid in by_kid:
                # §4 Selection option A (maintainer-accepted): a document
                # with two JWKs sharing a kid is an INVALID document.
                raise JwksDocumentError("JWKS document contains a duplicate kid")
            if entry.get("use") not in (None, "sig"):
                raise JwksDocumentError("JWKS key is not a signature key")
            key_ops = entry.get("key_ops")
            if isinstance(key_ops, list) and "verify" not in key_ops:
                raise JwksDocumentError("JWKS key does not permit verification")
            by_kid[entry_kid] = entry
        return by_kid

    def _select(self, document: dict[str, dict[str, Any]], kid: str) -> dict[str, Any]:
        jwk = document.get(kid)
        if jwk is None:
            raise JwksDocumentError("no key for kid in a valid JWKS document")
        return jwk

    def _cache_ttl(self, headers: Mapping[str, str]) -> float:
        ceiling = min(
            self.config.attestation_jwks_cache_ttl_seconds, MAX_CACHE_TTL_SECONDS
        )
        cache_control = headers.get("cache-control")
        if cache_control:
            match = _MAX_AGE_PATTERN.search(cache_control)
            if match:
                return min(float(match.group(1)), ceiling)
        return ceiling


# ---------------------------------------------------------------------------
# Signature verification (§5 step 8; §3 P1363-only encoding)
# ---------------------------------------------------------------------------


def _verify_signature(
    alg: str, jwk: dict[str, Any], signing_input: bytes, signature: bytes
) -> None:
    """Fixed-width precheck first: EdDSA 64 B; ES256 IEEE P1363 r||s 64 B —
    §3: DER and any other variable-width encoding MUST NOT be accepted, so
    wrong width is ``format``, not ``signature``. Key-type/curve consistency
    with the header alg is ``algorithm`` (§3/§4); unparseable key material is
    a malformed document (``key_unknown``); correct width with wrong bytes
    is ``signature``."""
    if len(signature) != 64:
        raise _SignatureStepError("format")
    if alg == "EdDSA":
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
            raise _SignatureStepError("algorithm")
        x = jwk.get("x")
        if not isinstance(x, str):
            raise _SignatureStepError("key_unknown")
        try:
            key = ed25519.Ed25519PublicKey.from_public_bytes(_b64url_decode(x))
        except (binascii.Error, ValueError):
            raise _SignatureStepError("key_unknown") from None
        try:
            key.verify(signature, signing_input)
        except InvalidSignature:
            raise _SignatureStepError("signature") from None
        return
    if alg == "ES256":
        if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
            raise _SignatureStepError("algorithm")
        x, y = jwk.get("x"), jwk.get("y")
        if not isinstance(x, str) or not isinstance(y, str):
            raise _SignatureStepError("key_unknown")
        try:
            x_bytes, y_bytes = _b64url_decode(x), _b64url_decode(y)
            if len(x_bytes) != 32 or len(y_bytes) != 32:
                raise ValueError("P-256 coordinate width")
            key = ec.EllipticCurvePublicNumbers(
                x=int.from_bytes(x_bytes, "big"),
                y=int.from_bytes(y_bytes, "big"),
                curve=ec.SECP256R1(),
            ).public_key()
            # P1363 r||s -> the (r, s) integer pair, RFC 7518 §3.4.
            der = encode_dss_signature(
                int.from_bytes(signature[:32], "big"),
                int.from_bytes(signature[32:], "big"),
            )
        except (binascii.Error, ValueError, TypeError):
            raise _SignatureStepError("key_unknown") from None
        try:
            key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            raise _SignatureStepError("signature") from None
        return
    raise _SignatureStepError("algorithm")


# ---------------------------------------------------------------------------
# The 13-step verifier (§5)
# ---------------------------------------------------------------------------

_MUST_STRING_CLAIMS = ("iss", "aud", "agent_id", "acting_for")


class AgentAttestationVerifier:
    """Runs §5 steps 1–13 once per lead acceptance at the pinned intake slot
    (wired in ``router.Platform.ingest`` after the dedup early returns,
    before ``insert_lead``).

    The resolver is injectable (deterministic, network-free tests); so is the
    clock (V22 boundary probes). ``iat``/``nbf`` freshness enforcement is out
    of scope for the record (Not-in-scope list); presence/type is enforced at
    step 4. Receiver-side header policy (re-dossier R4): ``crit`` and ``b64``
    are step-2 ``format`` failures; ``typ`` never decides an outcome; every
    other unknown member is ignored and MUST NOT be dereferenced — the only
    key-resolution path is ``iss -> JWK[kid]`` (§5 step 7)."""

    config: PlatformConfig
    resolver: JwksResolver
    clock: Callable[[], float]

    def __init__(
        self,
        config: PlatformConfig,
        resolver: JwksResolver | None = None,
        clock: Callable[[], float] | None = None,
    ):
        self.config = config
        self.resolver = resolver or JwksResolver(config)
        self.clock = clock or time.time

    def verify(
        self,
        token: str,
        *,
        receiver_id: str,
        envelope_agent_id: str,
        sender_id: str,
        scopes: Callable[[str], bool],
    ) -> AttestationBinding:
        digest = token_digest(token)
        kid: str | None = None
        iss_host: str | None = None

        def fail(reason: str) -> AttestationFailure:
            return AttestationFailure(
                reason, kid=kid, iss_host=iss_host, tok_sha256_16=digest
            )

        # Step 1 — compact-JWS shape (schema normally; belt here per the
        # revision battery: "padded segment at verifier reason=format
        # step=1"). Non-compact serializations and padded segments end here.
        try:
            check_attestation_shape(token)
        except AttestationShapeError:
            raise fail("format") from None
        segments = token.split(".")
        try:
            header_bytes = _b64url_decode(segments[0])
            claims_bytes = _b64url_decode(segments[1])
            signature = _b64url_decode(segments[2])
        except (binascii.Error, ValueError):
            raise fail("format") from None

        # Step 2 — strict UTF-8 JSON objects; duplicate member names REJECT
        # AT PARSE at every nesting depth; §2 receiver-side header policy is
        # evaluated at this, the shape, step.
        try:
            header = _parse_strict_json_object(header_bytes)
            claims = _parse_strict_json_object(claims_bytes)
        except _DecodeStepError as step:
            raise fail(step.reason) from None
        if "crit" in header or "b64" in header:
            # This profile defines no extensions: any crit names an extension
            # the receiver cannot understand; a b64 header is non-conformant
            # (§2). Both are step-2 format failures.
            raise fail("format")

        # Step 3 — exact, case-sensitive alg match in {EdDSA, ES256}, BEFORE
        # any key lookup: no trimming, case-folding, aliasing, or
        # post-lookup relaxation.
        alg = header.get("alg")
        if not isinstance(alg, str) or alg not in ALG_ALLOWLIST:
            raise fail("algorithm")

        # Step 4 — §6 MUST claims present with declared types. NumericDate is
        # judged on the claims BYTES: a base-10 integer literal only — the
        # float-literal sentinel catches fractions AND exponents. String,
        # float, bool, zero/negative or absent timestamps are format here;
        # never coerced, never ignored.
        for claim in _MUST_STRING_CLAIMS:
            value = claims.get(claim)
            if not isinstance(value, str) or not value:
                raise fail("format")
        iss = claims["iss"]
        for temporal in ("iat", "exp"):
            value = claims.get(temporal)
            if value is _NON_INTEGER_LITERAL:
                raise fail("format")
            if isinstance(value, bool) or not isinstance(value, int):
                raise fail("format")
            if value <= 0:
                raise fail("format")

        # Step 5 — header.kid present (§8 maps a missing kid to key_unknown).
        raw_kid = header.get("kid")
        if not isinstance(raw_kid, str) or not raw_kid:
            raise fail("key_unknown")
        kid = raw_kid

        # Step 6 — iss policy-valid via the paired egress controls (§4).
        # A policy-invalid iss MUST NOT reach issuer resolution: zero fetch.
        # iss_host is recorded pre-validity: it is the issuer URL's host
        # component only (frozen §Privacy schema), never a credential.
        iss_host = urlsplit(iss).hostname
        try:
            validate_issuer_egress(iss, self.config)
        except SecurityPolicyError:
            raise fail("issuer_policy") from None

        # Step 7 — resolve iss -> JWK[kid]. Transport failure is
        # key_unavailable (500 class); no such kid or an invalid document
        # (duplicate kid under option A included) is key_unknown (401).
        # Header URL members (jku/x5u/x5c) are NEVER dereferenced.
        try:
            jwk = self.resolver.resolve(iss, kid)
        except KeyUnavailableError:
            raise fail("key_unavailable") from None
        except JwksDocumentError:
            raise fail("key_unknown") from None
        except SecurityPolicyError:
            raise fail("issuer_policy") from None
        jwk_alg = jwk.get("alg")
        if jwk_alg is not None and jwk_alg != alg:
            # §3: the expected algorithm derives from the resolved JWK when
            # present; mismatch is an algorithm failure.
            raise fail("algorithm")

        # Step 8 — fixed-width signature precheck, then verify over the
        # header.claims bytes (the transmitted ASCII segment pair).
        signing_input = f"{segments[0]}.{segments[1]}".encode("ascii")
        try:
            _verify_signature(alg, jwk, signing_input, signature)
        except _SignatureStepError as step:
            raise fail(step.reason) from None

        # Step 9 — expiry: now >= exp + skew fails (reject AT equality,
        # revision F7; the <= 30 s cap is a configuration-time MUST).
        # iat/nbf freshness is deliberately not enforced at intake (§7).
        if (
            int(self.clock())
            >= claims["exp"] + self.config.attestation_clock_skew_seconds
        ):
            raise fail("expired")

        # Step 10 — aud binds the receiver; kills cross-receiver replay.
        if claims["aud"] != receiver_id:
            raise fail("audience")

        # Step 11 — byte-equal agent principal binding (§6).
        if claims["agent_id"] != envelope_agent_id:
            raise fail("claims_mismatch")

        # Step 12 — acting_for == authenticated sender_id, or carried by the
        # shipped scope machinery (§5: auth.has_scope — exact membership,
        # substring-free; wildcard/admin semantics stay the shipped ones).
        if claims["acting_for"] != sender_id and not scopes(
            f"act_for:{claims['acting_for']}"
        ):
            raise fail("claims_mismatch")

        # Step 13 — accept: bind agent_id/acting_for/iss/kid into the record.
        return AttestationBinding(kid=kid, iss_host=iss_host, tok_sha256_16=digest)

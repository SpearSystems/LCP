"""LEP-0008 implementation gates (record gate table, plat tier).

Named tests per the accepted record's gate table:

* V1  ``test_attestation_free_text_rejected`` — invalid A → 400 LCP-100 +
      attestation pointer (and the §2 padded-precedence case, the plat-tier
      pin of vec gate V21).
* V2  ``test_attestation_forged_hs256_rejected`` — invalid B → 401 LCP-002
      ``reason=algorithm``; step 3 fires BEFORE any key lookup even though
      the token also lacks ``aud`` (revision-battery ordering proof).
* V3  ``test_attestation_expired_first_axis`` — invalid C → ``expired``
      (first-failing-axis determinism).
* V4  ``test_attestation_cross_receiver_rejected`` — invalid D → ``audience``.
* V5  ``test_attestation_claims_mismatch_rejected`` — invalid E, both axes.
* V6  ``test_attestation_alg_none_and_detached_rejected`` — invalid F at the
      schema-shape layer (400) and the verifier layer (401 algorithm).
* V7  ``test_attestation_tampered_signature_rejected`` — invalid G →
      ``signature``; valid-a accepted end-to-end (fixture resolver).
* V9  ``test_attestation_unknown_kid_rejected`` + ``test_act_for_scope_delegation``.
* V10 ``test_attestation_jwks_unavailable_fails_closed` — availability-only
      500; fetched-but-invalid is V18, NOT this path.
* V13 ``test_attestation_iss_obfuscated_loopback_rejected`` — the resolve-aware
      egress leg rejects ``127.0.0.1`` / ``localhost`` / ``2130706433`` /
      ``0x7f000001`` / ``127.1`` / ``::ffff:127.0.0.1`` at step 6 with ZERO
      fetch issued, ALSO under the sandbox config (``test_mode=True`` +
      ``allow_insecure_webhooks=True``, re-dossier R1) — the insecure-webhook
      escape hatch must not relax JWKS egress; HTTPS-only likewise.
* V14 ``test_attestation_jwks_fetch_no_redirects_no_proxy_env`` — a 302 is
      never followed (no second fetch of ``Location``); the client is built
      with ``follow_redirects=False`` / ``trust_env=False`` so an
      ``HTTPS_PROXY`` in the environment cannot reroute the fetch.
* V15 ``test_attestation_decode_time_type_presence_format`` — string/float/
      zero/absent ``iat``/``exp``, absent MUST claims, non-object claims,
      duplicate names (top-level AND nesting depth 2), exponent-notation
      timestamps (re-dossier R3): all ``format`` at step 2/4 with zero
      key-resolution and zero crypto work.
* V17 ``test_attestation_es256_der_rejected`` — DER-in-slot → ``format`` at
      the step-8 width precheck; the same claim set's P1363 form verifies.
* V18 ``test_attestation_jwks_oversize_is_401`` — oversize / >32 keys /
      non-JSON fetched documents → 401 ``key_unknown``, never 500.
* V19 ``test_attestation_jwks_duplicate_kid_rejected`` — §4 option A
      (maintainer decision 2026-11-03): duplicate-kid document is invalid →
      ``key_unknown``.
* V20 ``test_attestation_duplicate_ack_never_refetches`` — §5 placement: the
      DUPLICATE ack and the LCP-005 collision path run BEFORE verification.
* V22 ``test_attestation_skew_config_capped_at_30`` — skew > 30 s refused at
      configuration time; ``now == exp + skew`` rejects (at equality),
      ``exp + skew - 1`` accepts.
* V23 ``test_history_projection_never_emits_attestation_bytes`` — the frozen
      audit schema ``{outcome, reason, iss_host, kid, tok_sha256_16}`` and no
      token bytes in any audit row, error body, metric, or log line. (The
      LEP-0005 history read surface is #202; its half of the interlock is
      asserted there.)
* V24 ``test_attestation_header_member_policy`` — ``typ`` never decides;
      ``JWT`` and ``at+jwt`` verify identically; ``x5u``/``jku`` members are
      ignored with ZERO dereference fetches; ``crit`` is a step-2 ``format``
      failure.

Plus the mode contract: ``test_attestation_observe_enforce_parity`` (observe
verifies + audits + accepts the identical reason; enforce rejects) and the §5
non-agent skip.

Deterministic and network-free: the fixture keys derive from the record's
published seeds (Ed25519 ``SHA-256("lep-0008-fixture-ed25519-seed")``;
ES256 scalar ``SHA-256("lep-0008-fixture-p256-seed")`` — the derivation test
reproduces the JWK bytes pinned in the record); DNS resolution is a fixture
table whose values match the live ``getaddrinfo`` answers the record's
battery observed (all loopback literals → ``127.0.0.1``); every JWKS read
goes through an injected fake transport or a stubbed resolver. The record's
inline example tokens are stored in the LEP file as compression handles
(``$$JWT_*$$``); per the record's §Valid-example derivation spec
("any reviewer regenerates every token byte-for-byte" from the pinned seed +
claims), the fixtures here re-generate rather than decode the handles.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
from typing import Any
import unittest
from unittest import mock
from uuid import NAMESPACE_URL, uuid5

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from lcp_platform import attestation as attestations
from lcp_platform.attestation import (
    AgentAttestationVerifier,
    AttestationFailure,
    FetchedDocument,
    JwksDocumentError,
    JwksResolver,
    KeyUnavailableError,
)
from lcp_platform.auth import signature_for
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService
from lcp_platform.storage import now_iso

ROOT = Path(__file__).resolve().parents[3]
NAMESPACE = uuid5(NAMESPACE_URL, "lep-0008-verification")

PUB_SECRET = "lep0008-publisher-secret"
AGENT_SECRET = "lep0008-agent-secret"

# The record's pinned fixture issuer and claim set (LEP-0008 §Valid example).
ISSUER = "https://agents.publisher.example/attestation"
VALID_CLAIMS = {
    "iss": ISSUER,
    "aud": "platform_001",
    "agent_id": "ai-assist-001",
    "acting_for": "pub_123",
    "iat": 1780272000,
    "exp": 2080000000,
}
VALID_HEADER = {"alg": "EdDSA", "typ": "JWT", "kid": "attest-key-1"}

# The record's invalid-F token, verbatim (empty third segment).
ALG_NONE_DETACHED = (
    "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    ".eyJpc3MiOiJodHRwczovL2FnZW50cy5wdWJsaXNoZXIuZXhhbXBsZS9hdHRlc3RhdGlvbiIs"
    "ImF1ZCI6InBsYXRmb3JtXzAwMSIsImFnZW50X2lkIjoiYWktYXNzaXN0LTAwMSIsImFjdGluZ19"
    "mb3IiOiJwdWJfMTIzIiwiaWF0IjoxNzgwMjcyMDAwLCJleHAiOjIwODAwMDAwMDB9."
)

# ---------------------------------------------------------------------------
# Record-published fixture keys (do not invent keys — seeds are in the record)
# ---------------------------------------------------------------------------

ED25519_SEED = hashlib.sha256(b"lep-0008-fixture-ed25519-seed").digest()
ED25519_PRIVATE = Ed25519PrivateKey.from_private_bytes(ED25519_SEED)
ES256_SCALAR = int.from_bytes(
    hashlib.sha256(b"lep-0008-fixture-p256-seed").digest(), "big"
)
ES256_PRIVATE = ec.derive_private_key(ES256_SCALAR, ec.SECP256R1())


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _ed25519_jwk() -> dict[str, Any]:
    x = ED25519_PRIVATE.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _b64u(x),
        "kid": "attest-key-1",
        "alg": "EdDSA",
    }


def _es256_jwk() -> dict[str, Any]:
    numbers = ES256_PRIVATE.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64u(numbers.x.to_bytes(32, "big")),
        "y": _b64u(numbers.y.to_bytes(32, "big")),
        "kid": "attest-es256-1",
        "alg": "ES256",
    }


ED25519_JWK = _ed25519_jwk()
ES256_JWK = _es256_jwk()
# JWK bytes pinned in the record (§Valid example + F3 ES256 evidence).
RECORD_ED25519_X = "1bCirUUETlbD2Z3ZNcYH95IskGacAJdPgSxxLRswlbY"
RECORD_ES256_X = "LHhhgrv-4W6IbzTx11LUcQukfb_RwXGOV4l4HEcRBok"
RECORD_ES256_Y = "5EEm6hx5OK4SCIDDSYrd-Bm4Mt--lsAAItTb3ZZN3fQ"


def make_token(
    header: dict[str, Any],
    claims: dict[str, Any],
    private_key: Any,
    *,
    alg: str = "EdDSA",
) -> str:
    return make_token_raw(
        json.dumps(header, separators=(",", ":")).encode("utf-8"),
        json.dumps(claims, separators=(",", ":")).encode("utf-8"),
        private_key,
        alg=alg,
    )


def make_token_raw(
    header_bytes: bytes,
    claims_bytes: bytes,
    private_key: Any,
    *,
    alg: str = "EdDSA",
) -> str:
    signing_input = f"{_b64u(header_bytes)}.{_b64u(claims_bytes)}".encode("ascii")
    if alg == "EdDSA":
        signature = private_key.sign(signing_input)
    else:
        # cryptography's EC signer emits DER; JWS canonical is P1363 r||s.
        der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{signing_input.decode('ascii')}.{_b64u(signature)}"


def valid_token(**claim_overrides: Any) -> str:
    claims = dict(VALID_CLAIMS)
    claims.update(claim_overrides)
    return make_token(VALID_HEADER, claims, ED25519_PRIVATE)


def _resigned(header: dict[str, Any], claims: dict[str, Any]) -> str:
    """Token signed by the fixture Ed25519 key with an arbitrary header —
    used to prove that a VALID signature cannot smuggle a claim-set defect
    past the decode-time steps (§5 steps 2-5 run before step 8)."""
    return make_token(header, claims, ED25519_PRIVATE)


# ---------------------------------------------------------------------------
# Offline DNS fixture — values equal the live answers the record's revision
# battery observed (2130706433 / 0x7f000001 / 127.1 / localhost -> 127.0.0.1;
# *.example -> 93.184.216.34, routable, named in the battery harness note).
# ---------------------------------------------------------------------------

_ADDRESS_TABLE = {
    "agents.publisher.example": ["93.184.216.34"],
    "localhost": ["127.0.0.1", "::1"],
    "2130706433": ["127.0.0.1"],
    "0x7f000001": ["127.0.0.1"],
    "127.1": ["127.0.0.1"],
}


def _fake_getaddrinfo(
    host: Any, port: Any = None, *args: Any, **kwargs: Any
) -> list[Any]:
    addresses = _ADDRESS_TABLE.get(str(host).rstrip(".").lower())
    if addresses is None:
        raise socket.gaierror("LEP-0008 fixture DNS: host not in table")
    family = socket.AF_INET6 if ":" in addresses[0] else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (address, 0)) for address in addresses]


class StubResolver:
    """Key-resolution stand-in recording every (iss, kid) resolution — the
    zero-fetch assertions for steps that MUST NOT reach issuer resolution."""

    def __init__(
        self,
        jwks: dict[str, dict[str, Any]] | None = None,
        error: Exception | None = None,
    ):
        self.jwks = jwks if jwks is not None else {ED25519_JWK["kid"]: ED25519_JWK}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def resolve(self, iss: str, kid: str) -> dict[str, Any]:
        self.calls.append((iss, kid))
        if self.error is not None:
            raise self.error
        jwk = self.jwks.get(kid)
        if jwk is None:
            raise JwksDocumentError("no key for kid")
        return jwk


def _compact(envelope: dict[str, Any]) -> bytes:
    return json.dumps(envelope, separators=(",", ":")).encode()


class RecordFixtureDerivationTests(unittest.TestCase):
    """The record sells byte-for-byte reproducibility from the published
    seeds; the fixture here re-derives and pins the published JWK bytes."""

    def test_attestation_ed25519_seed_reproduces_record_jwk(self) -> None:
        self.assertEqual(ED25519_JWK["x"], RECORD_ED25519_X)
        self.assertEqual(
            ED25519_JWK,
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": RECORD_ED25519_X,
                "kid": "attest-key-1",
                "alg": "EdDSA",
            },
        )

    def test_attestation_es256_seed_reproduces_record_jwk(self) -> None:
        self.assertEqual(ES256_JWK["x"], RECORD_ES256_X)
        self.assertEqual(ES256_JWK["y"], RECORD_ES256_Y)
        self.assertEqual(ES256_JWK["kid"], "attest-es256-1")


class AttestationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch(
            "lcp_platform.security.socket.getaddrinfo", new=_fake_getaddrinfo
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # ─── harness ───────────────────────────────────────────────────────────

    def _config(self, **overrides: Any) -> PlatformConfig:
        params: dict[str, Any] = {
            "database_path": Path(":memory:"),
            "schema_root": ROOT / "schemas",
            "platform_id": "platform_001",
            "require_auth": True,
            "allow_insecure_webhooks": True,  # sandbox — must NOT relax JWKS egress (V13/R1)
            "test_mode": True,
        }
        params.update(overrides)
        return PlatformConfig(**params)

    def _platform(
        self,
        *,
        mode: str = "enforce",
        resolver: StubResolver | None = None,
        **config_overrides: Any,
    ) -> tuple[Platform, StubResolver]:
        platform = Platform(self._config(attestation_mode=mode, **config_overrides))
        self.addCleanup(platform.close)
        platform.upsert_credential("pub_123", hmac_secret=PUB_SECRET)
        stub = resolver if resolver is not None else StubResolver()
        platform.attestation.resolver = stub  # type: ignore[assignment]
        return platform, stub

    def _lead(
        self,
        attestation_value: str,
        *,
        lead_id: str,
        sender: str = "pub_123",
        agent_id: str = "ai-assist-001",
        acting_for: str = "pub_123",
        source_type: str = "agent",
    ) -> dict[str, Any]:
        with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
            lead = json.load(handle)
        message = lead["lcp"]["message"]
        message["id"] = str(uuid5(NAMESPACE, lead_id))
        message["test"] = True
        message["idempotency_key"] = f"lep0008-{lead_id}"
        message["sender_id"] = sender
        payload = lead["lcp"]["payload"]
        payload["lead_id"] = lead_id
        payload.pop("expiry", None)
        provenance = payload.setdefault("provenance", {})
        provenance["source_type"] = source_type
        provenance["source_id"] = sender
        if attestation_value:
            provenance["agent"] = {
                "agent_id": agent_id,
                "acting_for": acting_for,
                "attestation": attestation_value,
            }
        else:
            provenance.pop("agent", None)
        return lead

    def _post(
        self, platform: Platform, envelope: dict[str, Any], secret: str = PUB_SECRET
    ) -> tuple[int, dict[str, Any]]:
        message = envelope["lcp"]["message"]
        key = message["idempotency_key"]
        body = _compact(envelope)
        timestamp = now_iso()
        headers = {
            "Content-Type": "application/json",
            "X-LCP-Test": "true",
            "X-LCP-Sender-Id": message["sender_id"],
            "X-LCP-Timestamp": timestamp,
            "X-LCP-Idempotency-Key": key,
            "X-LCP-Signature": signature_for(secret, timestamp, key, body),
        }
        status, _, response = PlatformService(platform).dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )
        return status, response

    def _error(self, response: dict[str, Any]) -> dict[str, Any]:
        return response["errors"][0]

    # ─── V1: free text → 400 LCP-100 (schema-shape class) ─────────────────

    def test_attestation_free_text_rejected(self) -> None:
        platform, stub = self._platform()
        status, response = self._post(
            platform,
            self._lead(
                "trust me, I am a real agent (free text)", lead_id="v1-freetext"
            ),
        )
        self.assertEqual(status, 400)
        error = self._error(response)
        self.assertEqual(error["code"], "LCP-100")
        self.assertIn("provenance/agent/attestation", error["details"][0])
        self.assertIn("does not match", error["details"][0])
        # Shape is the schema layer: the verifier never runs.
        self.assertEqual(stub.calls, [])
        self.assertIsNone(platform.store.get_lead("v1-freetext"))
        # §2 padded precedence (vec gate V21's plat-tier pin): a `=`-padded
        # signature segment is rejected 400 LCP-100 before any verification.
        header, claims, signature = valid_token().split(".")
        padded = f"{header}.{claims}.{signature}="
        status, response = self._post(platform, self._lead(padded, lead_id="v1-padded"))
        self.assertEqual(status, 400)
        self.assertEqual(self._error(response)["code"], "LCP-100")
        self.assertIsNone(platform.store.get_lead("v1-padded"))

    # ─── V2: forged HS256 (the #192 row-2 probe class) ────────────────────

    def test_attestation_forged_hs256_rejected(self) -> None:
        platform, stub = self._platform()
        # #192's probe shape: alg=HS256, iss "fake", agent_id "ai-1", a junk
        # signature segment — and NO aud claim. Step 3 fires before the
        # decode-time presence step could complain (battery ordering proof).
        forged = make_token(
            {"alg": "HS256", "typ": "JWT", "kid": "attest-key-1"},
            {
                "iss": "fake",
                "agent_id": "ai-1",
                "acting_for": "pub_123",
                "iat": 1780272000,
                "exp": 2080000000,
            },
            ED25519_PRIVATE,
        )
        status, response = self._post(platform, self._lead(forged, lead_id="v2-hs256"))
        self.assertEqual(status, 401)
        error = self._error(response)
        self.assertEqual(error["code"], "LCP-002")
        self.assertEqual(error["details"]["reason"], "algorithm")
        self.assertEqual(error["message"], "Agent attestation failed verification")
        # A step-3 failure MUST NOT run issuer resolution (§3).
        self.assertEqual(stub.calls, [])
        self.assertIsNone(platform.store.get_lead("v2-hs256"))

    # ─── V3: expired + claims-contradicting → expired first ───────────────

    def test_attestation_expired_first_axis(self) -> None:
        platform, _ = self._platform()
        token = valid_token(agent_id="someone-else", exp=1000000000)
        status, response = self._post(platform, self._lead(token, lead_id="v3-expired"))
        self.assertEqual(status, 401)
        # Step 9 fires before the step-11 contradiction (deterministic axis
        # order — the record re-proves this in the revision battery).
        self.assertEqual(self._error(response)["details"]["reason"], "expired")

    # ─── V4: cross-receiver replay (invalid D) ────────────────────────────

    def test_attestation_cross_receiver_rejected(self) -> None:
        platform, stub = self._platform()
        # A token minted for platform_002 replayed at platform_001. The
        # signature verifies cryptographically — only the aud binding stops
        # it (§7 cross-pair replay).
        token = valid_token(aud="platform_002")
        status, response = self._post(platform, self._lead(token, lead_id="v4-aud"))
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "audience")
        self.assertEqual(len(stub.calls), 1)  # resolution ran; replay died at 10

    # ─── V5: claims contradict the envelope, both axes ────────────────────

    def test_attestation_claims_mismatch_rejected(self) -> None:
        platform, _ = self._platform()
        status, response = self._post(
            platform,
            self._lead(
                valid_token(agent_id="someone-else"),
                lead_id="v5-agent-axis",
            ),
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "claims_mismatch")

        # acting_for axis: a validly-signed pub_123 assertion presented by a
        # sender that is neither pub_123 nor delegated for it.
        platform.upsert_credential(
            "agent_pub_001", hmac_secret=AGENT_SECRET, scopes=["lead:submit"]
        )
        status, response = self._post(
            platform,
            self._lead(
                valid_token(),
                lead_id="v5-sender-axis",
                sender="agent_pub_001",
                acting_for="pub_123",
            ),
            secret=AGENT_SECRET,
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "claims_mismatch")

    # ─── V6: alg:none / detached signature, both layers ───────────────────

    def test_attestation_alg_none_and_detached_rejected(self) -> None:
        platform, stub = self._platform()
        # Schema-shape layer: the empty third segment violates the §2
        # pattern before any header parsing — 400 LCP-100.
        status, response = self._post(
            platform, self._lead(ALG_NONE_DETACHED, lead_id="v6-detached")
        )
        self.assertEqual(status, 400)
        self.assertEqual(self._error(response)["code"], "LCP-100")
        self.assertIn(
            "provenance/agent/attestation", self._error(response)["details"][0]
        )
        # Verifier layer (belt): an alg:none token that DOES carry a
        # signature segment fails at step 3 — algorithm — 401 LCP-002.
        none_with_sig = ALG_NONE_DETACHED.rsplit(".", 1)[0] + "." + "AAAA"
        status, response = self._post(
            platform, self._lead(none_with_sig, lead_id="v6-none")
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "algorithm")
        self.assertEqual(stub.calls, [])  # neither shape nor step 3 touches keys

    # ─── V7: tampered signature (invalid G) + valid-a accepted ────────────

    def test_attestation_tampered_signature_rejected(self) -> None:
        platform, stub = self._platform()
        # The record's invalid G: valid-a's signature tail replaced under an
        # allowed EdDSA header — the pure forgery case.
        header, claims, signature = valid_token().split(".")
        flipped = (
            signature[:-6] + ("A" if signature[-6] != "A" else "B") + signature[-5:]
        )
        tampered = f"{header}.{claims}.{flipped}"
        status, response = self._post(
            platform, self._lead(tampered, lead_id="v7-tampered")
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "signature")
        self.assertIsNone(platform.store.get_lead("v7-tampered"))
        # A wrong-bytes signature still resolved the key first (step 7 → 8).
        self.assertEqual(stub.calls, [(ISSUER, "attest-key-1")])

        # valid-a accepted end-to-end against the fixture key.
        status, response = self._post(
            platform, self._lead(valid_token(), lead_id="v7-valid")
        )
        self.assertEqual(status, 200, response)
        self.assertEqual(response["lcp"]["payload"]["status"], "RECEIVED")
        self.assertIsNotNone(platform.store.get_lead("v7-valid"))
        self.assertEqual(
            stub.calls, [(ISSUER, "attest-key-1"), (ISSUER, "attest-key-1")]
        )

    # ─── V9: unknown kid; act_for: delegation ─────────────────────────────

    def test_attestation_unknown_kid_rejected(self) -> None:
        platform, stub = self._platform()
        header = dict(VALID_HEADER, kid="attest-key-9")
        token = _resigned(header, VALID_CLAIMS)
        status, response = self._post(platform, self._lead(token, lead_id="v9-kid"))
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "key_unknown")
        self.assertEqual(stub.calls, [(ISSUER, "attest-key-9")])

    def test_act_for_scope_delegation(self) -> None:
        platform, _ = self._platform()
        # The L3-001 topology: the agent submits with its OWN credential,
        # delegation registered as an act_for: scope string (auth.py
        # has_scope machinery — not a wire field).
        platform.upsert_credential(
            "agent_pub_001",
            hmac_secret=AGENT_SECRET,
            scopes=["lead:submit", "act_for:pub_123"],
        )
        token = valid_token()
        status, response = self._post(
            platform,
            self._lead(token, lead_id="v9-delegated", sender="agent_pub_001"),
            secret=AGENT_SECRET,
        )
        self.assertEqual(status, 200, response)

        # The same token submitted by the SAME agent credential WITHOUT the
        # scope fails claims_mismatch (record: "verifies iff the delegation
        # was registered").
        platform.upsert_credential(
            "agent_pub_002",
            hmac_secret=AGENT_SECRET + "-2",
            scopes=["lead:submit"],
        )
        status, response = self._post(
            platform,
            self._lead(token, lead_id="v9-undelegated", sender="agent_pub_002"),
            secret=AGENT_SECRET + "-2",
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "claims_mismatch")

    # ─── V10: issuer availability failure fails closed ────────────────────

    def test_attestation_jwks_unavailable_fails_closed(self) -> None:
        def _transport_down(url: str, timeout: float, max_bytes: int):
            raise KeyUnavailableError("dns/connect failure (fixture)")

        platform, _ = self._platform()
        platform.attestation.resolver = JwksResolver(
            platform.config, fetch=_transport_down
        )
        status, response = self._post(
            platform, self._lead(valid_token(), lead_id="v10-down")
        )
        self.assertEqual(status, 500)
        error = self._error(response)
        self.assertEqual(error["code"], "LCP-500")
        self.assertEqual(error["details"]["reason"], "key_unavailable")
        # Fail-closed: no lead row, no PII persisted.
        self.assertIsNone(platform.store.get_lead("v10-down"))

        # 5xx is the availability class (§8 revision F4).
        platform_up5, _ = self._platform()
        resolver5xx = JwksResolver(
            platform_up5.config,
            fetch=lambda url, timeout, max_bytes: FetchedDocument(
                status=503, headers={}, body=b"unavailable"
            ),
        )
        platform_up5.attestation.resolver = resolver5xx
        status, response = self._post(
            platform_up5, self._lead(valid_token(), lead_id="v10-5xx")
        )
        self.assertEqual(status, 500)
        self.assertEqual(self._error(response)["details"]["reason"], "key_unavailable")
        self.assertIsNone(platform_up5.store.get_lead("v10-5xx"))

        # Observe mode: availability failure logs only — accept path holds.
        observe, _ = self._platform(mode="observe")
        observe.attestation.resolver = JwksResolver(
            observe.config, fetch=_transport_down
        )
        status, response = self._post(
            observe, self._lead(valid_token(), lead_id="v10-observe")
        )
        self.assertEqual(status, 200, response)
        rows = [
            row
            for row in observe.store.list_audit_events("lead", "v10-observe")
            if row["action"] == "attestation.observe"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metadata"]["reason"], "key_unavailable")

    # ─── V13: obfuscated loopback plants, sandbox included ────────────────

    def test_attestation_iss_obfuscated_loopback_rejected(self) -> None:
        # The harness config IS the sandbox escape the re-dossier named:
        # test_mode=True + allow_insecure_webhooks=True. The resolve leg must
        # not inherit the shipped class's skip — every one of these iss hosts
        # rejects at step 6 with reason issuer_policy and ZERO fetch issued.
        platform, stub = self._platform()
        plants = [
            "https://127.0.0.1/attestation",
            "https://localhost/attestation",
            "https://2130706433/attestation",
            "https://0x7f000001/attestation",
            "https://127.1/attestation",
            "https://[::ffff:127.0.0.1]/attestation",
        ]
        for index, iss in enumerate(plants):
            token = valid_token(iss=iss)
            status, response = self._post(
                platform, self._lead(token, lead_id=f"v13-{index}")
            )
            with self.subTest(iss=iss):
                self.assertEqual(status, 401)
                self.assertEqual(
                    self._error(response)["details"]["reason"], "issuer_policy"
                )
                self.assertIsNone(platform.store.get_lead(f"v13-{index}"))
        self.assertEqual(stub.calls, [])  # not one fetch, ever
        # HTTPS-only in any mode: the http scheme fails even in the sandbox
        # that ships the allow_insecure_webhooks escape for webhooks.
        status, response = self._post(
            platform,
            self._lead(
                valid_token(iss="http://agents.publisher.example/attestation"),
                lead_id="v13-http",
            ),
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "issuer_policy")

    # ─── V14: no redirects, no proxy/env interception ─────────────────────

    def test_attestation_jwks_fetch_no_redirects_no_proxy_env(self) -> None:
        requests: list[tuple[str, str]] = []
        clients: list[dict[str, Any]] = []
        location = "http://127.0.0.1:1/jwks.json"

        class FakeResponse:
            status_code = 302
            headers = {"Location": location}

            def iter_bytes(self):
                yield b"moved"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class FakeClient:
            def __init__(self, **kwargs):
                clients.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def stream(self, method, url, **kwargs):
                requests.append((method, url))
                return FakeResponse()

        platform, _ = self._platform()
        platform.attestation.resolver = JwksResolver(platform.config)
        with (
            mock.patch.dict(
                os.environ,
                {
                    "HTTPS_PROXY": "http://proxy.invalid:9",
                    "HTTP_PROXY": "http://proxy.invalid:9",
                },
            ),
            mock.patch.object(attestations.httpx, "Client", FakeClient),
        ):
            status, response = self._post(
                platform, self._lead(valid_token(), lead_id="v14-redirect")
            )
        self.assertEqual(status, 401)
        # A 3xx from a policy-valid iss is the invalid-response path — never
        # a second fetch of Location (key_unknown, NOT a followed redirect).
        self.assertEqual(self._error(response)["details"]["reason"], "key_unknown")
        self.assertEqual(requests, [("GET", ISSUER + "/.well-known/jwks.json")])
        self.assertNotIn(location, [url for _, url in requests])
        self.assertEqual(len(clients), 1)
        self.assertIs(clients[0]["follow_redirects"], False)
        self.assertIs(clients[0]["trust_env"], False)
        self.assertLessEqual(clients[0]["timeout"], 2.0)

    # ─── V15: decode-time presence/type/format, zero resolution/crypto ────

    def test_attestation_decode_time_type_presence_format(self) -> None:
        platform, stub = self._platform()
        verifier = platform.attestation
        crypto_calls: list[str] = []
        original = attestations._verify_signature

        def spy(alg, jwk, signing_input, signature):  # pragma: no cover - spy
            crypto_calls.append(alg)
            return original(alg, jwk, signing_input, signature)

        def valid_signed_raw(claims_bytes: bytes) -> str:
            return make_token_raw(
                json.dumps(VALID_HEADER, separators=(",", ":")).encode("utf-8"),
                claims_bytes,
                ED25519_PRIVATE,
            )

        base = dict(VALID_CLAIMS)
        cases: dict[str, str] = {
            # string timestamps (the L3-001 defect shape; vec V16's class)
            "string-exp": json.dumps(
                {**base, "exp": "2080000000"}, separators=(",", ":")
            ),
            "string-iat": json.dumps(
                {**base, "iat": "2026-08-15T12:00:00Z"}, separators=(",", ":")
            ),
            # zero / absent
            "zero-exp": json.dumps({**base, "exp": 0}, separators=(",", ":")),
            "absent-exp": json.dumps(
                {key: value for key, value in base.items() if key != "exp"},
                separators=(",", ":"),
            ),
            "absent-iat": json.dumps(
                {key: value for key, value in base.items() if key != "iat"},
                separators=(",", ":"),
            ),
            # float (fraction)
            "float-iat": json.dumps(
                {**base, "iat": 1780272000.5}, separators=(",", ":")
            ),
            # exponent notation judged on BYTES (re-dossier R3) — 2e9 parses
            # integer-valued in IEEE-double parsers and MUST still fail.
            "exponent-iat": '{"iss":"%s","aud":"platform_001","agent_id":"ai-assist-001","acting_for":"pub_123","iat":1e9,"exp":2e9}'
            % ISSUER,
            "exponent-exp": '{"iss":"%s","aud":"platform_001","agent_id":"ai-assist-001","acting_for":"pub_123","iat":1780272000,"exp":2e9}'
            % ISSUER,
            # absent MUST claims
            "absent-aud": json.dumps(
                {key: value for key, value in base.items() if key != "aud"},
                separators=(",", ":"),
            ),
            "absent-agent-id": json.dumps(
                {key: value for key, value in base.items() if key != "agent_id"},
                separators=(",", ":"),
            ),
            "absent-acting-for": json.dumps(
                {key: value for key, value in base.items() if key != "acting_for"},
                separators=(",", ":"),
            ),
            "empty-string-aud": json.dumps({**base, "aud": ""}, separators=(",", ":")),
            # non-object claims serialization
            "non-object-claims": "[1,2,3]",
            # duplicate names (RFC 8259 §5 rejection, no last-wins)
            "duplicate-aud": '{"iss":"%s","aud":"platform_001","aud":"platform_002","agent_id":"ai-assist-001","acting_for":"pub_123","iat":1780272000,"exp":2080000000}'
            % ISSUER,
            # duplicate at nesting depth 2 (re-dossier R2) — validly signed
            "duplicate-nested": '{"iss":"%s","aud":"platform_001","agent_id":"ai-assist-001","acting_for":"pub_123","iat":1780272000,"exp":2080000000,"meta":{"tier":"gold","tier":"platinum"}}'
            % ISSUER,
        }
        with mock.patch.object(attestations, "_verify_signature", spy):
            for name, claims_bytes_text in cases.items():
                with self.subTest(case=name):
                    token = valid_signed_raw(claims_bytes_text.encode("utf-8"))
                    with self.assertRaises(AttestationFailure) as caught:
                        verifier.verify(
                            token,
                            receiver_id="platform_001",
                            envelope_agent_id="ai-assist-001",
                            sender_id="pub_123",
                            scopes=lambda _scope: False,
                        )
                    self.assertEqual(caught.exception.reason, "format")
                    # Every case is decided at step 2/4: no key resolution,
                    # no crypto work (re-dossier R2: a valid signature cannot
                    # smuggle a malformed claim set).
                    self.assertEqual(stub.calls, [])
        self.assertEqual(crypto_calls, [])

        # Wire mapping for one representative case: 401 LCP-002 format.
        status, response = self._post(
            platform,
            self._lead(
                valid_signed_raw(
                    json.dumps(
                        {**base, "exp": "2080000000"}, separators=(",", ":")
                    ).encode("utf-8")
                ),
                lead_id="v15-wire",
            ),
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "format")

    # ─── V17: ES256 DER-in-slot rejected; P1363 verifies ──────────────────

    def test_attestation_es256_der_rejected(self) -> None:
        platform, stub = self._platform(
            resolver=StubResolver(
                jwks={ED25519_JWK["kid"]: ED25519_JWK, ES256_JWK["kid"]: ES256_JWK}
            )
        )
        header = {"alg": "ES256", "typ": "JWT", "kid": "attest-es256-1"}
        p1363 = make_token(header, VALID_CLAIMS, ES256_PRIVATE, alg="ES256")
        signing_input = ".".join(p1363.split(".")[:2]).encode("ascii")
        der = ES256_PRIVATE.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        self.assertNotEqual(len(der), 64)  # DER is the variable-width fork
        der_token = f"{signing_input.decode('ascii')}.{_b64u(der)}"

        # P1363 form verifies (§3 encoding norm; lcp-pos-attestation-es256-001
        # mirrors this at the vector tier).
        status, response = self._post(platform, self._lead(p1363, lead_id="v17-p1363"))
        self.assertEqual(status, 200, response)

        # The same claim set with the DER bytes in the signature slot fails
        # at the step-8 width precheck with `format` (not `signature`).
        status, response = self._post(
            platform, self._lead(der_token, lead_id="v17-der")
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "format")
        self.assertEqual(
            [kid for _, kid in stub.calls], ["attest-es256-1", "attest-es256-1"]
        )

    # ─── V18: fetched-but-invalid documents are 401, never 500 ────────────

    def test_attestation_jwks_oversize_is_401(self) -> None:
        cases = {
            "oversize": FetchedDocument(
                status=200,
                headers={},
                body=json.dumps(
                    {"keys": [{"kty": "OKP", "kid": "attest-key-1", "x": "A" * 20_000}]}
                ).encode("utf-8"),
            ),
            "too-many-keys": FetchedDocument(
                status=200,
                headers={},
                body=json.dumps(
                    {
                        "keys": [
                            {"kty": "OKP", "kid": f"k{index}"} for index in range(33)
                        ]
                    }
                ).encode("utf-8"),
            ),
            "non-json": FetchedDocument(
                status=200, headers={}, body=b"<html>issuer misconfiguration</html>"
            ),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                platform, _ = self._platform()
                platform.attestation.resolver = JwksResolver(
                    platform.config,
                    fetch=lambda url, timeout, max_bytes, doc=document: doc,
                )
                status, response = self._post(
                    platform, self._lead(valid_token(), lead_id=f"v18-{name}")
                )
                self.assertEqual(status, 401)
                self.assertEqual(self._error(response)["code"], "LCP-002")
                self.assertEqual(
                    self._error(response)["details"]["reason"], "key_unknown"
                )
                self.assertIsNone(platform.store.get_lead(f"v18-{name}"))

        # 4xx (404 on the /.well-known path) joins the invalid-document class.
        platform, _ = self._platform()
        platform.attestation.resolver = JwksResolver(
            platform.config,
            fetch=lambda url, timeout, max_bytes: FetchedDocument(
                status=404, headers={}, body=b"not found"
            ),
        )
        status, response = self._post(
            platform, self._lead(valid_token(), lead_id="v18-404")
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "key_unknown")

    # ─── V19: duplicate kid under §4 option A ─────────────────────────────

    def test_attestation_jwks_duplicate_kid_rejected(self) -> None:
        platform, _ = self._platform()
        good = dict(ED25519_JWK)
        rival = dict(ED25519_JWK)
        rival["x"] = _b64u(b"\x00" * 32)  # same kid, different material
        document = FetchedDocument(
            status=200,
            headers={},
            body=json.dumps({"keys": [good, rival]}).encode("utf-8"),
        )
        platform.attestation.resolver = JwksResolver(
            platform.config,
            fetch=lambda url, timeout, max_bytes: document,
        )
        status, response = self._post(
            platform, self._lead(valid_token(), lead_id="v19-dupkid")
        )
        self.assertEqual(status, 401)
        # Option A (maintainer decision): two JWKs sharing a kid make the
        # WHOLE document invalid — no first/last-wins selection ever runs.
        self.assertEqual(self._error(response)["details"]["reason"], "key_unknown")

    # ─── V20: the DUPLICATE ack never re-verifies or refetches ────────────

    def test_attestation_duplicate_ack_never_refetches(self) -> None:
        platform, stub = self._platform()
        token = valid_token()
        lead = self._lead(token, lead_id="v20-accepted")
        status, response = self._post(platform, lead)
        self.assertEqual(status, 200)
        self.assertEqual(len(stub.calls), 1)

        verify_calls: list[int] = []
        original_verify = platform.attestation.verify

        def counting_verify(*args: Any, **kwargs: Any) -> Any:
            verify_calls.append(1)
            return original_verify(*args, **kwargs)

        platform.attestation.verify = counting_verify  # type: ignore[method-assign]

        # Replays of an already-accepted envelope: DUPLICATE ack, zero
        # verifier work, zero JWKS fetches (§5 placement, §7 "one
        # verification").
        status, response = self._post(platform, copy.deepcopy(lead))
        self.assertEqual(status, 200)
        self.assertEqual(response["lcp"]["payload"]["status"], "DUPLICATE")
        self.assertEqual(verify_calls, [])
        self.assertEqual(len(stub.calls), 1)

        # Idempotency-key collision with a DIFFERENT body fails on the
        # idempotency axis without touching the issuer or the verifier.
        conflicting = self._lead(token, lead_id="v20-other-lead")
        conflicting["lcp"]["message"]["idempotency_key"] = lead["lcp"]["message"][
            "idempotency_key"
        ]
        status, response = self._post(platform, conflicting)
        self.assertEqual(status, 409)
        self.assertEqual(self._error(response)["code"], "LCP-005")
        self.assertEqual(verify_calls, [])
        self.assertEqual(len(stub.calls), 1)

    # ─── V22: skew cap at configuration time + equality boundary ──────────

    def test_attestation_skew_config_capped_at_30(self) -> None:
        # A deployment configuration with skew > 30 s is REFUSED at startup
        # (the §7 cap is a MUST, not advisory).
        with self.assertRaises(ValueError):
            self._config(attestation_clock_skew_seconds=31)
        with self.assertRaises(ValueError):
            self._config(attestation_clock_skew_seconds=-1)
        self._config(attestation_clock_skew_seconds=30)  # ceiling accepted

        expiry = 2080000000
        claims = {**VALID_CLAIMS, "exp": expiry}

        def probe(skew: int, now: float) -> str | None:
            verifier = AgentAttestationVerifier(
                self._config(attestation_clock_skew_seconds=skew),
                resolver=StubResolver(),
                clock=lambda: now,
            )
            try:
                verifier.verify(
                    make_token(VALID_HEADER, claims, ED25519_PRIVATE),
                    receiver_id="platform_001",
                    envelope_agent_id="ai-assist-001",
                    sender_id="pub_123",
                    scopes=lambda _scope: False,
                )
            except AttestationFailure as failure:
                return failure.reason
            return None

        self.assertEqual(probe(30, expiry + 30.0), "expired")  # reject AT equality
        self.assertIsNone(probe(30, expiry + 30.0 - 1))  # accept one second earlier
        self.assertEqual(
            probe(0, float(expiry)), "expired"
        )  # §7 asymmetry vs transport
        self.assertIsNone(probe(0, float(expiry - 1)))

    # ─── V23: frozen audit schema, no token bytes anywhere ────────────────

    def test_history_projection_never_emits_attestation_bytes(self) -> None:
        platform, _ = self._platform()
        accepted = valid_token()
        header, claims, signature = accepted.split(".")
        flipped = (
            signature[:-6] + ("A" if signature[-6] != "A" else "B") + signature[-5:]
        )
        rejected = f"{header}.{claims}.{flipped}"

        captured: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(self.format(record))

        handler = Capture(level=logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(name)s %(message)s"))
        root = logging.getLogger()
        root.addHandler(handler)
        previous_level = root.level
        root.setLevel(logging.DEBUG)
        self.addCleanup(lambda: root.removeHandler(handler))
        self.addCleanup(root.setLevel, previous_level)

        self.assertEqual(
            self._post(platform, self._lead(accepted, lead_id="v23-accept"))[0], 200
        )
        status, response = self._post(
            platform, self._lead(rejected, lead_id="v23-reject")
        )
        self.assertEqual(status, 401)
        # The error body carries the reason, never the token.
        serialized_error = json.dumps(response)
        for needle in (accepted, header, claims, signature, rejected):
            self.assertNotIn(needle, serialized_error)

        frozen_keys = {"outcome", "reason", "iss_host", "kid", "tok_sha256_16"}
        rows = []
        for lead_id in ("v23-accept", "v23-reject"):
            attestation_rows = [
                row
                for row in platform.store.list_audit_events("lead", lead_id)
                if row["action"].startswith("attestation.")
            ]
            self.assertEqual(len(attestation_rows), 1, lead_id)
            rows.extend(attestation_rows)

        expected_digest = hashlib.sha256(accepted.encode("utf-8")).hexdigest()[:16]
        rejected_digest = hashlib.sha256(rejected.encode("utf-8")).hexdigest()[:16]
        for row in rows:
            metadata = row["metadata"]
            # The row carries exactly the frozen attestation keys plus the
            # store's cross-cutting request_id join — nothing attestation-
            # derived is admissible beyond the schema.
            self.assertLessEqual(set(metadata), frozen_keys | {"request_id"})
            self.assertEqual(set(metadata) & frozen_keys, frozen_keys)
            self.assertIn(metadata["outcome"], {"accept", "reject", "observe"})
            self.assertTrue(
                metadata["reason"] is None or metadata["reason"] in attestations.REASONS
            )
            self.assertEqual(row["action"], f"attestation.{metadata['outcome']}")
            self.assertIn(metadata["tok_sha256_16"], (expected_digest, rejected_digest))
            self.assertEqual(metadata["iss_host"], "agents.publisher.example")
            self.assertEqual(metadata["kid"], "attest-key-1")
            serialized = json.dumps(row)
            for needle in (accepted, header, claims, signature, rejected):
                self.assertNotIn(needle, serialized)
        # No log line emitted during either ingest carries token bytes.
        for line in captured:
            for needle in (accepted, rejected, header, claims):
                self.assertNotIn(needle, line)
        # Metrics/read surfaces carry nothing attestation-derived beyond the
        # store's generic counters.
        self.assertNotIn(accepted, json.dumps(platform.store.metrics()))

    # ─── V24: receiver-side header policy ─────────────────────────────────

    def test_attestation_header_member_policy(self) -> None:
        platform, stub = self._platform()

        # typ is OPTIONAL and never decides: JWT and at+jwt variants of
        # identical claims verify identically (RFC 8725-style coexistence).
        for index, typ in enumerate(("JWT", "at+jwt")):
            header = {"alg": "EdDSA", "typ": typ, "kid": "attest-key-1"}
            status, response = self._post(
                platform,
                self._lead(_resigned(header, VALID_CLAIMS), lead_id=f"v24-typ-{index}"),
            )
            self.assertEqual(status, 200, (typ, response))
        # A token with NO typ at all also verifies (optional on receipt).
        status, _ = self._post(
            platform,
            self._lead(
                _resigned({"alg": "EdDSA", "kid": "attest-key-1"}, VALID_CLAIMS),
                lead_id="v24-typ-absent",
            ),
        )
        self.assertEqual(status, 200)

        # Unknown non-crit members (x5u/jku) are ignored AND never
        # dereferenced: the stub records every resolution/fetch attempt, so a
        # library-side dereference would show up as an extra call or an
        # unexpected iss value.
        deref_url = "https://localhost/.well-known/x5u"
        header = {
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": "attest-key-1",
            "x5u": deref_url,
            "jku": deref_url,
        }
        calls_before = len(stub.calls)
        status, _ = self._post(
            platform, self._lead(_resigned(header, VALID_CLAIMS), lead_id="v24-x5u")
        )
        self.assertEqual(status, 200)
        new_calls = stub.calls[calls_before:]
        self.assertEqual(new_calls, [(ISSUER, "attest-key-1")])  # exactly the iss path
        self.assertTrue(all(iss == ISSUER for iss, _ in new_calls))

        # crit names an extension this profile does not understand: step-2
        # format failure, BEFORE any key work.
        calls_before = len(stub.calls)
        header = {"alg": "EdDSA", "typ": "JWT", "kid": "attest-key-1", "crit": ["b64"]}
        status, response = self._post(
            platform, self._lead(_resigned(header, VALID_CLAIMS), lead_id="v24-crit")
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "format")
        self.assertEqual(stub.calls[calls_before:], [])

        # b64 headers are non-conformant (§2) — same step-2 class.
        header = {"alg": "EdDSA", "typ": "JWT", "kid": "attest-key-1", "b64": True}
        status, response = self._post(
            platform, self._lead(_resigned(header, VALID_CLAIMS), lead_id="v24-b64")
        )
        self.assertEqual(status, 401)
        self.assertEqual(self._error(response)["details"]["reason"], "format")

    # ─── Mode contract: observe verifies + audits + accepts ───────────────

    def test_attestation_observe_enforce_parity(self) -> None:
        cases = {
            "free-text": ("trust me, I am a real agent (free text)", "format", 400),
            "hs256": (
                make_token(
                    {"alg": "HS256", "typ": "JWT", "kid": "attest-key-1"},
                    {
                        "iss": "fake",
                        "agent_id": "ai-1",
                        "aud": "platform_001",
                        "acting_for": "pub_123",
                        "iat": 1780272000,
                        "exp": 2080000000,
                    },
                    ED25519_PRIVATE,
                ),
                "algorithm",
                401,
            ),
            "expired": (valid_token(exp=1000000000), "expired", 401),
            "audience": (valid_token(aud="platform_002"), "audience", 401),
            "key-unknown": (
                _resigned(dict(VALID_HEADER, kid="attest-key-9"), VALID_CLAIMS),
                "key_unknown",
                401,
            ),
            "issuer-policy": (
                valid_token(iss="https://127.0.0.1/attestation"),
                "issuer_policy",
                401,
            ),
        }
        for name, (token, reason, enforce_status) in cases.items():
            with self.subTest(case=name):
                enforce, _ = self._platform()
                status, response = self._post(
                    enforce, self._lead(token, lead_id=f"parity-{name}-enforce")
                )
                self.assertEqual(status, enforce_status)
                if enforce_status == 401:
                    self.assertEqual(self._error(response)["details"]["reason"], reason)
                else:
                    self.assertEqual(self._error(response)["code"], "LCP-100")

                observe, _ = self._platform(mode="observe")
                status, response = self._post(
                    observe, self._lead(token, lead_id=f"parity-{name}-observe")
                )
                # Observe: zero new rejections on the wire...
                self.assertEqual(status, 200)
                self.assertEqual(response["lcp"]["payload"]["status"], "RECEIVED")
                self.assertIsNotNone(observe.store.get_lead(f"parity-{name}-observe"))
                # ...but the FULL procedure outcome is recorded with the same
                # reason the enforce path rejected on (parity of verdicts,
                # difference of consequence).
                rows = [
                    row
                    for row in observe.store.list_audit_events(
                        "lead", f"parity-{name}-observe"
                    )
                    if row["action"] == "attestation.observe"
                ]
                self.assertEqual(len(rows), 1)
                observed_reason = rows[0]["metadata"]["reason"]
                self.assertEqual(observed_reason, reason)
                self.assertEqual(rows[0]["metadata"]["outcome"], "observe")

    # ─── §5: the procedure does not run for non-agent traffic ─────────────

    def test_attestation_non_agent_lead_skips_procedure(self) -> None:
        platform, stub = self._platform()
        # A publisher-source lead (no agent block) ingests exactly as before.
        status, _ = self._post(
            platform,
            self._lead("", lead_id="skip-no-agent", source_type="publisher"),
        )
        self.assertEqual(status, 200)
        self.assertEqual(stub.calls, [])
        # An agent-source lead whose attestation is absent is still the
        # schema presence class (core.json required) — LCP-100 via schema.
        lead = self._lead("x", lead_id="skip-absent")
        del lead["lcp"]["payload"]["provenance"]["agent"]["attestation"]
        status, response = self._post(platform, lead)
        self.assertEqual(status, 400)
        self.assertIn("attestation", json.dumps(response))
        self.assertEqual(stub.calls, [])


if __name__ == "__main__":
    unittest.main()

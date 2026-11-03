"""LEP-0008 JwksResolver unit gates (supports record gates V14/V18/V19/V20).

Cache and egress-view semantics that the platform-tier gate suite exercises
through ingest are pinned directly here: TTL windows (cache-control/max-age,
the configured ceiling, the 24 h hard cap), the <= 30 s negative cache for
fetch failures, per-document selection sanity, and the strict egress config
view (HTTPS-only + resolve-aware in every mode). Deterministic: injected
clock + fake transport; no socket connect, no httpx, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
import socket
from typing import Any
import unittest
from unittest import mock

from lcp_platform.attestation import (
    FetchedDocument,
    JwksDocumentError,
    JwksResolver,
    KeyUnavailableError,
    validate_issuer_egress,
)
from lcp_platform.config import PlatformConfig
from lcp_platform.security import SecurityPolicyError

ISSUER = "https://agents.publisher.example/attestation"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
JWK: dict[str, Any] = {
    "kty": "OKP",
    "crv": "Ed25519",
    "x": "1bCirUUETlbD2Z3ZNcYH95IskGacAJdPgSxxLRswlbY",
    "kid": "attest-key-1",
    "alg": "EdDSA",
}


class RecordingFetch:
    """Fake JWKS transport: counts calls, replays scripted documents."""

    def __init__(
        self,
        document: FetchedDocument | Exception,
        headers: dict[str, str] | None = None,
    ):
        self.calls: list[str] = []
        self._document = document
        self._headers = headers or {}

    @classmethod
    def keys(cls, keys: list[dict[str, Any]], headers: dict[str, str] | None = None):
        body = json.dumps({"keys": keys}).encode("utf-8")
        return cls(FetchedDocument(status=200, headers=headers or {}, body=body))

    def __call__(self, url: str, timeout: float, max_bytes: int) -> FetchedDocument:
        self.calls.append(url)
        if isinstance(self._document, Exception):
            raise self._document
        return self._document


def _config(**overrides: Any) -> PlatformConfig:
    params: dict[str, Any] = {
        "database_path": Path(":memory:"),
        "test_mode": True,
        "allow_insecure_webhooks": True,  # sandbox — must not leak into JWKS egress
    }
    params.update(overrides)
    return PlatformConfig(**params)


def _patch_dns(table: dict[str, list[str]]):
    def fake(host: Any, *args: Any, **kwargs: Any) -> list[Any]:
        addresses = table.get(str(host).rstrip(".").lower())
        if addresses is None:
            raise socket.gaierror(str(host))
        family = socket.AF_INET6 if ":" in addresses[0] else socket.AF_INET
        return [
            (family, socket.SOCK_STREAM, 0, "", (address, 0)) for address in addresses
        ]

    return mock.patch("lcp_platform.security.socket.getaddrinfo", new=fake)


_ROUTABLE_DNS = {"agents.publisher.example": ["93.184.216.34"]}


class ResolverCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = _patch_dns(_ROUTABLE_DNS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_document_is_cached_until_ttl(self) -> None:
        now = [1_000.0]
        fetch = RecordingFetch.keys([JWK])
        resolver = JwksResolver(_config(), fetch=fetch, clock=lambda: now[0])
        self.assertEqual(
            resolver.resolve(ISSUER, "attest-key-1")["kid"], "attest-key-1"
        )
        self.assertEqual(fetch.calls, [JWKS_URL])
        # Inside the TTL: zero additional fetches (§4 Cache).
        now[0] += 60.0
        resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 1)
        # Past the configured TTL (3600 s default): refetch.
        now[0] += 3_600.0
        resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 2)

    def test_cache_control_max_age_is_honored(self) -> None:
        now = [0.0]
        fetch = RecordingFetch.keys(
            [JWK], headers={"cache-control": "public, max-age=60"}
        )
        resolver = JwksResolver(
            _config(attestation_jwks_cache_ttl_seconds=3600.0),
            fetch=fetch,
            clock=lambda: now[0],
        )
        resolver.resolve(ISSUER, "attest-key-1")
        now[0] = 59.0
        resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 1)
        now[0] = 61.0
        resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 2)

    def test_cache_age_capped_at_24h_hard_bound(self) -> None:
        now = [0.0]
        fetch = RecordingFetch.keys(
            [JWK], headers={"cache-control": "max-age=999999999"}
        )
        resolver = JwksResolver(_config(), fetch=fetch, clock=lambda: now[0])
        resolver.resolve(ISSUER, "attest-key-1")
        now[0] = 86_400.0 + 1  # past the 24 h hard cap, not the absurd max-age
        resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 2)

    def test_fetch_failure_negative_cached_at_most_30s(self) -> None:
        now = [0.0]
        fetch = RecordingFetch(KeyUnavailableError("dns fixture"))
        resolver = JwksResolver(_config(), fetch=fetch, clock=lambda: now[0])
        with self.assertRaises(KeyUnavailableError):
            resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 1)
        # Within the negative-cache window: fail closed WITHOUT refetching.
        now[0] = 29.0
        with self.assertRaises(KeyUnavailableError):
            resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 1)
        # Past 30 s the fetch is attempted again.
        now[0] = 31.0
        with self.assertRaises(KeyUnavailableError):
            resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 2)

    def test_5xx_is_availability_and_negative_cached(self) -> None:
        now = [0.0]
        fetch = RecordingFetch(FetchedDocument(status=503, headers={}, body=b"down"))
        resolver = JwksResolver(_config(), fetch=fetch, clock=lambda: now[0])
        with self.assertRaises(KeyUnavailableError):
            resolver.resolve(ISSUER, "attest-key-1")
        now[0] = 10.0
        with self.assertRaises(KeyUnavailableError):
            resolver.resolve(ISSUER, "attest-key-1")
        self.assertEqual(len(fetch.calls), 1)  # the window suppresses the retry fetch


class DocumentSanityTests(unittest.TestCase):
    """§4 Selection/Bounds decisions that live in the document parser."""

    def setUp(self) -> None:
        patcher = _patch_dns(_ROUTABLE_DNS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _resolve_with(self, keys: list[dict[str, Any]]) -> dict[str, Any]:
        resolver = JwksResolver(_config(), fetch=RecordingFetch.keys(keys))
        return resolver.resolve(ISSUER, "attest-key-1")

    def test_duplicate_kid_invalidates_document_option_a(self) -> None:
        # §4 option A (maintainer decision): first-wins/last-wins forks give
        # opposite answers for the same token — the whole document is invalid.
        with self.assertRaises(JwksDocumentError):
            self._resolve_with([dict(JWK), dict(JWK, x="AAAA")])

    def test_enc_key_is_invalid_document_class(self) -> None:
        with self.assertRaises(JwksDocumentError):
            self._resolve_with([dict(JWK, use="enc")])

    def test_key_ops_without_verify_is_invalid_document(self) -> None:
        with self.assertRaises(JwksDocumentError):
            self._resolve_with([dict(JWK, key_ops=["encrypt"])])

    def test_missing_kid_entry_is_invalid_document(self) -> None:
        with self.assertRaises(JwksDocumentError):
            self._resolve_with([{"kty": "OKP"}])

    def test_absent_kid_in_valid_document_is_key_unknown(self) -> None:
        with self.assertRaises(JwksDocumentError):
            self._resolve_with([dict(JWK, kid="other-key")])


class EgressViewTests(unittest.TestCase):
    """The strict config view — the re-dossier R1 MUST: sandbox flags do
    not relax JWKS egress, in either leg."""

    def test_http_issuer_rejected_even_in_sandbox(self) -> None:
        config = _config()  # test_mode + allow_insecure_webhooks: sandbox
        self.assertTrue(config.allow_insecure_webhooks)
        with self.assertRaises(SecurityPolicyError):
            validate_issuer_egress(
                "http://agents.publisher.example/attestation", config
            )

    def test_user_info_issuer_rejected(self) -> None:
        with self.assertRaises(SecurityPolicyError):
            validate_issuer_egress(
                "https://user:password@agents.publisher.example/attestation", _config()
            )

    def test_literal_loopback_rejected_pre_resolution(self) -> None:
        with self.assertRaises(SecurityPolicyError):
            validate_issuer_egress("https://127.0.0.1/attestation", _config())

    def test_resolve_leg_runs_in_sandbox(self) -> None:
        # localhost under allow_insecure_webhooks=True: the shipped class
        # would skip getaddrinfo; the strict view must NOT — and it rejects
        # the resolved 127.0.0.1.
        patcher = _patch_dns({"localhost": ["127.0.0.1", "::1"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.assertRaises(SecurityPolicyError):
            validate_issuer_egress("https://localhost/attestation", _config())


if __name__ == "__main__":
    unittest.main()

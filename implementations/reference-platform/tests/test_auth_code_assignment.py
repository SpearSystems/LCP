"""SPEC §5 auth-family code assignment (issue #143).

Pins the conditions §5 now assigns normatively: HMAC header presence,
timestamp parse/awareness, the inclusive and symmetric replay window, the
mutating-route idempotency header, header/envelope idempotency equality, and
the identity/authorization axis that reuses `LCP-002`.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from lcp_platform.auth import (
    AuthenticationError,
    _timestamp_is_fresh,
    signature_for,
)
from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService

ROOT = Path(__file__).resolve().parents[3]
SECRET = "auth-code-secret"
WINDOW = 300


def _ts(offset_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _lead(idempotency_key: str) -> dict:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        lead = json.load(handle)
    message = lead["lcp"]["message"]
    payload = lead["lcp"]["payload"]
    message["test"] = True
    message["id"] = str(uuid4())
    message["idempotency_key"] = idempotency_key
    payload["lead_id"] = f"auth-code-{uuid4().hex[:8]}"
    payload.pop("expiry", None)
    return lead


def _signed(
    envelope: dict,
    *,
    timestamp: str | None = None,
    drop: tuple[str, ...] = (),
    key: str | None = None,
) -> tuple[dict[str, str], bytes]:
    body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode()
    message = envelope["lcp"]["message"]
    timestamp = timestamp or _ts(0)
    idempotency_key = key if key is not None else message["idempotency_key"]
    headers = {
        "Content-Type": "application/json",
        "X-LCP-Test": "true",
        "X-LCP-Sender-Id": message["sender_id"],
        "X-LCP-Timestamp": timestamp,
        "X-LCP-Idempotency-Key": idempotency_key,
        "X-LCP-Signature": signature_for(SECRET, timestamp, idempotency_key, body),
    }
    for name in drop:
        headers.pop(name)
    return headers, body


class AuthCodeAssignmentTests(unittest.TestCase):
    def _platform(self, **overrides: object) -> Platform:
        config = PlatformConfig(
            database_path=Path(":memory:"),
            schema_root=ROOT / "schemas",
            platform_id="platform_001",
            require_auth=True,
            replay_window_seconds=WINDOW,
            allow_insecure_webhooks=True,
            test_mode=True,
        )
        if overrides:
            config = replace(config, **overrides)
        platform = Platform(config)
        platform.upsert_credential("pub_123", hmac_secret=SECRET)
        self.addCleanup(platform.close)
        return platform

    def _dispatch(self, platform: Platform, headers, body):
        return PlatformService(platform).dispatch(
            "POST", "/v1/lcp/leads", headers=headers, body=body
        )

    def _assert_code(self, platform: Platform, headers, body, code: str) -> None:
        status, _, error = self._dispatch(platform, headers, body)
        self.assertEqual(status, 401)
        self.assertEqual(error["errors"][0]["code"], code)

    # ── ERR-014: HMAC header presence ───────────────────────────────────────

    def test_missing_timestamp_header_is_lcp012(self) -> None:
        platform = self._platform()
        headers, body = _signed(
            _lead("auth-code-missing-ts"), drop=("X-LCP-Timestamp",)
        )
        self._assert_code(platform, headers, body, "LCP-012")

    def test_missing_signature_header_is_lcp012(self) -> None:
        platform = self._platform()
        headers, body = _signed(
            _lead("auth-code-missing-sig"), drop=("X-LCP-Signature",)
        )
        self._assert_code(platform, headers, body, "LCP-012")

    # ── ERR-015: timestamp must parse and carry a timezone ──────────────────

    def test_malformed_timestamp_is_lcp012(self) -> None:
        platform = self._platform()
        headers, body = _signed(_lead("auth-code-bad-ts"), timestamp="not-a-timestamp")
        self._assert_code(platform, headers, body, "LCP-012")

    def test_naive_timestamp_is_lcp012(self) -> None:
        platform = self._platform()
        naive = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        headers, body = _signed(_lead("auth-code-naive-ts"), timestamp=naive)
        self._assert_code(platform, headers, body, "LCP-012")

    # ── ERR-016: replay window is inclusive and symmetric ───────────────────

    def test_replay_window_boundary_is_inclusive_and_symmetric(self) -> None:
        now = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

        def at(offset_seconds: int) -> str:
            return (now + timedelta(seconds=offset_seconds)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        self.assertTrue(_timestamp_is_fresh(at(-WINDOW), WINDOW, now=now))
        self.assertTrue(_timestamp_is_fresh(at(WINDOW), WINDOW, now=now))
        self.assertFalse(_timestamp_is_fresh(at(-(WINDOW + 1)), WINDOW, now=now))
        self.assertFalse(_timestamp_is_fresh(at(WINDOW + 1), WINDOW, now=now))

    # ── ERR-017: idempotency header required on mutating routes ─────────────

    def test_mutating_request_without_idempotency_header_is_lcp012(self) -> None:
        platform = self._platform()
        headers, body = _signed(
            _lead("auth-code-no-idem"), drop=("X-LCP-Idempotency-Key",)
        )
        # Sign over the absent key so only the presence rule is on trial.
        headers["X-LCP-Signature"] = signature_for(
            SECRET, headers["X-LCP-Timestamp"], None, body
        )
        self._assert_code(platform, headers, body, "LCP-012")

    # ── ERR-018: header key must equal the envelope key ─────────────────────

    def test_header_envelope_idempotency_mismatch_is_lcp012(self) -> None:
        platform = self._platform()
        headers, body = _signed(_lead("auth-code-mismatch"), key="different-key")
        self._assert_code(platform, headers, body, "LCP-012")

    # ── ERR-002/ERR-019: identity and authorization stay on LCP-002 ─────────

    def test_wrong_bearer_key_for_recognized_sender_is_lcp002(self) -> None:
        platform = self._platform()
        platform.upsert_credential("pub_123", api_key="the-right-key")
        envelope = _lead("auth-code-bearer")
        headers, body = _signed(envelope)
        headers["Authorization"] = "Bearer the-wrong-key"
        status, _, error = self._dispatch(platform, headers, body)
        self.assertEqual(status, 401)
        self.assertEqual(error["errors"][0]["code"], "LCP-002")

    def test_scope_violation_reuses_lcp002(self) -> None:
        platform = self._platform()
        platform.upsert_credential(
            "pub_123", hmac_secret=SECRET, scopes=["lead:submit"]
        )
        timestamp = _ts(0)
        body = b""
        headers = {
            "X-LCP-Timestamp": timestamp,
            "X-LCP-Signature": signature_for(SECRET, timestamp, None, body),
        }
        with self.assertRaises(AuthenticationError) as context:
            platform.auth.authenticate(
                sender_id="pub_123",
                headers=headers,
                body=body,
                required_scope="platform:admin",
            )
        self.assertEqual(context.exception.code, "LCP-002")


if __name__ == "__main__":
    unittest.main()

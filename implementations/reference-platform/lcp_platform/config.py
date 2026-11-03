"""Reference platform configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.environ.get(name, default).split(",")
        if value.strip()
    )


@dataclass(frozen=True)
class PlatformConfig:
    database_path: Path
    database_url: str | None = None
    schema_root: Path | None = None
    secrets_file: Path | None = None
    pii_encryption_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    platform_id: str = "platform_001"
    routing_tenant_id: str = "default"
    require_auth: bool = True
    replay_window_seconds: int = 300
    webhook_timeout_seconds: float = 10.0
    # SPEC §9: the window a consumer has to acknowledge a delivered post
    # before the lead expires. Separate from the HTTP read timeout above.
    post_response_timeout_seconds: float = 30.0
    max_delivery_attempts: int = 5
    worker_interval_seconds: float = 1.0
    max_body_bytes: int = 2_000_000
    # LEP-0006 Track B §B.2: the 32 KiB total for `lead`/`post` `extensions`
    # is a SHOULD-level, scan-cheap policy bound (a fully shape-conformant
    # `{org}.form.questions` block can serialize past it; issue #197 tracks
    # the re-tune). The deployment MAY set a tighter limit; the operative hard
    # ceiling stays the transport body cap above, so a larger value here is
    # redundant rather than unsafe.
    max_extension_bytes: int = 32_768
    # D7/v1.2: an FX rate older than this (seconds) makes a cross-currency
    # offer NON-COMPARABLE in the auction. The default matches the decision
    # record; never compare with a guessed rate.
    fx_max_age_seconds: int = 300
    max_header_bytes: int = 16_384
    max_webhook_response_bytes: int = 262_144
    rate_limit_per_minute: int = 600
    allow_insecure_webhooks: bool = False
    webhook_host_allowlist: tuple[str, ...] = ()
    test_mode: bool = False
    # LEP-0008 agent attestation verification (§4/§5/§7): "observe" verifies and
    # audits but still accepts (Rollout Phase 2); "enforce" (default at ship)
    # rejects on any §5 failure. Skew cap <= 30 s is a §7 configuration-time
    # MUST; fetch timeout <= 2 s, JWKS document <= 16 KiB, and cache TTL <= 24 h
    # are the §4 Bounds/Cache ceilings. Egress is the shipped paired guard
    # (security.validate_webhook_url + validate_egress_host), HTTPS-only and
    # resolve-aware in every mode; webhook_host_allowlist, when configured, is
    # the issuer allowlist — otherwise SSRF-guard-only applies.
    attestation_mode: str = "enforce"
    attestation_clock_skew_seconds: int = 0
    attestation_fetch_timeout_seconds: float = 2.0
    attestation_jwks_max_bytes: int = 16_384
    attestation_jwks_cache_ttl_seconds: float = 3_600.0
    attachment_directory: Path = Path("./data/lcp-attachments")
    max_attachment_bytes: int = 100 * 1024 * 1024
    allowed_attachment_content_types: tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/plain",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    # Production object-storage profile. The file backend remains the default
    # for local and sandbox use; the S3-compatible backend requires all of the
    # object/KMS/residency fields below when selected outside test mode.
    attachment_backend: str = "file"
    attachment_scanner: str = "none"
    attachment_scan_required: bool = False
    attachment_residency: str | None = None
    attachment_allowed_residencies: tuple[str, ...] = ()
    attachment_object_bucket: str | None = None
    attachment_object_prefix: str = "lcp/attachments"
    attachment_object_endpoint_url: str | None = None
    attachment_object_region: str | None = None
    attachment_object_kms_key_id: str | None = None
    attachment_object_residency: str | None = None
    attachment_clamav_socket: str | None = None
    attachment_clamav_host: str = "127.0.0.1"
    attachment_clamav_port: int = 3310

    def __post_init__(self) -> None:
        if self.post_response_timeout_seconds <= 0:
            raise ValueError("The post response timeout must be positive")
        if self.max_body_bytes <= 0 or self.max_header_bytes <= 0:
            raise ValueError("Request size limits must be positive")
        if self.max_extension_bytes <= 0:
            # The bound is a policy target, not a transport rule: a value above
            # max_body_bytes is unreachable, never unsafe, so it is not refused.
            raise ValueError("The extensions policy bound must be positive")
        if self.fx_max_age_seconds <= 0:
            raise ValueError("LCP_FX_MAX_AGE_SECONDS must be positive")
        if self.max_webhook_response_bytes <= 0:
            raise ValueError("Webhook response limit must be positive")
        if self.max_attachment_bytes <= 0:
            raise ValueError("Attachment size limit must be positive")
        if self.rate_limit_per_minute <= 0:
            raise ValueError("Rate limit must be positive")
        if not self.require_auth and not self.test_mode:
            raise ValueError("Authentication may only be disabled in test mode")
        if not self.test_mode and not self.pii_encryption_key:
            raise ValueError("LCP_PII_ENCRYPTION_KEY is required outside test mode")
        if self.allow_insecure_webhooks and not self.test_mode:
            raise ValueError("Insecure webhooks may only be enabled in test mode")
        if self.attestation_mode not in {"observe", "enforce"}:
            raise ValueError("LCP_ATTESTATION_MODE must be 'observe' or 'enforce'")
        if not 0 <= self.attestation_clock_skew_seconds <= 30:
            raise ValueError(
                "LEP-0008 §7: attestation clock skew is capped at 30 seconds"
            )
        if not 0 < self.attestation_fetch_timeout_seconds <= 2.0:
            raise ValueError(
                "LEP-0008 §4: the JWKS fetch timeout must be positive and at most 2 seconds"
            )
        if not 0 < self.attestation_jwks_max_bytes <= 16_384:
            raise ValueError(
                "LEP-0008 §4: the JWKS document limit must be positive and at most 16 KiB"
            )
        if not 0 < self.attestation_jwks_cache_ttl_seconds <= 86_400:
            raise ValueError(
                "LEP-0008 §4: the JWKS cache TTL must be positive and at most 24 hours"
            )
        if self.attachment_backend not in {"file", "s3"}:
            raise ValueError("LCP_ATTACHMENT_BACKEND must be 'file' or 's3'")
        if self.attachment_scanner not in {"none", "clamav"}:
            raise ValueError("LCP_ATTACHMENT_SCANNER must be 'clamav' or 'none'")
        if (
            self.attachment_scan_required
            and self.attachment_scanner == "none"
            and not self.test_mode
        ):
            raise ValueError(
                "A production attachment scan is required when LCP_ATTACHMENT_SCAN_REQUIRED is enabled"
            )
        if self.attachment_backend == "s3" and not self.test_mode:
            missing = {
                "LCP_ATTACHMENT_OBJECT_BUCKET": self.attachment_object_bucket,
                "LCP_ATTACHMENT_OBJECT_KMS_KEY_ID": self.attachment_object_kms_key_id,
                "LCP_ATTACHMENT_OBJECT_RESIDENCY": self.attachment_object_residency,
            }
            if any(not value for value in missing.values()):
                raise ValueError(
                    f"S3 attachment backend requires: {', '.join(key for key, value in missing.items() if not value)}"
                )
            if not self.attachment_scan_required:
                raise ValueError("S3 attachment backend requires malware scanning")
            if (
                self.attachment_allowed_residencies
                and self.attachment_object_residency.upper()
                not in {
                    value.strip().upper()
                    for value in self.attachment_allowed_residencies
                }
            ):
                raise ValueError(
                    "Object-store residency is not in the allowed residency policy"
                )

    @classmethod
    def from_env(cls) -> "PlatformConfig":
        return cls(
            database_path=Path(
                os.environ.get("LCP_DATABASE_PATH", "./data/lcp.sqlite3")
            ),
            database_url=os.environ.get("LCP_DATABASE_URL"),
            schema_root=Path(os.environ["LCP_SCHEMA_DIR"])
            if os.environ.get("LCP_SCHEMA_DIR")
            else None,
            secrets_file=Path(os.environ["LCP_SECRETS_FILE"])
            if os.environ.get("LCP_SECRETS_FILE")
            else None,
            pii_encryption_key=os.environ.get("LCP_PII_ENCRYPTION_KEY"),
            host=os.environ.get("LCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("LCP_PORT", "8080")),
            platform_id=os.environ.get("LCP_PLATFORM_ID", "platform_001"),
            routing_tenant_id=os.environ.get("LCP_ROUTING_TENANT_ID", "default"),
            require_auth=_bool("LCP_REQUIRE_AUTH", True),
            replay_window_seconds=int(
                os.environ.get("LCP_REPLAY_WINDOW_SECONDS", "300")
            ),
            webhook_timeout_seconds=float(
                os.environ.get("LCP_WEBHOOK_TIMEOUT_SECONDS", "10")
            ),
            post_response_timeout_seconds=float(
                os.environ.get("LCP_POST_RESPONSE_TIMEOUT_SECONDS", "30")
            ),
            max_delivery_attempts=int(os.environ.get("LCP_MAX_DELIVERY_ATTEMPTS", "5")),
            worker_interval_seconds=float(
                os.environ.get("LCP_WORKER_INTERVAL_SECONDS", "1")
            ),
            max_body_bytes=int(os.environ.get("LCP_MAX_BODY_BYTES", "2000000")),
            max_extension_bytes=int(os.environ.get("LCP_MAX_EXTENSION_BYTES", "32768")),
            fx_max_age_seconds=int(os.environ.get("LCP_FX_MAX_AGE_SECONDS", "300")),
            max_header_bytes=int(os.environ.get("LCP_MAX_HEADER_BYTES", "16384")),
            max_webhook_response_bytes=int(
                os.environ.get("LCP_MAX_WEBHOOK_RESPONSE_BYTES", "262144")
            ),
            rate_limit_per_minute=int(
                os.environ.get("LCP_RATE_LIMIT_PER_MINUTE", "600")
            ),
            allow_insecure_webhooks=_bool("LCP_ALLOW_INSECURE_WEBHOOKS", False),
            webhook_host_allowlist=_csv("LCP_WEBHOOK_HOST_ALLOWLIST"),
            test_mode=_bool("LCP_TEST_MODE", False),
            attestation_mode=os.environ.get("LCP_ATTESTATION_MODE", "enforce").lower(),
            attestation_clock_skew_seconds=int(
                os.environ.get("LCP_ATTESTATION_CLOCK_SKEW_SECONDS", "0")
            ),
            attestation_fetch_timeout_seconds=float(
                os.environ.get("LCP_ATTESTATION_FETCH_TIMEOUT_SECONDS", "2")
            ),
            attestation_jwks_max_bytes=int(
                os.environ.get("LCP_ATTESTATION_JWKS_MAX_BYTES", "16384")
            ),
            attestation_jwks_cache_ttl_seconds=float(
                os.environ.get("LCP_ATTESTATION_JWKS_CACHE_TTL_SECONDS", "3600")
            ),
            attachment_directory=Path(
                os.environ.get("LCP_ATTACHMENT_DIRECTORY", "./data/lcp-attachments")
            ),
            max_attachment_bytes=int(
                os.environ.get("LCP_MAX_ATTACHMENT_BYTES", str(100 * 1024 * 1024))
            ),
            allowed_attachment_content_types=_csv(
                "LCP_ALLOWED_ATTACHMENT_CONTENT_TYPES",
                "application/pdf,image/jpeg,image/png,image/webp,text/plain,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            attachment_backend=os.environ.get("LCP_ATTACHMENT_BACKEND", "file").lower(),
            attachment_scanner=os.environ.get(
                "LCP_ATTACHMENT_SCANNER", "clamav"
            ).lower(),
            attachment_scan_required=_bool("LCP_ATTACHMENT_SCAN_REQUIRED", True),
            attachment_residency=os.environ.get("LCP_ATTACHMENT_RESIDENCY"),
            attachment_allowed_residencies=_csv("LCP_ATTACHMENT_ALLOWED_RESIDENCIES"),
            attachment_object_bucket=os.environ.get("LCP_ATTACHMENT_OBJECT_BUCKET"),
            attachment_object_prefix=os.environ.get(
                "LCP_ATTACHMENT_OBJECT_PREFIX", "lcp/attachments"
            ),
            attachment_object_endpoint_url=os.environ.get(
                "LCP_ATTACHMENT_OBJECT_ENDPOINT_URL"
            ),
            attachment_object_region=os.environ.get("LCP_ATTACHMENT_OBJECT_REGION"),
            attachment_object_kms_key_id=os.environ.get(
                "LCP_ATTACHMENT_OBJECT_KMS_KEY_ID"
            ),
            attachment_object_residency=os.environ.get(
                "LCP_ATTACHMENT_OBJECT_RESIDENCY"
            ),
            attachment_clamav_socket=os.environ.get("LCP_ATTACHMENT_CLAMAV_SOCKET"),
            attachment_clamav_host=os.environ.get(
                "LCP_ATTACHMENT_CLAMAV_HOST", "127.0.0.1"
            ),
            attachment_clamav_port=int(
                os.environ.get("LCP_ATTACHMENT_CLAMAV_PORT", "3310")
            ),
        )

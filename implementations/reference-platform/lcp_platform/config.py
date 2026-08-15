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
    return tuple(value.strip() for value in os.environ.get(name, default).split(",") if value.strip())


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
    max_delivery_attempts: int = 5
    worker_interval_seconds: float = 1.0
    max_body_bytes: int = 2_000_000
    max_header_bytes: int = 16_384
    max_webhook_response_bytes: int = 262_144
    rate_limit_per_minute: int = 600
    allow_insecure_webhooks: bool = False
    webhook_host_allowlist: tuple[str, ...] = ()
    test_mode: bool = False
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
        if self.max_body_bytes <= 0 or self.max_header_bytes <= 0:
            raise ValueError("Request size limits must be positive")
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
        if self.attachment_backend not in {"file", "s3"}:
            raise ValueError("LCP_ATTACHMENT_BACKEND must be 'file' or 's3'")
        if self.attachment_scanner not in {"none", "clamav"}:
            raise ValueError("LCP_ATTACHMENT_SCANNER must be 'clamav' or 'none'")
        if self.attachment_scan_required and self.attachment_scanner == "none" and not self.test_mode:
            raise ValueError("A production attachment scan is required when LCP_ATTACHMENT_SCAN_REQUIRED is enabled")
        if self.attachment_backend == "s3" and not self.test_mode:
            missing = {
                "LCP_ATTACHMENT_OBJECT_BUCKET": self.attachment_object_bucket,
                "LCP_ATTACHMENT_OBJECT_KMS_KEY_ID": self.attachment_object_kms_key_id,
                "LCP_ATTACHMENT_OBJECT_RESIDENCY": self.attachment_object_residency,
            }
            if any(not value for value in missing.values()):
                raise ValueError(f"S3 attachment backend requires: {', '.join(key for key, value in missing.items() if not value)}")
            if not self.attachment_scan_required:
                raise ValueError("S3 attachment backend requires malware scanning")
            if self.attachment_allowed_residencies and self.attachment_object_residency.upper() not in {
                value.strip().upper() for value in self.attachment_allowed_residencies
            }:
                raise ValueError("Object-store residency is not in the allowed residency policy")

    @classmethod
    def from_env(cls) -> "PlatformConfig":
        return cls(
            database_path=Path(os.environ.get("LCP_DATABASE_PATH", "./data/lcp.sqlite3")),
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
            replay_window_seconds=int(os.environ.get("LCP_REPLAY_WINDOW_SECONDS", "300")),
            webhook_timeout_seconds=float(os.environ.get("LCP_WEBHOOK_TIMEOUT_SECONDS", "10")),
            max_delivery_attempts=int(os.environ.get("LCP_MAX_DELIVERY_ATTEMPTS", "5")),
            worker_interval_seconds=float(os.environ.get("LCP_WORKER_INTERVAL_SECONDS", "1")),
            max_body_bytes=int(os.environ.get("LCP_MAX_BODY_BYTES", "2000000")),
            max_header_bytes=int(os.environ.get("LCP_MAX_HEADER_BYTES", "16384")),
            max_webhook_response_bytes=int(os.environ.get("LCP_MAX_WEBHOOK_RESPONSE_BYTES", "262144")),
            rate_limit_per_minute=int(os.environ.get("LCP_RATE_LIMIT_PER_MINUTE", "600")),
            allow_insecure_webhooks=_bool("LCP_ALLOW_INSECURE_WEBHOOKS", False),
            webhook_host_allowlist=_csv("LCP_WEBHOOK_HOST_ALLOWLIST"),
            test_mode=_bool("LCP_TEST_MODE", False),
            attachment_directory=Path(os.environ.get("LCP_ATTACHMENT_DIRECTORY", "./data/lcp-attachments")),
            max_attachment_bytes=int(os.environ.get("LCP_MAX_ATTACHMENT_BYTES", str(100 * 1024 * 1024))),
            allowed_attachment_content_types=_csv(
                "LCP_ALLOWED_ATTACHMENT_CONTENT_TYPES",
                "application/pdf,image/jpeg,image/png,image/webp,text/plain,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            attachment_backend=os.environ.get("LCP_ATTACHMENT_BACKEND", "file").lower(),
            attachment_scanner=os.environ.get("LCP_ATTACHMENT_SCANNER", "clamav").lower(),
            attachment_scan_required=_bool("LCP_ATTACHMENT_SCAN_REQUIRED", True),
            attachment_residency=os.environ.get("LCP_ATTACHMENT_RESIDENCY"),
            attachment_allowed_residencies=_csv("LCP_ATTACHMENT_ALLOWED_RESIDENCIES"),
            attachment_object_bucket=os.environ.get("LCP_ATTACHMENT_OBJECT_BUCKET"),
            attachment_object_prefix=os.environ.get("LCP_ATTACHMENT_OBJECT_PREFIX", "lcp/attachments"),
            attachment_object_endpoint_url=os.environ.get("LCP_ATTACHMENT_OBJECT_ENDPOINT_URL"),
            attachment_object_region=os.environ.get("LCP_ATTACHMENT_OBJECT_REGION"),
            attachment_object_kms_key_id=os.environ.get("LCP_ATTACHMENT_OBJECT_KMS_KEY_ID"),
            attachment_object_residency=os.environ.get("LCP_ATTACHMENT_OBJECT_RESIDENCY"),
            attachment_clamav_socket=os.environ.get("LCP_ATTACHMENT_CLAMAV_SOCKET"),
            attachment_clamav_host=os.environ.get("LCP_ATTACHMENT_CLAMAV_HOST", "127.0.0.1"),
            attachment_clamav_port=int(os.environ.get("LCP_ATTACHMENT_CLAMAV_PORT", "3310")),
        )

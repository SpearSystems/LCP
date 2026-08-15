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

    def __post_init__(self) -> None:
        if self.max_body_bytes <= 0 or self.max_header_bytes <= 0:
            raise ValueError("Request size limits must be positive")
        if self.max_webhook_response_bytes <= 0:
            raise ValueError("Webhook response limit must be positive")
        if self.rate_limit_per_minute <= 0:
            raise ValueError("Rate limit must be positive")
        if not self.require_auth and not self.test_mode:
            raise ValueError("Authentication may only be disabled in test mode")
        if not self.test_mode and not self.pii_encryption_key:
            raise ValueError("LCP_PII_ENCRYPTION_KEY is required outside test mode")
        if self.allow_insecure_webhooks and not self.test_mode:
            raise ValueError("Insecure webhooks may only be enabled in test mode")

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
            max_webhook_response_bytes=int(
                os.environ.get("LCP_MAX_WEBHOOK_RESPONSE_BYTES", "262144")
            ),
            rate_limit_per_minute=int(os.environ.get("LCP_RATE_LIMIT_PER_MINUTE", "600")),
            allow_insecure_webhooks=_bool("LCP_ALLOW_INSECURE_WEBHOOKS", False),
            webhook_host_allowlist=tuple(
                value.strip()
                for value in os.environ.get("LCP_WEBHOOK_HOST_ALLOWLIST", "").split(",")
                if value.strip()
            ),
            test_mode=_bool("LCP_TEST_MODE", False),
        )

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
    schema_root: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    platform_id: str = "platform_001"
    require_auth: bool = True
    replay_window_seconds: int = 300
    webhook_timeout_seconds: float = 10.0
    max_delivery_attempts: int = 5
    worker_interval_seconds: float = 1.0
    max_body_bytes: int = 2_000_000
    rate_limit_per_minute: int = 600
    test_mode: bool = False

    @classmethod
    def from_env(cls) -> "PlatformConfig":
        return cls(
            database_path=Path(os.environ.get("LCP_DATABASE_PATH", "./data/lcp.sqlite3")),
            schema_root=Path(os.environ["LCP_SCHEMA_DIR"])
            if os.environ.get("LCP_SCHEMA_DIR")
            else None,
            host=os.environ.get("LCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("LCP_PORT", "8080")),
            platform_id=os.environ.get("LCP_PLATFORM_ID", "platform_001"),
            require_auth=_bool("LCP_REQUIRE_AUTH", True),
            replay_window_seconds=int(os.environ.get("LCP_REPLAY_WINDOW_SECONDS", "300")),
            webhook_timeout_seconds=float(os.environ.get("LCP_WEBHOOK_TIMEOUT_SECONDS", "10")),
            max_delivery_attempts=int(os.environ.get("LCP_MAX_DELIVERY_ATTEMPTS", "5")),
            worker_interval_seconds=float(os.environ.get("LCP_WORKER_INTERVAL_SECONDS", "1")),
            max_body_bytes=int(os.environ.get("LCP_MAX_BODY_BYTES", "2000000")),
            rate_limit_per_minute=int(os.environ.get("LCP_RATE_LIMIT_PER_MINUTE", "600")),
            test_mode=_bool("LCP_TEST_MODE", False),
        )

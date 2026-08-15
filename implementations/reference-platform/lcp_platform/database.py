"""Database backend selection."""

from __future__ import annotations

from typing import Any

from .config import PlatformConfig
from .storage import Store


def create_store(config: PlatformConfig) -> Any:
    if config.database_url:
        from .postgres import PostgresStore
        return PostgresStore(
            config.database_url,
            pii_encryption_key=config.pii_encryption_key,
        )
    return Store(config.database_path, pii_encryption_key=config.pii_encryption_key)

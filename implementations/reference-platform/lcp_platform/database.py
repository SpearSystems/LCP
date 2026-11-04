"""Database backend selection."""

from __future__ import annotations

from typing import Any

from .config import PlatformConfig
from .storage import Store


def create_store(config: PlatformConfig) -> Any:
    # W4-05/W4-06: the deferred-write queue and both retention windows are
    # store state, so the backend choice cannot change their meaning.
    if config.database_url:
        from .postgres import PostgresStore

        return PostgresStore(
            config.database_url,
            pii_encryption_key=config.pii_encryption_key,
            history_queue_capacity=config.history_queue_capacity,
            envelope_retention_days=config.envelope_retention_days,
            history_retention_days=config.history_retention_days,
        )
    return Store(
        config.database_path,
        pii_encryption_key=config.pii_encryption_key,
        history_queue_capacity=config.history_queue_capacity,
        envelope_retention_days=config.envelope_retention_days,
        history_retention_days=config.history_retention_days,
    )

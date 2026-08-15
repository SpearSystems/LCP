"""External secret loading for the reference platform.

The database may be used for local development. Production deployments should
mount this file from a secret manager/Kubernetes Secret and avoid persisting
raw HMAC secrets in the database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class SecretProviderError(RuntimeError):
    pass


class FileSecretProvider:
    """Load sender secrets from a JSON file mounted by the deployment."""

    def __init__(self, path: Path | None, *, require_private_permissions: bool = True):
        self.path = path
        self.require_private_permissions = require_private_permissions
        self._secrets: dict[str, dict[str, Any]] = {}
        self._mtime_ns: int | None = None
        if path:
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        if not self.path.exists():
            raise SecretProviderError(f"Secret file does not exist: {self.path}")
        if self.require_private_permissions and os.name != "nt":
            mode = self.path.stat().st_mode & 0o007
            if mode:
                raise SecretProviderError(
                    f"Secret file {self.path} is world accessible; use chmod 600 or 640"
                )
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise SecretProviderError("Secret file must contain an object keyed by sender ID")
        for sender_id, values in data.items():
            if not isinstance(sender_id, str) or not isinstance(values, dict):
                raise SecretProviderError("Secret file entries must be sender objects")
            if "tenant_id" in values and not isinstance(values["tenant_id"], str):
                raise SecretProviderError("tenant_id must be a string")
            if "scopes" in values and (
                not isinstance(values["scopes"], list)
                or not all(isinstance(scope, str) and scope for scope in values["scopes"])
            ):
                raise SecretProviderError("scopes must be a list of non-empty strings")
            for field in ("hmac_secret", "previous_hmac_secret", "api_key"):
                if field in values and not isinstance(values[field], str):
                    raise SecretProviderError(f"{field} must be a string")
        self._secrets = data
        self._mtime_ns = self.path.stat().st_mtime_ns

    def _reload_if_changed(self) -> None:
        if self.path and self.path.stat().st_mtime_ns != self._mtime_ns:
            self._load()

    def get(self, sender_id: str) -> dict[str, Any] | None:
        self._reload_if_changed()
        values = self._secrets.get(sender_id)
        if values and values.get("active", True) is not True:
            return None
        return values

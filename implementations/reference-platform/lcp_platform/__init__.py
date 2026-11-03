"""Production-oriented reference LCP platform.

Public names are resolved lazily so importing a submodule (for example
``lcp_platform.matching`` from a benchmark) does not pull the HTTP, storage,
and crypto stack at package-import time.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "MappingError": "mapping",
    "MappingRegistry": "mapping",
    "PublisherNormalizer": "mapping",
    "Platform": "router",
    "RequestError": "router",
    "PlatformConfig": "config",
    "PlatformService": "service",
}

__all__ = [
    "MappingError",
    "MappingRegistry",
    "Platform",
    "PlatformConfig",
    "PlatformService",
    "PublisherNormalizer",
    "RequestError",
]
__version__ = "1.0.2"


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is not None:
        return getattr(import_module(f".{module}", __name__), name)
    if name.startswith("_"):
        raise AttributeError(name)
    # ``from lcp_platform import attestation`` still resolves the submodule.
    try:
        return import_module(f".{name}", __name__)
    except ModuleNotFoundError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

"""Production-oriented reference LCP platform."""

from .config import PlatformConfig
from .mapping import MappingError, MappingRegistry, PublisherNormalizer
from .router import Platform, RequestError
from .service import PlatformService

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

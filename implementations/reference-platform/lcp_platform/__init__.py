"""Production-oriented reference LCP platform."""

from .config import PlatformConfig
from .router import Platform, RequestError
from .service import PlatformService

__all__ = ["Platform", "PlatformConfig", "PlatformService", "RequestError"]
__version__ = "0.1.0"

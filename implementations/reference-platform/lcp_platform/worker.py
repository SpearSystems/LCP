"""Standalone delivery worker for production process managers."""

from __future__ import annotations

import time

from .config import PlatformConfig
from .router import Platform


def run() -> None:
    config = PlatformConfig.from_env()
    platform = Platform(config)
    print("LCP delivery worker started")
    try:
        while True:
            platform.process_once()
            time.sleep(config.worker_interval_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        platform.close()


def main() -> None:
    run()


if __name__ == "__main__":
    main()

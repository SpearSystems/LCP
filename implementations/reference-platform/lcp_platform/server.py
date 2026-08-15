"""Local HTTP server entry point for the reference platform."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

from .config import PlatformConfig
from .router import Platform
from .service import PlatformService


class LCPRequestHandler(BaseHTTPRequestHandler):
    service: PlatformService
    max_body_bytes: int = 2_000_000

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET", b"")

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > self.max_body_bytes:
            self.send_error(413, "Request body is too large")
            return
        self._dispatch("POST", self.rfile.read(length))

    def _dispatch(self, method: str, body: bytes) -> None:
        headers = {key: value for key, value in self.headers.items()}
        status, response_headers, payload = self.service.dispatch(
            method,
            self.path,
            headers=headers,
            body=body,
        )
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for key, value in response_headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        # Keep the default operational access log while avoiding noisy headers.
        super().log_message(format, *args)


def run(config: PlatformConfig | None = None) -> None:
    config = config or PlatformConfig.from_env()
    platform = Platform(config)
    service = PlatformService(platform)
    handler = type("ConfiguredLCPRequestHandler", (LCPRequestHandler,), {})
    handler.service = service
    handler.max_body_bytes = config.max_body_bytes
    server = ThreadingHTTPServer((config.host, config.port), handler)
    worker = threading.Thread(
        target=_worker_loop,
        args=(platform, config.worker_interval_seconds),
        name="lcp-delivery-worker",
        daemon=True,
    )
    worker.start()
    print(f"LCP platform listening on http://{config.host}:{config.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        platform.close()


def _worker_loop(platform: Platform, interval: float) -> None:
    while True:
        try:
            platform.process_once()
        except Exception as exc:  # keep delivery worker alive; operators see the error
            print(f"LCP delivery worker error: {exc}")
        time.sleep(interval)


def main() -> None:
    run()


if __name__ == "__main__":
    main()

"""WSGI entry point.

Run the delivery worker separately in production, for example through the same
process supervisor as the WSGI server.
"""

from __future__ import annotations

import json

from .config import PlatformConfig
from .router import Platform
from .service import PlatformService

_platform = Platform(PlatformConfig.from_env())
_service = PlatformService(_platform)


def application(environ: dict, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    query = environ.get("QUERY_STRING", "")
    raw_length = environ.get("CONTENT_LENGTH")
    try:
        length = int(raw_length) if raw_length else 0
    except ValueError:
        length = -1
    if length < 0 or length > _platform.config.max_body_bytes:
        payload = {"errors": [{"code": "LCP-001", "message": "Request body is too large"}]}
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        start_response("413 ERROR", [("Content-Type", "application/json"), ("Content-Length", str(len(encoded)))])
        return [encoded]
    body = environ["wsgi.input"].read(length) if length else b""
    headers = {
        key[5:].replace("_", "-"): value
        for key, value in environ.items()
        if key.startswith("HTTP_")
    }
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    full_path = f"{path}?{query}" if query else path
    status, response_headers, payload = _service.dispatch(
        method, full_path, headers=headers, body=body
    )
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    start_response(
        f"{status} {'OK' if status < 400 else 'ERROR'}",
        list(response_headers.items()) + [("Content-Length", str(len(encoded)))],
    )
    return [encoded]

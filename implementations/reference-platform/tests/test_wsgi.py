from __future__ import annotations

import base64
from hashlib import sha256
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.service import PlatformService


ROOT = Path(__file__).resolve().parents[3]


class WSGIAttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # wsgi.py constructs its process-global platform at import time. Give
        # that construction a harmless sandbox configuration, then replace it
        # with the isolated platform used by each test.
        env = {
            "LCP_TEST_MODE": "true",
            "LCP_REQUIRE_AUTH": "false",
            "LCP_DATABASE_PATH": ":memory:",
            "LCP_ATTACHMENT_SCANNER": "none",
            "LCP_ATTACHMENT_SCAN_REQUIRED": "false",
        }
        cls._saved_env = {key: os.environ.get(key) for key in env}
        os.environ.update(env)
        cls.wsgi = importlib.import_module("lcp_platform.wsgi")
        for key, value in cls._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls._import_platform = cls.wsgi._platform

    @classmethod
    def tearDownClass(cls) -> None:
        cls._import_platform.close()

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                test_mode=True,
                max_body_bytes=4,
                max_attachment_bytes=1024,
                attachment_directory=Path(self.tempdir.name) / "attachments",
                attachment_scanner="none",
                pii_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
            )
        )
        self.previous_platform = self.wsgi._platform
        self.previous_service = self.wsgi._service
        self.wsgi._platform = self.platform
        self.wsgi._service = PlatformService(self.platform)

    def tearDown(self) -> None:
        self.wsgi._platform = self.previous_platform
        self.wsgi._service = self.previous_service
        self.platform.close()
        self.tempdir.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        environ: dict[str, object] = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "wsgi.url_scheme": "http",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
        }
        for key, value in (headers or {}).items():
            if key.lower() == "content-type":
                environ["CONTENT_TYPE"] = value
            else:
                environ[f"HTTP_{key.upper().replace('-', '_')}"] = value
        started: list[tuple[str, list[tuple[str, str]]]] = []

        def start_response(status: str, response_headers: list[tuple[str, str]], _exc_info=None) -> None:
            started.append((status, response_headers))

        response = b"".join(self.wsgi.application(environ, start_response))
        self.assertEqual(len(started), 1)
        status, response_headers = started[0]
        return int(status.split()[0]), dict(response_headers), response

    def test_attachment_upload_uses_attachment_limit_not_json_limit(self) -> None:
        body = b"%PDF\x00binary\xffattachment"
        digest = sha256(body).hexdigest()
        status, response_headers, response = self.request(
            "POST",
            "/v1/lcp/attachments",
            body=body,
            headers={
                "X-LCP-Test": "true",
                "X-LCP-Sender-Id": "publisher_wsgi",
                "X-LCP-Lead-Id": "wsgi-lead-001",
                "X-LCP-Attachment-Id": "att_wsgi_001",
                "X-LCP-Filename": "evidence.pdf",
                "Content-Type": "application/pdf",
                "X-LCP-Content-SHA256": digest,
                "X-LCP-Idempotency-Key": "wsgi-attachment-001",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(response_headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(response)["attachment_id"], "att_wsgi_001")

    def test_attachment_download_returns_binary_headers_and_bytes(self) -> None:
        body = b"%PDF\x00binary\xffdownload"
        digest = sha256(body).hexdigest()
        self.request(
            "POST",
            "/v1/lcp/attachments",
            body=body,
            headers={
                "X-LCP-Test": "true",
                "X-LCP-Sender-Id": "publisher_wsgi",
                "X-LCP-Lead-Id": "wsgi-lead-002",
                "X-LCP-Attachment-Id": "att_wsgi_002",
                "X-LCP-Filename": "report.pdf",
                "Content-Type": "application/pdf",
                "X-LCP-Content-SHA256": digest,
                "X-LCP-Idempotency-Key": "wsgi-attachment-002",
            },
        )
        status, response_headers, response = self.request(
            "GET",
            "/v1/lcp/attachments/att_wsgi_002",
            headers={"X-LCP-Sender-Id": "publisher_wsgi"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response, body)
        self.assertEqual(response_headers["Content-Type"], "application/pdf")
        self.assertEqual(response_headers["Content-Disposition"], 'attachment; filename="report.pdf"')
        self.assertEqual(response_headers["Content-Length"], str(len(body)))


if __name__ == "__main__":
    unittest.main()

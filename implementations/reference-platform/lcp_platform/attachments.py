"""Secure attachment storage and scanning adapters.

The reference platform uses :class:`FileAttachmentStore` for a self-contained
sandbox. Production deployments can select :class:`S3ObjectStorageAttachmentStore`
for AWS S3 or another S3-compatible service. The adapter deliberately keeps
provider-specific URLs and object keys opaque to LCP messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Protocol

from .crypto import EnvelopeCipher


class AttachmentError(ValueError):
    """Raised for invalid, missing, unsafe, or tampered attachment content."""


@dataclass(frozen=True)
class MalwareScanResult:
    """Safe-to-persist result of an attachment malware scan."""

    status: str
    engine: str
    scanned_at: str


class MalwareScanner(Protocol):
    def scan(self, content: bytes, *, filename: str, content_type: str) -> MalwareScanResult:
        """Return a clean result or raise :class:`AttachmentError`."""


class NoopMalwareScanner:
    """Test-only scanner; production configuration must use a real scanner."""

    def scan(self, content: bytes, *, filename: str, content_type: str) -> MalwareScanResult:
        from .storage import now_iso

        return MalwareScanResult("not_scanned", "disabled-test-scanner", now_iso())


class ClamAVMalwareScanner:
    """Fail-closed ClamAV scanner using a local ``clamd`` daemon."""

    def __init__(
        self,
        *,
        socket_path: str | None = None,
        host: str = "127.0.0.1",
        port: int = 3310,
        client: Any | None = None,
    ):
        if client is not None:
            self.client = client
            return
        try:
            import clamd
        except ImportError as exc:
            raise AttachmentError(
                "ClamAV scanning requires the reference-platform[object-storage] extra"
            ) from exc
        try:
            self.client = (
                clamd.ClamdUnixSocket(path=socket_path)
                if socket_path
                else clamd.ClamdNetworkSocket(host=host, port=port)
            )
        except Exception as exc:
            raise AttachmentError("Unable to initialize the ClamAV scanner") from exc

    def scan(self, content: bytes, *, filename: str, content_type: str) -> MalwareScanResult:
        del filename, content_type
        try:
            result = self.client.instream(content)
        except Exception as exc:
            raise AttachmentError("Malware scanner is unavailable") from exc
        if not isinstance(result, dict):
            raise AttachmentError("Malware scanner returned an invalid result")
        stream_result = result.get("stream")
        if not isinstance(stream_result, tuple) or not stream_result:
            raise AttachmentError("Malware scanner returned an invalid result")
        status = str(stream_result[0]).upper()
        if status == "FOUND":
            raise AttachmentError("Attachment failed malware scanning")
        if status != "OK":
            raise AttachmentError("Attachment malware scan did not complete cleanly")
        from .storage import now_iso

        return MalwareScanResult("clean", "clamav", now_iso())


class ObjectStorageClient(Protocol):
    """Minimal S3-compatible client contract used by the production adapter."""

    def put_object(self, **kwargs: Any) -> Any: ...

    def get_object(self, **kwargs: Any) -> Any: ...

    def head_object(self, **kwargs: Any) -> Any: ...

    def delete_object(self, **kwargs: Any) -> Any: ...


_ATTACHMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FILENAME = re.compile(r"^[^/\\\x00]{1,255}$")
_CONTENT_TYPE = re.compile(r"^[^;\s]+/[^;\s]+$")
_RESIDENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,31}$")


def validate_residency(residency: str) -> str:
    """Normalize and validate a country/region residency policy identifier."""
    normalized = residency.strip().upper()
    if not _RESIDENCY.fullmatch(normalized):
        raise AttachmentError("attachment residency must be a non-empty country or region identifier")
    return normalized


class FileAttachmentStore:
    """Store encrypted attachment bytes with restrictive local permissions."""

    encryption = "application_encrypted"

    def __init__(self, root: str | Path, *, cipher: EnvelopeCipher):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)
        self.cipher = cipher

    @staticmethod
    def validate_metadata(*, attachment_id: str, filename: str, content_type: str, size_bytes: int) -> None:
        if not _ATTACHMENT_ID.fullmatch(attachment_id):
            raise AttachmentError("attachment_id contains unsafe characters")
        if not _FILENAME.fullmatch(filename):
            raise AttachmentError("filename must be a basename without path separators")
        if not _CONTENT_TYPE.fullmatch(content_type):
            raise AttachmentError("content_type must be a valid media type")
        if size_bytes <= 0:
            raise AttachmentError("attachment must not be empty")

    def _path(self, attachment_id: str) -> Path:
        self.validate_metadata(attachment_id=attachment_id, filename="x", content_type="x/x", size_bytes=1)
        return self.root / f"{attachment_id}.bin"

    def put(
        self,
        attachment_id: str,
        content: bytes,
        *,
        sha256_hex: str,
        content_type: str,
        filename: str,
        residency: str,
    ) -> str:
        del content_type, filename
        validate_residency(residency)
        if sha256(content).hexdigest() != sha256_hex:
            raise AttachmentError("attachment sha256 does not match the uploaded bytes")
        path = self._path(attachment_id)
        encrypted = self.cipher.encrypt_bytes(content)
        with tempfile.NamedTemporaryFile(dir=self.root, prefix=f".{attachment_id}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
        return f"lcp://attachments/{attachment_id}"

    def read(self, storage_ref: str, *, expected_sha256: str) -> bytes:
        prefix = "lcp://attachments/"
        if not storage_ref.startswith(prefix):
            raise AttachmentError("unsupported attachment storage reference")
        path = self._path(storage_ref.removeprefix(prefix))
        try:
            content = self.cipher.decrypt_bytes(path.read_bytes())
        except FileNotFoundError as exc:
            raise AttachmentError("attachment bytes are not available") from exc
        except Exception as exc:
            raise AttachmentError("attachment decryption failed") from exc
        if sha256(content).hexdigest() != expected_sha256:
            raise AttachmentError("attachment integrity check failed")
        return content

    def delete(self, storage_ref: str) -> None:
        prefix = "lcp://attachments/"
        if not storage_ref.startswith(prefix):
            return
        path = self._path(storage_ref.removeprefix(prefix))
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class S3ObjectStorageAttachmentStore:
    """S3-compatible object storage with mandatory SSE-KMS and residency keys.

    The adapter is intentionally dependency-injected for tests and for cloud-
    neutral S3-compatible services. When no client is supplied it lazily uses
    ``boto3.client('s3')``. The KMS key and residency are immutable properties
    of an adapter instance, so a process cannot accidentally write one tenant's
    attachment to another configured region.
    """

    encryption = "provider_encrypted"
    _ref_prefix = "lcp-object://attachments/"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        residency: str,
        kms_key_id: str,
        client: ObjectStorageClient | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ):
        if not bucket or not re.fullmatch(r"[A-Za-z0-9._-]{3,255}", bucket):
            raise AttachmentError("object storage bucket is invalid")
        if not kms_key_id:
            raise AttachmentError("object storage requires a KMS key identifier")
        self.bucket = bucket
        self.prefix = prefix.strip("/") or "lcp/attachments"
        self.residency = validate_residency(residency)
        self.kms_key_id = kms_key_id
        if client is not None:
            self.client = client
        else:
            try:
                import boto3
            except ImportError as exc:
                raise AttachmentError(
                    "S3 object storage requires the reference-platform[object-storage] extra"
                ) from exc
            self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region_name)

    def _key(self, attachment_id: str) -> str:
        FileAttachmentStore.validate_metadata(
            attachment_id=attachment_id,
            filename="x",
            content_type="x/x",
            size_bytes=1,
        )
        return f"{self.prefix}/residency/{self.residency}/{attachment_id}.bin"

    def _attachment_id(self, storage_ref: str) -> str:
        if not storage_ref.startswith(self._ref_prefix):
            raise AttachmentError("unsupported attachment storage reference")
        attachment_id = storage_ref.removeprefix(self._ref_prefix)
        self._key(attachment_id)
        return attachment_id

    def put(
        self,
        attachment_id: str,
        content: bytes,
        *,
        sha256_hex: str,
        content_type: str,
        filename: str,
        residency: str,
    ) -> str:
        FileAttachmentStore.validate_metadata(
            attachment_id=attachment_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
        )
        if validate_residency(residency) != self.residency:
            raise AttachmentError("attachment residency does not match the object-store adapter")
        if sha256(content).hexdigest() != sha256_hex:
            raise AttachmentError("attachment sha256 does not match the uploaded bytes")
        key = self._key(attachment_id)
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": content,
            "ContentType": content_type,
            "Metadata": {
                "lcp-sha256": sha256_hex,
                "lcp-residency": self.residency,
                "lcp-filename": filename,
                "lcp-malware-status": "clean",
            },
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self.kms_key_id,
            "SSEKMSEncryptionContext": json.dumps({"lcp_residency": self.residency}, separators=(",", ":")),
        }
        try:
            self.client.put_object(**kwargs)
        except Exception as exc:
            raise AttachmentError("object storage write failed") from exc
        return f"{self._ref_prefix}{attachment_id}"

    def read(self, storage_ref: str, *, expected_sha256: str) -> bytes:
        attachment_id = self._attachment_id(storage_ref)
        key = self._key(attachment_id)
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            metadata = head.get("Metadata", {})
            if metadata.get("lcp-residency", "").upper() != self.residency:
                raise AttachmentError("object residency metadata does not match policy")
            if metadata.get("lcp-sha256", "").lower() != expected_sha256.lower():
                raise AttachmentError("object metadata hash does not match the attachment record")
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"].read()
        except AttachmentError:
            raise
        except Exception as exc:
            raise AttachmentError("object storage read failed") from exc
        if sha256(body).hexdigest() != expected_sha256:
            raise AttachmentError("attachment integrity check failed")
        return body

    def delete(self, storage_ref: str) -> None:
        attachment_id = self._attachment_id(storage_ref)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._key(attachment_id))
        except Exception as exc:
            raise AttachmentError("object storage deletion failed") from exc


def build_attachment_store(config: Any, *, cipher: EnvelopeCipher) -> FileAttachmentStore | S3ObjectStorageAttachmentStore:
    """Build the configured backend; imports cloud SDKs only when selected."""
    if config.attachment_backend == "s3":
        return S3ObjectStorageAttachmentStore(
            bucket=config.attachment_object_bucket or "",
            prefix=config.attachment_object_prefix,
            residency=config.attachment_object_residency or "",
            kms_key_id=config.attachment_object_kms_key_id or "",
            endpoint_url=config.attachment_object_endpoint_url,
            region_name=config.attachment_object_region,
        )
    if config.attachment_backend != "file":
        raise AttachmentError("LCP_ATTACHMENT_BACKEND must be 'file' or 's3'")
    return FileAttachmentStore(config.attachment_directory, cipher=cipher)


def build_malware_scanner(config: Any) -> MalwareScanner:
    if config.attachment_scanner == "none":
        return NoopMalwareScanner()
    if config.attachment_scanner == "clamav":
        return ClamAVMalwareScanner(
            socket_path=config.attachment_clamav_socket,
            host=config.attachment_clamav_host,
            port=config.attachment_clamav_port,
        )
    raise AttachmentError("LCP_ATTACHMENT_SCANNER must be 'clamav' or 'none'")

"""Standalone LCP SDK for Python integrations."""

from .capability import (
    SLOT_FIELDS,
    receiver_advertises_slot_fields,
    slot_capability_errors,
    slot_fields_present,
)
from .client import LCPClient, LCPHTTPError
from .envelope import build_envelope, message_metadata, utc_timestamp
from .signing import (
    SignatureError,
    canonical_signing_bytes,
    contact_hash,
    normalize_contact,
    sign_hmac,
    verify_hmac,
)
from .validation import SchemaValidator, ValidationError
from .webhook import verify_http_request

__all__ = [
    "SLOT_FIELDS",
    "LCPClient",
    "LCPHTTPError",
    "SchemaValidator",
    "SignatureError",
    "ValidationError",
    "build_envelope",
    "canonical_signing_bytes",
    "contact_hash",
    "message_metadata",
    "normalize_contact",
    "receiver_advertises_slot_fields",
    "sign_hmac",
    "slot_capability_errors",
    "slot_fields_present",
    "utc_timestamp",
    "verify_hmac",
    "verify_http_request",
]

__version__ = "1.0.2"

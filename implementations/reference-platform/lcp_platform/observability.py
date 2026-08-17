"""Request-scoped observability primitives with no consumer-data logging."""

from __future__ import annotations

from contextvars import ContextVar


_request_id: ContextVar[str | None] = ContextVar("lcp_request_id", default=None)


def set_request_id(value: str | None):
    """Set the request ID for the current execution context."""
    return _request_id.set(value)


def reset_request_id(token) -> None:
    """Restore the previous request ID after a request finishes."""
    _request_id.reset(token)


def current_request_id() -> str | None:
    """Return the current request ID, if the call is inside an HTTP request."""
    return _request_id.get()

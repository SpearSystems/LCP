"""Deployment security policy helpers."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from .config import PlatformConfig


class SecurityPolicyError(ValueError):
    pass


def validate_webhook_url(url: str, config: PlatformConfig) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"}:
        raise SecurityPolicyError("Webhook URL must use HTTPS")
    if parsed.username or parsed.password:
        raise SecurityPolicyError("Webhook URL must not contain embedded credentials")
    if not parsed.hostname:
        raise SecurityPolicyError("Webhook URL must include a hostname")
    hostname = _normalized_hostname(parsed.hostname)
    allowlist = {_normalized_hostname(value) for value in config.webhook_host_allowlist}
    if parsed.scheme == "http" and not config.allow_insecure_webhooks:
        raise SecurityPolicyError("HTTP webhook URLs are disabled outside sandbox mode")
    if allowlist and hostname not in allowlist:
        raise SecurityPolicyError("Webhook hostname is not on the configured allowlist")
    _reject_private_ip(hostname, config)


def validate_egress_host(hostname: str, config: PlatformConfig) -> None:
    """Resolve a webhook hostname and reject private/link-local destinations."""
    hostname = _normalized_hostname(hostname)
    if not hostname:
        raise SecurityPolicyError("Webhook URL has no hostname")
    allowlist = {_normalized_hostname(value) for value in config.webhook_host_allowlist}
    if allowlist and hostname not in allowlist:
        raise SecurityPolicyError("Webhook hostname is not on the configured allowlist")
    _reject_private_ip(hostname, config)
    if config.allow_insecure_webhooks:
        return
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise SecurityPolicyError("Webhook hostname could not be resolved") from exc
    if not addresses:
        raise SecurityPolicyError("Webhook hostname has no resolved addresses")
    for address in addresses:
        _reject_private_ip(address, config)


def _normalized_hostname(hostname: str) -> str:
    return hostname.rstrip(".").lower()


def _reject_private_ip(hostname: str, config: PlatformConfig) -> None:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return
    if config.allow_insecure_webhooks:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise SecurityPolicyError("Private or non-routable webhook destination is forbidden")

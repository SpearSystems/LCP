#!/usr/bin/env python3
"""Check that a proposed LCP release version is available in public registries.

This is intentionally a pre-publication check. It never publishes anything and
fails closed when a registry cannot answer, so a maintainer does not mistake an
outage for an available version.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


USER_AGENT = "LCP-release-dry-run/1.0 (+https://github.com/SpearSystems/LCP)"


@dataclass(frozen=True)
class RegistryCheck:
    name: str
    url: str
    kind: str = "exact"


class RegistryCheckError(RuntimeError):
    pass


def registry_checks(version: str) -> tuple[RegistryCheck, ...]:
    encoded_npm = quote("@spear-systems/lcp-sdk", safe="")
    encoded_version = quote(version, safe="")
    return (
        RegistryCheck(
            "PyPI lcp-sdk",
            f"https://pypi.org/pypi/lcp-sdk/{encoded_version}/json",
        ),
        RegistryCheck(
            "PyPI lcp-mcp-server",
            f"https://pypi.org/pypi/lcp-mcp-server/{encoded_version}/json",
        ),
        RegistryCheck(
            "PyPI lcp-reference-platform",
            f"https://pypi.org/pypi/lcp-reference-platform/{encoded_version}/json",
        ),
        RegistryCheck(
            "npm @spear-systems/lcp-sdk",
            f"https://registry.npmjs.org/{encoded_npm}/{encoded_version}",
        ),
        RegistryCheck(
            "NuGet LcpSdk",
            f"https://api.nuget.org/v3-flatcontainer/lcpsdk/{encoded_version}/lcpsdk.nuspec",
        ),
        # repo1.maven.org is the authoritative CDN: an absent version returns
        # HTTP 404 for the artifact POM, so each coordinate is an exact check.
        # (The legacy search.maven.org SOLR endpoint is flaky and frequently
        # times out, so it is deliberately not used as a release gate.)
        RegistryCheck(
            "Maven Central lcp-sdk",
            f"https://repo1.maven.org/maven2/com/spearsystems/lcp-sdk/{encoded_version}/lcp-sdk-{encoded_version}.pom",
        ),
        RegistryCheck(
            "Maven Central lcp-sdk-kotlin",
            f"https://repo1.maven.org/maven2/com/spearsystems/lcp-sdk-kotlin/{encoded_version}/lcp-sdk-kotlin-{encoded_version}.pom",
        ),
        RegistryCheck(
            "crates.io lcp-sdk",
            f"https://crates.io/api/v1/crates/lcp-sdk/{encoded_version}",
        ),
        RegistryCheck(
            "RubyGems lcp-sdk",
            f"https://rubygems.org/api/v2/rubygems/lcp-sdk/versions/{encoded_version}.json",
        ),
        RegistryCheck(
            "Packagist spearsystems/lcp-sdk",
            "https://repo.packagist.org/p2/spearsystems/lcp-sdk.json",
            "packagist",
        ),
    )


def fetch(check: RegistryCheck, timeout: float = 15.0) -> tuple[int, bytes]:
    request = Request(check.url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as error:
        return int(error.code), error.read()
    except URLError as error:
        raise RegistryCheckError(f"{check.name}: registry request failed: {error.reason}") from error
    except TimeoutError as error:
        raise RegistryCheckError(f"{check.name}: registry request timed out") from error
    except OSError as error:
        raise RegistryCheckError(f"{check.name}: registry request failed: {error}") from error


def contains_version(check: RegistryCheck, status: int, body: bytes, version: str) -> bool:
    if status == 404:
        return False
    if status != 200:
        raise RegistryCheckError(f"{check.name}: unexpected HTTP status {status}")
    if check.kind == "exact":
        return True
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegistryCheckError(f"{check.name}: invalid JSON response") from error
    if check.kind == "packagist":
        packages = document.get("packages", {})
        versions = packages.get("spearsystems/lcp-sdk", [])
        return any(str(item.get("version", "")).lstrip("v") == version for item in versions)
    raise RegistryCheckError(f"{check.name}: unsupported check type {check.kind}")


def check_version_available(version: str, *, timeout: float = 15.0) -> list[str]:
    """Return registry names which already contain *version*.

    A registry outage raises ``RegistryCheckError`` rather than returning an
    empty list, because availability cannot be inferred safely from failure.
    """

    existing: list[str] = []
    for check in registry_checks(version):
        status, body = fetch(check, timeout=timeout)
        present = contains_version(check, status, body, version)
        state = "occupied" if present else "available"
        print(f"{check.name}: {state} (HTTP {status})")
        if present:
            existing.append(check.name)
    return existing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="proposed SemVer without the leading v")
    parser.add_argument(
        "--expect-absent",
        action="store_true",
        help="fail if any checked registry already contains the proposed version",
    )
    parser.add_argument(
        "--expect-present",
        action="store_true",
        help="fail unless every checked registry contains the proposed version",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    if args.expect_absent and args.expect_present:
        print("--expect-absent and --expect-present are mutually exclusive", file=sys.stderr)
        return 2
    try:
        existing = check_version_available(args.version, timeout=args.timeout)
    except RegistryCheckError as error:
        print(f"release registry validation failed: {error}", file=sys.stderr)
        return 1
    if args.expect_absent and existing:
        print(
            "proposed version is already occupied in: " + ", ".join(existing),
            file=sys.stderr,
        )
        return 1
    if args.expect_present:
        missing = [check.name for check in registry_checks(args.version) if check.name not in existing]
        if missing:
            print(
                "proposed version is missing from: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 1
    print(f"release registry validation passed for {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

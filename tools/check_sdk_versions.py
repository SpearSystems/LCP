#!/usr/bin/env python3
"""Check that package metadata agrees with the coordinated SDK_VERSION."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_version(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.parse_args()
    expected = read_version(Path("SDK_VERSION"))
    checks = {
        "python": re.search(r'^version = "([^"]+)"', read_version(Path("implementations/sdk/python/pyproject.toml")), re.MULTILINE),
        "typescript": json.loads(read_version(Path("implementations/sdk/typescript/package.json")))["version"],
        "csharp": re.search(r"<Version>([^<]+)</Version>", read_version(Path("implementations/sdk/csharp/src/LcpSdk.csproj"))),
        "java": re.search(r"<version>([^<]+)</version>", read_version(Path("implementations/sdk/java/pom.xml"))),
        "rust": re.search(r'^version = "([^"]+)"', read_version(Path("implementations/sdk/rust/Cargo.toml")), re.MULTILINE),
        "ruby": re.search(r'spec.version = "([^"]+)"', read_version(Path("implementations/sdk/ruby/lcp_sdk.gemspec"))),
        "kotlin": re.search(r'^version = "([^"]+)"', read_version(Path("implementations/sdk/kotlin/build.gradle.kts")), re.MULTILINE),
    }
    errors = []
    for name, value in checks.items():
        actual = value.group(1) if hasattr(value, "group") else value
        if actual != expected:
            errors.append(f"{name}: {actual!r} does not match SDK_VERSION {expected!r}")
    if errors:
        print("SDK version synchronization failed:\n- " + "\n- ".join(errors))
        return 1
    print(f"SDK version synchronization passed: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

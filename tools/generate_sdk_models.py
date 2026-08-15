#!/usr/bin/env python3
"""Generate the portable schema bundle used by every published SDK.

Canonical schemas live in ``schemas/`` and ``verticals/``.  SDK source trees
keep reviewed typed model declarations, while this tool creates the exact
runtime schema bundle and manifest at release time.  Keeping the bundle
creation here prevents one language package from silently shipping a different
validation contract from another.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SDK_DIR = ROOT / "implementations" / "sdk"
SDK_NAMES = ("python", "typescript", "go", "csharp", "java", "php", "rust", "ruby", "kotlin", "swift")
BUNDLE_FILES = {
    "python": "lcp_sdk/schema-bundle.json",
    "typescript": "src/generated/schema-bundle.json",
    "go": "schema-bundle.json",
    "csharp": "schema-bundle.json",
    "java": "src/main/resources/lcp/schema-bundle.json",
    "php": "resources/schema-bundle.json",
    "rust": "schema-bundle.json",
    "ruby": "lib/schema-bundle.json",
    "kotlin": "src/main/resources/lcp/schema-bundle.json",
    "swift": "Sources/LCP/Resources/schema-bundle.json",
}
MANIFEST_FILES = {"typescript": "src/generated/schema-manifest.json"}


def canonical_files() -> list[Path]:
    return sorted([*ROOT.joinpath("schemas").glob("*.json"), *ROOT.joinpath("verticals").glob("*.json")])


def expected_manifest() -> dict[str, Any]:
    return {
        "protocol_version": "1.0.0",
        "schema_files": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in canonical_files()
        },
    }


def schema_bundle() -> dict[str, dict[str, Any]]:
    return {
        str(path.relative_to(ROOT)): json.loads(path.read_text(encoding="utf-8"))
        for path in canonical_files()
    }


def bundle_path(name: str) -> Path:
    return SDK_DIR / name / BUNDLE_FILES.get(name, "generated/schema-bundle.json")


def manifest_path(name: str) -> Path:
    return SDK_DIR / name / MANIFEST_FILES.get(name, "generated/schema-manifest.json")


def write_outputs() -> None:
    manifest = expected_manifest()
    bundle = schema_bundle()
    for name in SDK_NAMES:
        output = bundle_path(name)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_path(name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_outputs() -> int:
    from check_sdk_schema_sync import main as check_main

    return check_main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write bundles and manifests into every SDK")
    parser.add_argument("--check", action="store_true", help="verify checked-in manifests and model declarations")
    options = parser.parse_args()
    if options.write:
        write_outputs()
    raise SystemExit(check_outputs() if options.check or not options.write else 0)

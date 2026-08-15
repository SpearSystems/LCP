#!/usr/bin/env python3
"""Verify that every SDK is synchronized with the canonical LCP schemas."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK_DIR = ROOT / "implementations" / "sdk"
SDK_NAMES = ("python", "typescript", "go", "csharp", "java", "php", "rust", "ruby", "kotlin", "swift")
MANIFEST_FILES = {"typescript": "src/generated/schema-manifest.json"}
MODEL_FILES = {
    "python": "generated/models.py",
    "typescript": "src/generated/models.ts",
    "go": "generated_models.go",
    "csharp": "src/GeneratedModels.cs",
    "java": "src/main/java/com/spearsystems/lcp/GeneratedModels.java",
    "php": "src/GeneratedModels.php",
    "rust": "src/generated_models.rs",
    "ruby": "lib/generated_models.rb",
    "kotlin": "src/main/kotlin/com/spearsystems/lcp/GeneratedModels.kt",
    "swift": "Sources/LCP/GeneratedModels.swift",
}
REQUIRED_MODELS = ("Lead", "Call", "Ping", "Post", "Bid", "Ack", "Event", "Offer")


def expected_manifest() -> dict[str, object]:
    files = sorted(list((ROOT / "schemas").glob("*.json")) + list((ROOT / "verticals").glob("*.json")))
    return {
        "protocol_version": "1.0.0",
        "schema_files": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify checked-in manifests and model markers")
    parser.parse_args()
    manifest = expected_manifest()
    errors: list[str] = []
    for name in SDK_NAMES:
        sdk = SDK_DIR / name
        manifest_path = sdk / MANIFEST_FILES.get(name, "generated/schema-manifest.json")
        if not manifest_path.exists():
            errors.append(f"{name}: missing {manifest_path.relative_to(ROOT)}")
        else:
            try:
                actual = json.loads(manifest_path.read_text())
            except json.JSONDecodeError as error:
                errors.append(f"{name}: invalid manifest: {error}")
            else:
                if actual != manifest:
                    errors.append(f"{name}: schema-manifest.json is stale; regenerate typed models")
        model_path = sdk / MODEL_FILES[name]
        if not model_path.exists():
            errors.append(f"{name}: missing generated model file {model_path.relative_to(ROOT)}")
            continue
        source = model_path.read_text(errors="replace").upper()
        if "GENERATED FROM SCHEMAS/" not in source:
            errors.append(f"{name}: generated model marker missing from {model_path.relative_to(ROOT)}")
        for model in REQUIRED_MODELS:
            if model.upper() not in source:
                errors.append(f"{name}: generated models omit {model} schema")
        bundle_files = {
            "python": "lcp_sdk/schema-bundle.json", "typescript": "src/generated/schema-bundle.json",
            "go": "schema-bundle.json", "csharp": "schema-bundle.json",
            "java": "src/main/resources/lcp/schema-bundle.json", "php": "resources/schema-bundle.json",
            "rust": "schema-bundle.json", "ruby": "lib/schema-bundle.json",
            "kotlin": "src/main/resources/lcp/schema-bundle.json", "swift": "Sources/LCP/Resources/schema-bundle.json",
        }
        bundle_path = sdk / bundle_files[name]
        if not bundle_path.exists():
            errors.append(f"{name}: missing schema bundle {bundle_path.relative_to(ROOT)}")
        else:
            try:
                bundle = json.loads(bundle_path.read_text())
                if sorted(bundle) != sorted(manifest["schema_files"]):
                    errors.append(f"{name}: schema-bundle.json does not contain every canonical schema")
            except json.JSONDecodeError as error:
                errors.append(f"{name}: invalid schema bundle: {error}")
        if name == "python":
            for src in sorted([*(ROOT / "schemas").glob("*.json"), *(ROOT / "verticals").glob("*.json")]):
                loose = sdk / "lcp_sdk" / src.relative_to(ROOT)
                if not loose.exists() or loose.read_bytes() != src.read_bytes():
                    errors.append(f"python: loose schema copy {loose.relative_to(ROOT)} is stale; regenerate")
    if errors:
        print("SDK schema synchronization failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"SDK schema synchronization passed for {len(SDK_NAMES)} SDKs and {len(manifest['schema_files'])} schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

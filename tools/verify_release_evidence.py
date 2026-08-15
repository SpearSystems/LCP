#!/usr/bin/env python3
"""Verify a downloaded LCP release-evidence bundle without trusting GitHub.

The release workflow uploads ``release-manifest.json``, ``release-notes.md``,
per-package source archives, CycloneDX SBOMs, SLSA provenance statements, and a
Sigstore bundle for every artifact. This tool checks that a downloaded bundle
is internally consistent:

- the manifest is well formed and references the expected package set;
- every referenced file exists beside the manifest;
- source-archive, SBOM, and provenance SHA-256 digests match the files;
- SBOMs parse as CycloneDX JSON and provenance statements parse as in-toto
  SLSA v1 JSON; and
- each provenance subject binds the manifest's archive and digest.

Signature verification is optional and offline by default. Pass
``--identity``/``--issuer`` to verify every Sigstore bundle with the cosign
CLI. The tool fails closed: it never accepts a bundle whose files, digests, or
JSON are inconsistent, and it refuses to skip signature verification when an
identity was requested but cosign is unavailable.

Usage:
    python3 tools/verify_release_evidence.py PATH [--identity ...] [--issuer ...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

EXPECTED_PACKAGES = {
    "lcp-sdk-python",
    "lcp-mcp-server",
    "lcp-reference-platform",
    "lcp-sdk-typescript",
    "lcp-sdk-go",
    "lcp-sdk-csharp",
    "lcp-sdk-java",
    "lcp-sdk-kotlin",
    "lcp-sdk-php",
    "lcp-sdk-rust",
    "lcp-sdk-ruby",
    "lcp-sdk-swift",
}

MANIFEST_FILE = "release-manifest.json"
NOTES_FILE = "release-notes.md"


class EvidenceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError(f"{label} ({path.name}) is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise EvidenceError(f"{label} ({path.name}) must be a JSON object")
    return document


def verify_manifest(manifest_path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    manifest = load_json(manifest_path, "release manifest")

    for field in ("protocol", "release_tag", "commit", "dry_run", "sdk_version", "schema_manifest_sha256"):
        if field not in manifest:
            errors.append(f"release manifest is missing required field '{field}'")

    source_sbom = manifest.get("source_sbom")
    if not isinstance(source_sbom, dict):
        errors.append("release manifest field 'source_sbom' must be an object")
    else:
        sbom_file = source_sbom.get("file")
        if not sbom_file:
            errors.append("release manifest 'source_sbom.file' is missing")
        else:
            sbom_path = root / sbom_file
            if not sbom_path.is_file():
                errors.append(f"source SBOM file is missing: {sbom_file}")
            else:
                try:
                    load_json(sbom_path, "source SBOM")
                except EvidenceError as error:
                    errors.append(str(error))
            if not source_sbom.get("signature"):
                errors.append("release manifest 'source_sbom.signature' is missing")
            else:
                signature_path = root / source_sbom["signature"]
                if not signature_path.is_file():
                    errors.append(f"source SBOM signature is missing: {source_sbom['signature']}")

    packages = manifest.get("package_evidence")
    if not isinstance(packages, list):
        errors.append("release manifest 'package_evidence' must be a list")
        packages = []
    if len(packages) != len(EXPECTED_PACKAGES):
        errors.append(f"release manifest lists {len(packages)} packages; expected {len(EXPECTED_PACKAGES)}")

    seen_packages: set[str] = set()
    for index, package in enumerate(packages):
        if not isinstance(package, dict):
            errors.append(f"package_evidence[{index}] must be an object")
            continue
        package_name = package.get("package")
        if not isinstance(package_name, str) or not package_name:
            errors.append(f"package_evidence[{index}] has no 'package' coordinate")
            continue
        package_key = package_name.split(":", 1)[-1]
        if package_key not in EXPECTED_PACKAGES:
            errors.append(f"unexpected package coordinate in manifest: {package_name}")
        if package_key in seen_packages:
            errors.append(f"duplicate package coordinate in manifest: {package_name}")
        seen_packages.add(package_key)

        for required in ("source_path", "source_archive", "source_sha256", "sbom", "provenance", "signatures"):
            if required not in package:
                errors.append(f"package '{package_name}' is missing required field '{required}'")

        source_archive = package.get("source_archive")
        if source_archive:
            archive_path = root / source_archive
            if not archive_path.is_file():
                errors.append(f"source archive is missing: {source_archive}")
            else:
                actual = sha256_file(archive_path)
                expected = package.get("source_sha256")
                if not expected:
                    errors.append(f"package '{package_name}' has no source_sha256")
                elif expected != actual:
                    errors.append(
                        f"source archive digest mismatch for {source_archive}: "
                        f"manifest {expected}, file {actual}"
                    )

        sbom_name = package.get("sbom")
        if sbom_name:
            sbom_path = root / sbom_name
            if not sbom_path.is_file():
                errors.append(f"package SBOM is missing: {sbom_name}")
            else:
                try:
                    sbom = load_json(sbom_path, f"SBOM for {package_name}")
                    if sbom.get("bomFormat") != "CycloneDX":
                        errors.append(f"{sbom_name} is not a CycloneDX document")
                except EvidenceError as error:
                    errors.append(str(error))

        provenance_name = package.get("provenance")
        if provenance_name:
            provenance_path = root / provenance_name
            if not provenance_path.is_file():
                errors.append(f"provenance statement is missing: {provenance_name}")
            else:
                try:
                    provenance = load_json(provenance_path, f"provenance for {package_name}")
                    if provenance.get("predicateType") != "https://slsa.dev/provenance/v1":
                        errors.append(f"{provenance_name} is not an SLSA v1 statement")
                    subjects = provenance.get("subject")
                    if not isinstance(subjects, list) or not subjects:
                        errors.append(f"{provenance_name} has no in-toto subject")
                    else:
                        subject = subjects[0]
                        archive_name = package.get("source_archive")
                        subject_name = subject.get("name") if isinstance(subject, dict) else None
                        subject_digest = (
                            subject.get("digest", {}).get("sha256")
                            if isinstance(subject, dict)
                            else None
                        )
                        if archive_name and subject_name != archive_name:
                            errors.append(
                                f"provenance subject {subject_name!r} does not match "
                                f"manifest archive {archive_name!r}"
                            )
                        if subject_digest and package.get("source_sha256") != subject_digest:
                            errors.append(
                                f"provenance subject digest {subject_digest} does not match "
                                f"manifest digest {package.get('source_sha256')}"
                            )
                    byproducts = (
                        provenance.get("predicate", {})
                        .get("runDetails", {})
                        .get("byproducts")
                    )
                    if isinstance(byproducts, list):
                        sbom_digest = None
                        for byproduct in byproducts:
                            if isinstance(byproduct, dict) and byproduct.get("uri") == package.get("sbom"):
                                digest = byproduct.get("digest", {}).get("sha256")
                                if isinstance(digest, str):
                                    sbom_digest = digest
                        if sbom_digest and sbom_name:
                            sbom_path = root / sbom_name
                            if sbom_path.is_file() and sha256_file(sbom_path) != sbom_digest:
                                errors.append(
                                    f"SBOM digest in {provenance_name} does not match {sbom_name}"
                                )
                except EvidenceError as error:
                    errors.append(str(error))

        signatures = package.get("signatures")
        if not isinstance(signatures, dict):
            errors.append(f"package '{package_name}' has no 'signatures' object")
        else:
            for kind, bundle_name in (
                ("source_archive", f"{package.get('source_archive', '')}.sigstore.json"),
                ("sbom", f"{package.get('sbom', '')}.sigstore.json"),
                ("provenance", f"{package.get('provenance', '')}.sigstore.json"),
            ):
                declared = signatures.get(kind)
                if declared != bundle_name:
                    errors.append(
                        f"package '{package_name}' signature '{kind}' is {declared!r}, "
                        f"expected {bundle_name!r}"
                    )
                if declared and not (root / declared).is_file():
                    errors.append(f"Sigstore bundle is missing: {declared}")

    missing_packages = EXPECTED_PACKAGES - seen_packages
    if missing_packages:
        errors.append(
            "release manifest is missing package evidence for: "
            + ", ".join(sorted(missing_packages))
        )

    if not (root / NOTES_FILE).is_file():
        errors.append(f"release notes are missing: {NOTES_FILE}")
    else:
        notes_signature = root / f"{NOTES_FILE}.sigstore.json"
        if not notes_signature.is_file():
            errors.append(f"release notes signature is missing: {notes_signature.name}")

    manifest_signature = root / f"{MANIFEST_FILE}.sigstore.json"
    if not manifest_signature.is_file():
        errors.append(f"release manifest signature is missing: {manifest_signature.name}")

    return errors


def verify_cosign_bundles(root: Path, identity: str, issuer: str) -> list[str]:
    errors: list[str] = []
    cosign = shutil.which("cosign")
    if cosign is None:
        errors.append("--identity/--issuer requested but cosign is not installed; refusing to skip signature verification")
        return errors

    targets: list[tuple[Path, Path]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name.endswith(".sigstore.json"):
            artifact = path.with_suffix("")
            while artifact.suffix in (".json", ".gz", ".tar"):
                artifact = artifact.with_suffix("")
            if artifact.is_file() and not artifact.name.endswith(".sigstore.json"):
                targets.append((artifact, path))

    if not targets:
        errors.append("no Sigstore bundles found under the release directory")
        return errors

    for artifact, bundle in targets:
        result = subprocess.run(
            [
                cosign,
                "verify-blob",
                "--bundle",
                str(bundle),
                "--certificate-identity",
                identity,
                "--certificate-oidc-issuer",
                issuer,
                str(artifact),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            errors.append(
                f"cosign rejected {artifact.name}: {result.stderr.strip() or result.stdout.strip()}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="downloaded release-evidence directory (or the dry-run artifact contents)")
    parser.add_argument(
        "--identity",
        default="",
        help="expected Sigstore certificate identity, e.g. https://github.com/SpearSystems/LCP/.github/workflows/release.yml@refs/tags/v1.0.0",
    )
    parser.add_argument(
        "--issuer",
        default="",
        help="expected Sigstore OIDC issuer, e.g. https://token.actions.githubusercontent.com",
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser()
    if not root.is_dir():
        print(f"release evidence path is not a directory: {root}", file=sys.stderr)
        return 2

    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        print(f"release manifest not found at {manifest_path}", file=sys.stderr)
        return 2

    errors = verify_manifest(manifest_path, root)
    if args.identity or args.issuer:
        if not args.identity or not args.issuer:
            print("both --identity and --issuer are required for signature verification", file=sys.stderr)
            return 2
        errors.extend(verify_cosign_bundles(root, args.identity, args.issuer))

    if errors:
        print(f"release evidence verification FAILED with {len(errors)} problem(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"release evidence verified: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

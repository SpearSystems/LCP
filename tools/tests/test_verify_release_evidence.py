import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from verify_release_evidence import (  # noqa: E402
    EXPECTED_PACKAGES,
    EvidenceError,
    check_stale_release_state,
    sha256_file,
    verify_manifest,
)


def build_fixture_release(root: Path, *, tamper_manifest_digest: bool = False, dry_run: bool = True) -> Path:
    """Create a minimal but structurally complete release-evidence bundle."""
    manifest_dir = root / "release"
    manifest_dir.mkdir(parents=True)

    for name in EXPECTED_PACKAGES:
        archive = manifest_dir / f"{name}-0.1.0.source.tar.gz"
        archive.write_bytes(b"archive:" + name.encode())
        sbom = manifest_dir / f"{name}-v0.1.0.cdx.json"
        sbom.write_text(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.5",
                    "serialNumber": "urn:uuid:00000000-0000-4000-8000-000000000000",
                    "version": 1,
                    "components": [],
                }
            )
            + "\n"
        )
        provenance = manifest_dir / f"{name}-v0.1.0.provenance.json"
        provenance.write_text(
            json.dumps(
                {
                    "_type": "https://in-toto.io/Statement/v1",
                    "subject": [
                        {
                            "name": archive.name,
                            "digest": {"sha256": sha256_file(archive)},
                        }
                    ],
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "predicate": {
                        "buildDefinition": {
                            "buildType": "https://github.com/SpearSystems/LCP/.github/workflows/release.yml",
                            "externalParameters": {
                                "release_tag": "v0.1.0",
                                "package": f"PyPI:{name}",
                                "source_path": f"implementations/{name}",
                            },
                        },
                        "runDetails": {
                            "builder": {
                                "id": "https://github.com/SpearSystems/LCP/.github/workflows/release.yml"
                            },
                            "metadata": {"invocationId": "12345"},
                            "byproducts": [
                                {
                                    "uri": sbom.name,
                                    "digest": {"sha256": sha256_file(sbom)},
                                }
                            ],
                        },
                    },
                }
            )
            + "\n"
        )
        for artifact in (archive, sbom, provenance):
            (manifest_dir / (artifact.name + ".sigstore.json")).write_text('{"fake":"bundle"}\n')

    source_sbom = manifest_dir / "lcp-source-sbom.cdx.json"
    source_sbom.write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []}) + "\n"
    )
    (manifest_dir / "lcp-source-sbom.cdx.json.sigstore.json").write_text('{"fake":"bundle"}\n')

    (manifest_dir / "release-notes.md").write_text("# LCP v0.1.0 dry run\n")
    (manifest_dir / "release-notes.md.sigstore.json").write_text('{"fake":"bundle"}\n')

    packages = []
    for name in sorted(EXPECTED_PACKAGES):
        archive = manifest_dir / f"{name}-0.1.0.source.tar.gz"
        sbom = manifest_dir / f"{name}-v0.1.0.cdx.json"
        provenance = manifest_dir / f"{name}-v0.1.0.provenance.json"
        manifest_digest = sha256_file(archive)
        if tamper_manifest_digest and name == "lcp-sdk-python":
            manifest_digest = "0" * 64
        packages.append(
            {
                "package": f"PyPI:{name}",
                "source_path": f"implementations/{name}",
                "source_archive": archive.name,
                "source_sha256": manifest_digest,
                "sbom": sbom.name,
                "provenance": provenance.name,
                "signatures": {
                    "source_archive": archive.name + ".sigstore.json",
                    "sbom": sbom.name + ".sigstore.json",
                    "provenance": provenance.name + ".sigstore.json",
                },
            }
        )

    container_metadata = {
        "image": "ghcr.io/spearsystems/lcp-reference-platform",
        "tag": "v0.1.0",
        "digest": "sha256:" + "d" * 64,
        "reference": "ghcr.io/spearsystems/lcp-reference-platform@sha256:" + "d" * 64,
        "commit": "c" * 40,
        "workflow_run_id": "123456",
        "dry_run": dry_run,
    }
    (manifest_dir / "lcp-container-release.json").write_text(
        json.dumps(container_metadata, indent=2, sort_keys=True) + "\n"
    )
    manifest = {
        "protocol": "LCP",
        "release_tag": "v0.1.0",
        "commit": "c" * 40,
        "dry_run": dry_run,
        "sdk_version": "0.1.0",
        "schema_manifest_sha256": "a" * 64,
        "release_workflow": "https://github.com/SpearSystems/LCP/actions/workflows/release.yml",
        "source_sbom": {
            "file": "lcp-source-sbom.cdx.json",
            "signature": "lcp-source-sbom.cdx.json.sigstore.json",
        },
        "package_evidence": packages,
        "container": {**container_metadata, "metadata": "lcp-container-release.json"},
    }
    (manifest_dir / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (manifest_dir / "release-manifest.json.sigstore.json").write_text('{"fake":"bundle"}\n')
    return manifest_dir


class StaleReleaseStateTests(unittest.TestCase):
    def _write(self, root: Path, relative: str, text: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_stale_phrases_are_reported_with_file_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "README.md", "# LCP\n\nThe v1.0.1 release is not yet published.\n")
            self._write(root, "docs/RELEASE.md", "Tag v1.0.1 has not been tagged yet.\n")
            problems = check_stale_release_state(root)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("README.md:3" in p and "not yet published" in p for p in problems))
        self.assertTrue(any("docs/RELEASE.md:1" in p and "has not been tagged" in p for p in problems))

    def test_clean_docs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "README.md", "# LCP\n\nv1.0.1 is published. New features arrive in future releases.\n")
            self._write(root, "docs/RELEASE.md", "Release evidence verified.\n")
            self._write(root, "CHANGELOG.md", "- Released v1.0.1.\n")
            self.assertEqual(check_stale_release_state(root), [])

    def test_missing_repo_root_is_reported(self) -> None:
        problems = check_stale_release_state(Path("/definitely/not/a/real/path"))
        self.assertEqual(len(problems), 1)
        self.assertIn("not a directory", problems[0])

    def test_repository_docs_are_clean_today(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.assertEqual(check_stale_release_state(repo_root), [])


class VerifyReleaseEvidenceTests(unittest.TestCase):
    def test_sha256_file_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob"
            path.write_bytes(b"lcp-evidence")
            self.assertEqual(
                sha256_file(path), hashlib.sha256(b"lcp-evidence").hexdigest()
            )

    def test_complete_fixture_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_dir = build_fixture_release(Path(tmp))
            self.assertEqual(verify_manifest(manifest_dir / "release-manifest.json", manifest_dir), [])

    def test_missing_package_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_dir = build_fixture_release(Path(tmp))
            (manifest_dir / "lcp-sdk-go-0.1.0.source.tar.gz").unlink()
            errors = verify_manifest(manifest_dir / "release-manifest.json", manifest_dir)
            self.assertTrue(any("lcp-sdk-go" in error for error in errors))
            self.assertTrue(any("source archive is missing" in error for error in errors))

    def test_tampered_manifest_digest_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_dir = build_fixture_release(Path(tmp), tamper_manifest_digest=True)
            errors = verify_manifest(manifest_dir / "release-manifest.json", manifest_dir)
            self.assertTrue(any("digest mismatch" in error for error in errors))

    def test_real_release_container_metadata_is_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_dir = build_fixture_release(Path(tmp), dry_run=False)
            self.assertEqual(verify_manifest(manifest_dir / "release-manifest.json", manifest_dir), [])

    def test_real_release_requires_container_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_dir = build_fixture_release(Path(tmp), dry_run=False)
            (manifest_dir / "lcp-container-release.json").unlink()
            errors = verify_manifest(manifest_dir / "release-manifest.json", manifest_dir)
            self.assertTrue(any("container metadata is missing" in error for error in errors))

    def test_missing_manifest_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(EvidenceError):
                verify_manifest(Path(tmp) / "missing.json", Path(tmp))


if __name__ == "__main__":
    unittest.main()

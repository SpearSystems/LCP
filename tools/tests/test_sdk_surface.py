"""Unit tests for tools/check_sdk_surface.py.

Fixtures are built in temporary directories so the gate's failure paths are
proven without touching the real SDK tree; one test then asserts the checked-in
manifest is satisfied by the repository as it stands.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1]))

import check_sdk_surface as surface  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

PY_FILE = "lcp/client.py"
TS_FILE = "src/index.ts"
PY_SOURCE = "def submit_lead(envelope):\n    return envelope\n"
TS_SOURCE = (
    "export class Client {\n  submitLead(envelope: object) { return envelope; }\n}\n"
)


def manifest_for(members: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One-operation manifest covering the two fixture SDKs."""
    return {
        "schema_version": 1,
        "sdk_root": "implementations/sdk",
        "operations": [
            {
                "id": "submit_lead",
                "endpoint": "POST /v1/lcp/leads",
                "members": {
                    "python": {"file": PY_FILE, "name": "submit_lead"},
                    "typescript": {"file": TS_FILE, "name": "submitLead"},
                    **members,
                },
            }
        ],
    }


class Fixture:
    """A throwaway repository root with two SDKs and a manifest."""

    def __init__(self, members: dict[str, dict[str, Any]] | None = None) -> None:
        self._tmp: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.root: Path = Path(self._tmp.name)
        (self.root / "implementations" / "sdk" / "python").mkdir(parents=True)
        (self.root / "implementations" / "sdk" / "typescript").mkdir(parents=True)
        (self.root / "tools").mkdir()
        self.write("python", PY_FILE, PY_SOURCE)
        self.write("typescript", TS_FILE, TS_SOURCE)
        self.manifest: dict[str, Any] = manifest_for(members or {})
        self.write_manifest()

    def path(self, sdk: str, name: str) -> Path:
        return self.root / "implementations" / "sdk" / sdk / name

    def write(self, sdk: str, name: str, text: str) -> None:
        target = self.path(sdk, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def write_manifest(self) -> None:
        (self.root / "tools" / "sdk_surface.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def run(self, *args: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = surface.main(["--root", str(self.root), *args])
        return code, buffer.getvalue()

    def cleanup(self) -> None:
        self._tmp.cleanup()


class SurfaceCheckTests(unittest.TestCase):
    def fixture(
        self, members: dict[str, dict[str, Any]] | None = None
    ) -> Fixture:
        built = Fixture(members)
        self.addCleanup(built.cleanup)
        return built

    def test_present_members_pass(self):
        built = self.fixture()
        code, output = built.run()
        self.assertEqual(code, 0, output)
        self.assertIn("ok", output)
        self.assertNotIn("MISS", output)
        self.assertIn("2 present", output)

    def test_missing_member_fails_and_names_sdk_member_file(self):
        built = self.fixture()
        built.write("typescript", TS_FILE, "export class Client {}\n")
        code, output = built.run()
        self.assertEqual(code, 1)
        self.assertIn("typescript", output)
        self.assertIn("submitLead", output)
        self.assertIn(TS_FILE, output)
        self.assertIn("MISS", output)

    def test_case_sensitive_match(self):
        built = self.fixture()
        built.write("python", PY_FILE, "def Submit_Lead(envelope):\n    ...\n")
        code, output = built.run()
        self.assertEqual(code, 1)
        self.assertIn("submit_lead", output)

    def test_missing_file_fails(self):
        built = self.fixture()
        built.path("python", PY_FILE).unlink()
        code, output = built.run()
        self.assertEqual(code, 1)
        self.assertIn("python", output)
        self.assertIn(PY_FILE, output)

    def test_gap_is_reported_but_passes(self):
        built = self.fixture(
            {"typescript": {"file": TS_FILE, "name": None, "note": "not shipped yet"}}
        )
        code, output = built.run()
        self.assertEqual(code, 0, output)
        self.assertIn("GAP", output)
        self.assertIn("not shipped yet", output)

    def test_fail_on_gap_flips_exit(self):
        built = self.fixture(
            {"typescript": {"file": TS_FILE, "name": None, "note": "not shipped yet"}}
        )
        code, output = built.run("--fail-on-gap")
        self.assertEqual(code, 1, output)

    def test_manifest_must_cover_every_sdk_directory(self):
        built = self.fixture()
        del built.manifest["operations"][0]["members"]["typescript"]
        built.write_manifest()
        code, output = built.run()
        self.assertEqual(code, 2)
        self.assertIn("typescript", output)

    def test_unknown_sdk_directory_is_a_manifest_error(self):
        built = self.fixture()
        (built.root / "implementations" / "sdk" / "dart").mkdir()
        code, output = built.run()
        self.assertEqual(code, 2)
        self.assertIn("dart", output)

    def test_json_report(self):
        built = self.fixture(
            {"typescript": {"file": TS_FILE, "name": None, "note": "not shipped yet"}}
        )
        code, output = built.run("--json")
        self.assertEqual(code, 0, output)
        data = json.loads(output)
        self.assertEqual(data["operations"], ["submit_lead"])
        self.assertEqual(data["sdks"], ["python", "typescript"])
        self.assertEqual(
            data["counts"], {"present": 1, "gap": 1, "missing": 0, "total": 2}
        )
        self.assertEqual(
            data["cells"]["submit_lead"], {"python": "ok", "typescript": "GAP"}
        )
        self.assertEqual([gap["sdk"] for gap in data["gaps"]], ["typescript"])
        self.assertEqual(data["missing"], [])

    def test_json_error_report(self):
        built = self.fixture()
        (built.root / "tools" / "sdk_surface.json").write_text("{", encoding="utf-8")
        code, output = built.run("--json")
        self.assertEqual(code, 2)
        self.assertIn("error", json.loads(output))


class RepositoryManifestTests(unittest.TestCase):
    """The checked-in manifest must describe the tree as it is today."""

    def _repository_report(self) -> tuple[int, dict[str, Any]]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = surface.main(["--root", str(REPO_ROOT), "--json"])
        if code == 2:
            self.fail(f"manifest is unusable: {buffer.getvalue()}")
        return code, json.loads(buffer.getvalue())

    def test_repository_manifest_is_satisfied(self):
        code, data = self._repository_report()
        self.assertEqual(code, 0, data["missing"])
        self.assertEqual(data["missing"], [])
        self.assertEqual(len(data["sdks"]), 10)
        self.assertGreaterEqual(len(data["operations"]), 7)

    def test_repository_matrix_is_complete(self):
        _code, data = self._repository_report()
        self.assertEqual(
            data["counts"]["total"],
            len(data["sdks"]) * len(data["operations"]),
        )
        for operation in data["operations"]:
            self.assertEqual(sorted(data["cells"][operation]), sorted(data["sdks"]))


if __name__ == "__main__":
    unittest.main()

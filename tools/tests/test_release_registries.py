import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from check_release_registries import (  # noqa: E402
    RegistryCheckError,
    contains_version,
    registry_checks,
)


class ReleaseRegistryTests(unittest.TestCase):
    def test_checks_cover_published_registry_families(self):
        checks = registry_checks("0.1.0")
        names = {check.name for check in checks}
        self.assertIn("PyPI lcp-sdk", names)
        self.assertIn("PyPI lcp-mcp-server", names)
        self.assertIn("PyPI lcp-reference-platform", names)
        self.assertIn("npm @spearsystems/lcp-sdk", names)
        self.assertIn("Maven Central Java/Kotlin", names)
        self.assertIn("Packagist spearsystems/lcp-sdk", names)
        self.assertTrue(any("0.1.0" in check.url for check in checks))

    def test_exact_registry_is_present_only_on_http_200(self):
        check = registry_checks("0.1.0")[0]
        self.assertFalse(contains_version(check, 404, b"", "0.1.0"))
        self.assertTrue(contains_version(check, 200, b"{}", "0.1.0"))
        with self.assertRaises(RegistryCheckError):
            contains_version(check, 503, b"", "0.1.0")

    def test_maven_response_is_version_specific(self):
        check = next(item for item in registry_checks("0.1.0") if item.kind == "maven")
        self.assertTrue(contains_version(check, 200, b'{"response":{"numFound":1}}', "0.1.0"))
        self.assertFalse(contains_version(check, 200, b'{"response":{"numFound":0}}', "0.1.0"))

    def test_packagist_response_is_version_specific(self):
        check = next(item for item in registry_checks("0.1.0") if item.kind == "packagist")
        body = json.dumps(
            {"packages": {"spearsystems/lcp-sdk": [{"version": "0.0.9"}, {"version": "0.1.0"}]}}
        ).encode()
        self.assertTrue(contains_version(check, 200, body, "0.1.0"))
        self.assertFalse(contains_version(check, 200, body, "0.1.1"))

    def test_expect_present_mode_requires_all_registries(self):
        from unittest import mock

        from check_release_registries import main

        all_names = [check.name for check in registry_checks("0.1.0")]
        with mock.patch("check_release_registries.check_version_available", return_value=[]):
            # No registry contains the version yet: probe fails closed.
            self.assertEqual(main(["--version", "0.1.0", "--expect-present"]), 1)
        with mock.patch(
            "check_release_registries.check_version_available", return_value=all_names
        ):
            # Every registry contains the version: probe passes.
            self.assertEqual(main(["--version", "0.1.0", "--expect-present"]), 0)

    def test_expect_absent_and_present_are_mutually_exclusive(self):
        from unittest import mock

        from check_release_registries import main

        with mock.patch("check_release_registries.check_version_available") as fetch:
            # Rejected before any registry work happens.
            self.assertEqual(
                main(["--version", "0.1.0", "--expect-absent", "--expect-present"]), 2
            )
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

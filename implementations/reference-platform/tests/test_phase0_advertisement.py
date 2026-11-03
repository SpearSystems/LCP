"""Phase 0 (LEP-0008, ratified 2026-11-03): the conformance advertisement must
match what the platform actually verifies.

SPEC §7 defines conformance L3 as *agent binding (attestation verification)*.
At this commit the reference platform validates the attestation's **shape**
only — a well-formed JWT-shaped string, a forged HMAC, and an expired
attestation all ingest (reproduced in issue #192 at several audited SHAs).
Advertising ``conformance_level: "L3"`` is therefore a false conformance
claim, and Phase 0 of the accepted LEP-0008 record requires advertising
``"L2"`` until enforce-mode verification is live and green in CI.

When attestation verification ships, this test is the deliberate gate: L3 may
only be advertised again by changing this expectation in the same PR that
lands the V13–V24 verification gates.
"""

from pathlib import Path
import unittest

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform

ROOT = Path(__file__).resolve().parents[3]


class Phase0AdvertisementTests(unittest.TestCase):
    def _platform(self) -> Platform:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        self.addCleanup(platform.close)
        return platform

    def test_conformance_level_is_l2_until_attestation_verification_is_live(
        self,
    ) -> None:
        capabilities = self._platform().capabilities()
        self.assertEqual(
            capabilities["conformance_level"],
            "L2",
            "L3 means agent binding (attestation verification) per SPEC §7; the "
            "platform verifies shape only today (#192), so L3 must not be "
            "advertised. Change this expectation only together with the "
            "LEP-0008 verification implementation (V13–V24).",
        )

    def test_advertised_level_vocabulary_is_bounded(self) -> None:
        level = self._platform().capabilities()["conformance_level"]
        self.assertIn(level, {"L1", "L2", "L3"})


if __name__ == "__main__":
    unittest.main()

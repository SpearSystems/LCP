"""LEP-0008 §9 advertisement honesty: the conformance level must match what the
platform actually enforces.

SPEC §7 defines conformance L3 as *agent binding (attestation verification)*.
Before LEP-0008's implementation the platform validated the attestation's
**shape** only while advertising L3 — a false conformance claim (issue #192:
forged and free-text attestations ingested). Phase 0 of the accepted record
downgraded the advertisement to L2 until verification was live; the
implementation now verifies, and the advertised level follows the enforced
mode: ``enforce`` is L3, ``observe`` verifies-and-records but accepts, so it
stays L2.

This pin is the gate on both directions: a future change that stops verifying
must not keep advertising L3.
"""

from pathlib import Path
import unittest

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform

ROOT = Path(__file__).resolve().parents[3]


class ConformanceAdvertisementTests(unittest.TestCase):
    def _platform(self, *, mode: str = "enforce") -> Platform:
        platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=True,
                allow_insecure_webhooks=True,
                test_mode=True,
                attestation_mode=mode,
            )
        )
        self.addCleanup(platform.close)
        return platform

    def test_enforce_mode_advertises_l3(self) -> None:
        capabilities = self._platform(mode="enforce").capabilities()
        self.assertEqual(
            capabilities["conformance_level"],
            "L3",
            "L3 means agent binding (attestation verification) per SPEC §7; it "
            "may be advertised only while enforce-mode verification is live.",
        )

    def test_observe_mode_does_not_advertise_l3(self) -> None:
        capabilities = self._platform(mode="observe").capabilities()
        self.assertEqual(
            capabilities["conformance_level"],
            "L2",
            "observe verifies and records but accepts, so it is not L3 "
            "(LEP-0008 §9 advertisement honesty).",
        )

    def test_advertised_level_vocabulary_is_bounded(self) -> None:
        level = self._platform().capabilities()["conformance_level"]
        self.assertIn(level, {"L1", "L2", "L3"})


if __name__ == "__main__":
    unittest.main()

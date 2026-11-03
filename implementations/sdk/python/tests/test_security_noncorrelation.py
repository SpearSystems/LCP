"""SPEC-COVERAGE SEC-08 gap closure on the Python SDK: per-pair non-correlation.

SPEC.md §9 Phone hash (dedup), verbatim (SPEC @
ace8861561f4dbe80bca17b42d41b6cca453df6c):

    "`phone_hash = HMAC-SHA256(shared_secret, normalized_e164)"
    "- The `shared_secret` is **per-pair** — the publisher hashes with a
      secret shared with each specific buyer. Each buyer receives a
      different hash for the same phone number."
    "- **Purpose:** dedup within a single buyer's stream. Not a
      cross-buyer dedup mechanism (different buyers see different hashes)."
    "- **Privacy property:** an attacker who intercepts a ping cannot
      reverse the hash without the shared secret. Unlike unsalted SHA-256,
      a precomputed rainbow table over the phone-number keyspace is
      useless."

And §9 Contact hashes, "Re-keying requires the plaintext":

    "HMAC of a *received* digest under another pair's secret is not a
    re-key; a platform may re-key only from contact values it holds in
    plaintext (the leads it delivered), never from reported digests."

The pinned rows of ``test-vectors/sdk/contact-hash.json`` are consumed as
the single-secret baseline; the gap this file closes is the *second-secret*
computation: the same plaintext phone under two different pair secrets must
produce two distinct digests, neither may equal the other's (or any
unsalted SHA-256/SHA-1 of the preimage), and a received pair-A digest must
not be re-keyable into pair B's digest by hashing the digest itself.

Deterministic and network-free: pure stdlib ``hmac``/``hashlib`` recomputation
alongside ``lcp_sdk.contact_hash``; no transports, no sleeps.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from pathlib import Path

from lcp_sdk import contact_hash
from lcp_sdk.signing import normalize_contact

ROOT = Path(__file__).resolve().parents[4]
VECTORS = ROOT / "test-vectors" / "sdk" / "contact-hash.json"

# The second pair secret: an added computation over the SAME plaintexts the
# pinned vectors use, under a different bilateral secret.
SECOND_PAIR_SECRET = "second-pair-secret-klmnopqrstuvwxyz012345"


def _recompute(secret: str, preimage: str) -> str:
    """The §9 pinned recompute rule, independent of the SDK helper."""
    return hmac.new(
        secret.encode("utf-8"), preimage.encode("utf-8"), hashlib.sha256
    ).hexdigest()


class PerPairNonCorrelationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.vector_secret = self.suite["secret"]
        self.rows = {row["domain"]: row for row in self.suite["vectors"]}

    def test_pinned_rows_are_the_baseline(self) -> None:
        # The per-pair rule is only meaningful if the pinned baseline holds:
        # reproduce every row under its own pair secret first.
        for domain, row in self.rows.items():
            with self.subTest(domain=domain):
                self.assertEqual(
                    contact_hash(row["secret"], row["preimage"], domain=domain),
                    row["digest"],
                )

    def test_same_phone_under_two_pair_secrets_gives_two_distinct_digests(self) -> None:
        row = self.rows["phone"]
        digest_a = contact_hash(self.vector_secret, row["preimage"], domain="phone")
        digest_b = contact_hash(SECOND_PAIR_SECRET, row["preimage"], domain="phone")

        # Two distinct digests...
        self.assertNotEqual(digest_a, digest_b)
        # ...and neither equals the other's (cross-pair non-correlation in
        # both directions: each is exactly its own key's HMAC over the same
        # plaintext, nothing more).
        self.assertEqual(digest_a, _recompute(self.vector_secret, row["preimage"]))
        self.assertEqual(digest_b, _recompute(SECOND_PAIR_SECRET, row["preimage"]))
        self.assertNotEqual(digest_b, row["digest"])

        # A third buyer, third secret: still a third, unrelated digest.
        digest_c = contact_hash(
            "third-pair-secret-BBCDEFGHIJKLMNOPQRS0123456",
            row["preimage"],
            domain="phone",
        )
        self.assertNotIn(digest_c, {digest_a, digest_b})

    def test_digests_are_not_unsalted_digests_of_the_phone(self) -> None:
        # §9 privacy property: the per-pair HMAC is not invertible by a
        # precomputed table over the phone keyspace; unsalted SHA-256 (the
        # named attack) and SHA-1 (a conformance failure anywhere in a
        # contact-hash path) must not appear on any pair's output.
        row = self.rows["phone"]
        unsalted_sha256 = hashlib.sha256(row["preimage"].encode("utf-8")).hexdigest()
        unsalted_sha1 = hashlib.sha1(row["preimage"].encode("utf-8")).hexdigest()
        for secret in (self.vector_secret, SECOND_PAIR_SECRET):
            digest = contact_hash(secret, row["preimage"], domain="phone")
            self.assertNotEqual(digest, unsalted_sha256)
            self.assertNotEqual(digest, unsalted_sha1)

    def test_received_digest_cannot_be_re_keyed_for_another_pair(self) -> None:
        # §9 "Re-keying requires the plaintext": hashing a received pair-A
        # digest under pair B's secret is NOT a re-key — it must not produce
        # what pair B's real contact digest is for the same phone.
        row = self.rows["phone"]
        digest_a = contact_hash(self.vector_secret, row["preimage"], domain="phone")
        digest_b = contact_hash(SECOND_PAIR_SECRET, row["preimage"], domain="phone")
        forged_rekey = contact_hash(SECOND_PAIR_SECRET, digest_a, domain="phone")
        self.assertNotEqual(forged_rekey, digest_b)

    def test_email_preimages_split_per_pair_too(self) -> None:
        # Same rule for the contact-hash model's email domain (computing
        # only; §9 keeps email-form *emission* gated — see
        # `contacted_hashes` in the post schema). The digests stay distinct
        # per pair secret.
        row = self.rows["email"]
        digest_a = contact_hash(self.vector_secret, row["preimage"], domain="email")
        digest_b = contact_hash(SECOND_PAIR_SECRET, row["preimage"], domain="email")
        self.assertEqual(digest_a, row["digest"])
        self.assertNotEqual(digest_a, digest_b)
        self.assertEqual(
            digest_b,
            _recompute(SECOND_PAIR_SECRET, normalize_contact(row["preimage"], "email")),
        )


if __name__ == "__main__":
    unittest.main()

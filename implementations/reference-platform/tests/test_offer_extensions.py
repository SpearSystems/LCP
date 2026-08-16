from __future__ import annotations

import unittest

from lcp_platform.matching import match_offer


class OfferExtensionMatchingTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "channel": "form",
            "attributes": {
                "vertical": "home_services",
                "service_type": "roofing",
                "service_subtype": "repair",
                "project_value": 25000,
            },
            "location": {
                "country_code": "AU",
                "state_region": "NSW",
                "postal_code": "2000",
            },
        }

    def offer(self) -> dict:
        return {
            "active": True,
            "vertical": "home_services",
            "countries": ["AU"],
            "floor_price_cents": 1000,
            "currency": "AUD",
            "extensions": {
                "lcp.platform.requirements": {
                    "profile_id": "buyer-home-services-v1",
                    "version": "2026-08-17",
                    "predicates": [
                        {
                            "path": "attributes.service_type",
                            "operator": "in",
                            "values": ["roofing", "gutters"],
                        },
                        {
                            "path": "attributes.project_value",
                            "operator": "between",
                            "min": 10000,
                            "max": 50000,
                        },
                        {
                            "path": "channel",
                            "operator": "equals",
                            "value": "form",
                        },
                    ],
                },
                "lcp.platform.service_area": {
                    "profile_id": "au-nsw-metro",
                    "version": "2026-08-17",
                    "countries": ["AU"],
                    "state_regions": ["NSW"],
                    "postal_codes": ["2000", "2001"],
                },
            },
        }

    def test_versioned_profiles_match_and_explain_failures(self) -> None:
        result = match_offer(self.offer(), self.payload())
        self.assertTrue(result.matched, result.reasons)

        outside_area = self.payload()
        outside_area["location"]["postal_code"] = "3000"
        result = match_offer(self.offer(), outside_area)
        self.assertFalse(result.matched)
        self.assertIn("service_area_postal_code_not_supported", result.reasons)

    def test_requirement_profile_mismatch_is_explainable(self) -> None:
        offer = self.offer()
        offer["extensions"]["lcp.platform.requirements"]["predicates"][0]["values"] = ["plumbing"]

        result = match_offer(offer, self.payload())

        self.assertFalse(result.matched)
        self.assertIn("requirements_predicate_mismatch", result.reasons)

    def test_invalid_requirement_operator_fails_closed(self) -> None:
        offer = self.offer()
        offer["extensions"]["lcp.platform.requirements"]["predicates"][0]["operator"] = "regex"

        result = match_offer(offer, self.payload())

        self.assertFalse(result.matched)
        self.assertIn("requirements_profile_invalid", result.reasons)


if __name__ == "__main__":
    unittest.main()

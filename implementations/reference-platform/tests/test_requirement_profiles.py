"""W4-01..W4-04: requirement profiles, predicate audit, datasets, candidates."""

from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import unittest

from lcp_platform.config import PlatformConfig
from lcp_platform.matching import (
    ProfileValidationError,
    ServiceAreaCatalog,
    ServiceAreaDatasetError,
    evaluate_requirement_profile,
    match_offer,
    validate_requirement_profile,
)
from lcp_platform.router import Platform, RequestError

ROOT = Path(__file__).resolve().parents[3]


def payload(**overrides) -> dict:
    value = {
        "channel": "form",
        "attributes": {
            "vertical": "home_services",
            "service_type": "roofing",
            "project_value": 25000,
        },
        "location": {
            "country_code": "AU",
            "state_region": "NSW",
            "postal_code": "2000",
        },
    }
    value.update(overrides)
    return value


def profile(version: str = "1", predicates: list | None = None, **extra) -> dict:
    value = {
        "profile_id": "home-services",
        "version": version,
        "predicates": predicates
        if predicates is not None
        else [
            {
                "path": "attributes.service_type",
                "operator": "in",
                "values": ["roofing", "gutters"],
            },
            {
                "path": "attributes.project_value",
                "operator": "range",
                "min": 10000,
                "max": 50000,
            },
        ],
    }
    value.update(extra)
    return value


def offer(**overrides) -> dict:
    value = {
        "offer_id": "offer-1",
        "buyer_id": "buyer-1",
        "tenant_id": "default",
        "active": True,
        "vertical": "home_services",
        "countries": ["AU"],
        "floor_price_cents": 1000,
        "currency": "AUD",
        "extensions": {"lcp.platform.requirements": {"profile_id": "home-services"}},
    }
    value.update(overrides)
    return value


def dataset(name: str, version: str = "2026-01-01", **overrides) -> dict:
    value = {
        "name": name,
        "version": version,
        "effective_from": "2026-01-01T00:00:00Z",
        "effective_to": None,
        "source": "unit-test fixture",
        "countries": ["AU"],
        "regions": ["NSW"],
        "postal_codes": ["2000", "2001"],
    }
    value.update(overrides)
    return value


class ProfilePlatformTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.areas = self.directory / "areas"
        self.areas.mkdir()
        self.platform = Platform(
            PlatformConfig(
                database_path=Path(":memory:"),
                schema_root=ROOT / "schemas",
                require_auth=False,
                test_mode=True,
                allow_insecure_webhooks=True,
                service_area_directory=self.areas,
            )
        )

    def tearDown(self) -> None:
        self.platform.close()
        self._tmp.cleanup()

    def write_area(self, name: str, **overrides) -> Path:
        path = self.areas / f"{name}.json"
        path.write_text(json.dumps(dataset(name, **overrides)), encoding="utf-8")
        return path

    # ─── W4-01: versioned profile store ────────────────────────────────────

    def test_profile_versions_activation_rollback_and_deactivation(self) -> None:
        v1 = self.platform.create_requirement_profile(profile("1"))
        v2 = self.platform.create_requirement_profile(
            profile(
                "2",
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "eq",
                        "value": "plumbing",
                    }
                ],
            )
        )
        self.assertEqual((v1["status"], v2["status"]), ("inactive", "inactive"))

        self.platform.activate_requirement_profile("home-services", "1")
        active = [
            p
            for p in self.platform.list_requirement_profiles("home-services")
            if p["effective"]
        ]
        self.assertEqual(
            [(p["version"], p["status"]) for p in active], [("1", "active")]
        )
        self.assertIsNone(active[0]["effective_to"])

        self.platform.activate_requirement_profile("home-services", "2")
        versions = {
            p["version"]: p
            for p in self.platform.list_requirement_profiles("home-services")
        }
        self.assertEqual(versions["2"]["status"], "active")
        self.assertEqual(versions["1"]["status"], "superseded")
        self.assertIsNotNone(versions["1"]["effective_to"])
        self.assertFalse(versions["1"]["effective"])

        self.platform.rollback_requirement_profile("home-services", "1")
        versions = {
            p["version"]: p
            for p in self.platform.list_requirement_profiles("home-services")
        }
        self.assertEqual(versions["1"]["status"], "active")
        self.assertIsNone(versions["1"]["effective_to"])
        self.assertEqual(versions["2"]["status"], "superseded")

        self.platform.deactivate_requirement_profile("home-services")
        self.assertFalse(
            any(
                p["effective"]
                for p in self.platform.list_requirement_profiles("home-services")
            )
        )
        record, reason = self.platform.store.resolve_requirement_profile(
            "home-services"
        )
        self.assertIsNone(record)
        self.assertEqual(reason, "requirement_profile_unavailable")

    def test_profile_version_is_immutable_and_unknown_version_is_rejected(self) -> None:
        self.platform.create_requirement_profile(profile("1"))
        with self.assertRaises(RequestError) as caught:
            self.platform.create_requirement_profile(profile("1"))
        self.assertIn("home-services@1", str(caught.exception))
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(RequestError):
            self.platform.activate_requirement_profile("home-services", "9")

    # ─── W4-02: predicate language ─────────────────────────────────────────

    def test_predicate_validation_names_unknown_path_operator_and_limits(self) -> None:
        cases = [
            (
                [{"path": "attributes.nope", "operator": "eq", "value": "x"}],
                "attributes.nope",
            ),
            ([{"path": "channel", "operator": "regex", "value": "x"}], "regex"),
            ([{"path": "attributes.a.b.c", "operator": "eq", "value": "x"}], "depth"),
            ([{"path": "channel", "operator": "gte", "value": 1}], "numeric"),
            (
                [{"path": "attributes.service_type", "operator": "eq", "value": 7}],
                "must be string",
            ),
        ]
        for predicates, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(ProfileValidationError) as caught:
                    validate_requirement_profile(profile("1", predicates))
                self.assertIn(expected, str(caught.exception))

        too_many = [
            {"path": "attributes.service_type", "operator": "in", "values": ["roofing"]}
            for _ in range(51)
        ]
        with self.assertRaises(ProfileValidationError) as caught:
            validate_requirement_profile(profile("1", too_many))
        self.assertIn("50", str(caught.exception))

        too_wide = [
            {
                "path": "attributes.service_type",
                "operator": "in",
                "values": [f"v{i}" for i in range(101)],
            }
        ]
        with self.assertRaises(ProfileValidationError) as caught:
            validate_requirement_profile(profile("1", too_wide))
        self.assertIn("100", str(caught.exception))

    def test_predicate_evaluation_reasons(self) -> None:
        cases = [
            (
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "eq",
                        "value": "roofing",
                    }
                ],
                payload(),
                True,
                "matched",
            ),
            (
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "eq",
                        "value": "plumbing",
                    }
                ],
                payload(),
                False,
                "value_mismatch",
            ),
            (
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "in",
                        "values": ["plumbing"],
                    }
                ],
                payload(),
                False,
                "set_miss",
            ),
            (
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "not_in",
                        "values": ["roofing"],
                    }
                ],
                payload(),
                False,
                "value_present",
            ),
            (
                [
                    {
                        "path": "attributes.project_value",
                        "operator": "gte",
                        "value": 30000,
                    }
                ],
                payload(),
                False,
                "outside_range",
            ),
            (
                [
                    {
                        "path": "attributes.project_value",
                        "operator": "lte",
                        "value": 30000,
                    }
                ],
                payload(),
                True,
                "matched",
            ),
            (
                [{"path": "attributes.nope", "operator": "eq", "value": 1}],
                payload(),
                False,
                "invalid_profile",
            ),
            (
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "exists",
                        "value": True,
                    }
                ],
                payload(),
                True,
                "matched",
            ),
            (
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "exists",
                        "value": False,
                    }
                ],
                payload(),
                False,
                "field_present",
            ),
        ]
        for predicates, lead, expected, reason in cases:
            if reason == "invalid_profile":
                matched, audits = evaluate_requirement_profile(
                    profile("1", predicates), lead
                )
                self.assertFalse(matched)
                self.assertEqual(audits[0].reason, "invalid_profile")
                continue
            matched, audits = evaluate_requirement_profile(
                profile("1", predicates), lead
            )
            self.assertEqual(matched, expected)
            self.assertEqual(audits[0].reason, reason)

        missing = evaluate_requirement_profile(
            profile(
                "1",
                [{"path": "attributes.project_value", "operator": "gte", "value": 1}],
            ),
            {"attributes": {}},
        )
        self.assertEqual(missing[1][0].reason, "missing_field")
        wrong_type = evaluate_requirement_profile(
            profile(
                "1",
                [{"path": "attributes.project_value", "operator": "gte", "value": 1}],
            ),
            {"attributes": {"project_value": "25000"}},
        )
        self.assertEqual(wrong_type[1][0].reason, "wrong_type")

    def test_match_offer_uses_pinned_profile_and_audits_each_predicate(self) -> None:
        self.platform.create_requirement_profile(profile("1"))
        self.platform.activate_requirement_profile("home-services", "1")

        result = match_offer(offer(), payload(), profiles=self.platform.store)
        self.assertTrue(result.matched, result.reasons)
        self.assertEqual(result.profile["profile_id"], "home-services")
        self.assertEqual(result.profile["version"], "1")
        self.assertEqual(len(result.predicates), 2)
        self.assertTrue(all(item["matched"] for item in result.predicates))
        self.assertEqual(result.predicates[0]["reason"], "matched")

        failed = match_offer(
            offer(),
            payload(
                attributes={
                    "vertical": "home_services",
                    "service_type": "plumbing",
                    "project_value": 25000,
                }
            ),
            profiles=self.platform.store,
        )
        self.assertFalse(failed.matched)
        self.assertIn("requirements_predicate_mismatch", failed.reasons)
        self.assertEqual(failed.predicates[0]["reason"], "set_miss")

        self.platform.create_requirement_profile(
            profile(
                "2",
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "eq",
                        "value": "plumbing",
                    }
                ],
            )
        )
        self.platform.activate_requirement_profile("home-services", "2")
        pinned = offer()
        pinned["extensions"]["lcp.platform.requirements"]["version"] = "1"
        self.assertTrue(
            match_offer(pinned, payload(), profiles=self.platform.store).matched
        )
        self.assertFalse(
            match_offer(offer(), payload(), profiles=self.platform.store).matched
        )

        unknown = offer()
        unknown["extensions"]["lcp.platform.requirements"] = {"profile_id": "missing"}
        result = match_offer(unknown, payload(), profiles=self.platform.store)
        self.assertFalse(result.matched)
        self.assertIn("requirement_profile_unknown", result.reasons)
        unresolved = match_offer(offer(), payload())
        self.assertIn("requirement_profile_unresolved", unresolved.reasons)

    def test_match_audit_row_schema_and_retention(self) -> None:
        self.platform.create_requirement_profile(profile("1"))
        self.platform.activate_requirement_profile("home-services", "1")
        candidate = offer()
        result = match_offer(candidate, payload(), profiles=self.platform.store)
        self.platform._record_match_evaluation(
            lead_id="lead-audit-1", offer=candidate, result=result
        )

        records = self.platform.store.list_match_audits("lead-audit-1")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(
            set(record),
            {
                "lead_id",
                "offer_id",
                "buyer_id",
                "matched",
                "reasons",
                "profile",
                "predicates",
                "service_area",
                "created_at",
            },
        )
        self.assertEqual(record["profile"]["profile_id"], "home-services")
        self.assertEqual(record["profile"]["version"], "1")
        self.assertEqual(record["predicates"][0]["path"], "attributes.service_type")
        self.assertIsNone(record["service_area"])

        self.assertEqual(
            self.platform.store.purge_match_audits("2999-01-01T00:00:00Z"), 1
        )
        self.assertEqual(self.platform.store.list_match_audits("lead-audit-1"), [])

        bad = profile(
            "1", [{"path": "attributes.nope", "operator": "eq", "value": "x"}]
        )
        with self.assertRaises(RequestError) as caught:
            self.platform.create_requirement_profile(bad)
        self.assertEqual(caught.exception.code, "LCP-100")
        self.assertIn("attributes.nope", str(caught.exception))

    def test_service_area_dataset_is_versioned_cached_and_fail_closed(self) -> None:
        self.write_area("au-nsw-metro")
        self.platform.service_areas.clear()
        catalog = self.platform.service_areas
        candidate = offer(
            extensions={
                "lcp.platform.service_area": {"service_area_ref": "au-nsw-metro"}
            }
        )
        result = match_offer(candidate, payload(), service_areas=catalog)
        self.assertTrue(result.matched, result.reasons)
        self.assertEqual(result.service_area["checks"][0]["version"], "2026-01-01")

        outside = match_offer(
            candidate,
            payload(
                location={
                    "country_code": "AU",
                    "state_region": "NSW",
                    "postal_code": "3000",
                }
            ),
            service_areas=catalog,
        )
        self.assertIn("service_area_postal_code_not_supported", outside.reasons)
        self.assertFalse(outside.service_area["matched"])

        unknown = offer(
            extensions={"lcp.platform.service_area": {"service_area_ref": "missing"}}
        )
        failed = match_offer(unknown, payload(), service_areas=catalog)
        self.assertIn("service_area_unknown", failed.reasons)

        self.write_area(
            "au-expired",
            effective_from="2020-01-01T00:00:00Z",
            effective_to="2020-06-01T00:00:00Z",
        )
        expired = match_offer(
            offer(
                extensions={
                    "lcp.platform.service_area": {"service_area_ref": "au-expired"}
                }
            ),
            payload(),
            service_areas=catalog,
        )
        self.assertIn("service_area_expired", expired.reasons)

        self.write_area("au-nsw-metro", version="2027-01-01", postal_codes=["2000"])
        reloaded, reason = catalog.resolve("au-nsw-metro")
        self.assertIsNone(reason)
        self.assertEqual(reloaded.version, "2027-01-01")

        broken = self.areas / "broken.json"
        broken.write_text(
            json.dumps(dataset("broken", countries=["au"])), encoding="utf-8"
        )
        catalog.clear()
        with self.assertRaises(ServiceAreaDatasetError) as caught:
            catalog.names()
        self.assertIn("broken.json", str(caught.exception))

        committed = ServiceAreaCatalog(ROOT / "data" / "service-areas")
        self.assertIn("au-nsw-metro", committed.names())
        version, reason = committed.resolve("au-nsw-metro")
        self.assertIsNone(reason)
        self.assertIsNotNone(version)

    def test_profile_service_area_reference_combines_with_dataset(self) -> None:
        self.write_area("au-nsw-metro")
        self.platform.create_requirement_profile(
            profile(
                "1",
                [
                    {
                        "path": "attributes.service_type",
                        "operator": "in",
                        "values": ["roofing"],
                    }
                ],
                service_area_ref="au-nsw-metro",
            )
        )
        self.platform.activate_requirement_profile("home-services", "1")
        matched = match_offer(
            offer(),
            payload(),
            profiles=self.platform.store,
            service_areas=self.platform.service_areas,
        )
        self.assertTrue(matched.matched, matched.reasons)
        outside = match_offer(
            offer(),
            payload(
                location={
                    "country_code": "AU",
                    "state_region": "NSW",
                    "postal_code": "9999",
                }
            ),
            profiles=self.platform.store,
            service_areas=self.platform.service_areas,
        )
        self.assertIn("service_area_postal_code_not_supported", outside.reasons)

    def test_candidate_index_is_superset_of_full_scan_for_random_corpus(self) -> None:
        rng = random.Random(20261104)
        service_types = ["roofing", "gutters", "plumbing", "solar"]
        regions = ["NSW", "VIC", "QLD"]
        postals = ["2000", "2001", "3000", "4000"]
        for index in range(40):
            value = {
                "offer_id": f"offer-{index}",
                "buyer_id": "buyer-1",
                "tenant_id": "default",
                "active": True,
                "vertical": "home_services",
                "countries": ["AU"],
                "floor_price_cents": 100,
                "currency": "AUD",
            }
            if rng.random() < 0.5:
                value["attribute_in"] = {
                    "service_type": rng.sample(service_types, rng.randint(1, 3))
                }
            if rng.random() < 0.6:
                value["state_regions"] = rng.sample(regions, rng.randint(1, 3))
            if rng.random() < 0.4:
                value["postal_codes"] = rng.sample(postals, rng.randint(1, 3))
            roll = rng.random()
            if roll < 0.2:
                value["extensions"] = None
            elif roll < 0.5:
                value["extensions"] = {
                    "lcp.platform.requirements": {
                        "profile_id": f"inline-{index}",
                        "version": "1",
                        "predicates": [
                            {
                                "path": "attributes.service_type",
                                "operator": "in",
                                "values": rng.sample(service_types, 2),
                            }
                        ],
                    }
                }
            self.platform.store.upsert_offer(value)

        matched_seen = 0
        for _ in range(60):
            lead = payload(
                attributes={
                    "vertical": "home_services",
                    "service_type": rng.choice(service_types),
                },
                location={
                    "country_code": "AU",
                    "state_region": rng.choice(regions),
                    "postal_code": rng.choice(postals),
                },
            )
            full = [
                candidate
                for candidate in self.platform.store.list_offers(active_only=True)
                if match_offer(candidate, lead).matched
            ]
            indexed = {
                candidate["offer_id"]
                for candidate in self.platform.store.list_offer_candidates(
                    lead, tenant_id="default"
                )
            }
            self.assertTrue({candidate["offer_id"] for candidate in full} <= indexed)
            matched_seen += len(full)
        self.assertGreater(matched_seen, 0)

    def test_offer_delete_and_update_keep_candidate_index_consistent(self) -> None:
        value = offer(offer_id="delete-me", postal_codes=["2000"])
        self.platform.upsert_offer(value)
        lead = payload()
        indexed = {
            candidate["offer_id"]
            for candidate in self.platform.store.list_offer_candidates(
                lead, tenant_id="default"
            )
        }
        self.assertIn("delete-me", indexed)

        self.platform.delete_offer("delete-me")
        self.assertIsNone(self.platform.store.get_offer("delete-me"))
        indexed = {
            candidate["offer_id"]
            for candidate in self.platform.store.list_offer_candidates(
                lead, tenant_id="default"
            )
        }
        self.assertNotIn("delete-me", indexed)
        with self.assertRaises(RequestError):
            self.platform.delete_offer("delete-me")
        revisions = self.platform.store.list_audit_events("offer", "delete-me")
        self.assertIn("offer.delete", [revision["action"] for revision in revisions])


if __name__ == "__main__":
    unittest.main()

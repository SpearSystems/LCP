#!/usr/bin/env python3
"""Stress-test the reference platform with synthetic, messy lead traffic.

This benchmark never sends network webhooks and never uses real consumer data.
Use match mode for CPU-only scaling and ingest/route modes for persistence and
routing pressure. Results are directional benchmarks, not production capacity
claims.
"""

from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PLATFORM = ROOT / "implementations" / "reference-platform"
sys.path.insert(0, str(REFERENCE_PLATFORM))

from lcp_platform.config import PlatformConfig  # noqa: E402
from lcp_platform.matching import match_offer  # noqa: E402
from lcp_platform.router import Platform  # noqa: E402


CATEGORIES = ("roofing", "gutters", "pest_control", "plumbing", "hvac", "solar")
SUBTYPES = ("repair", "replacement", "maintenance", "inspection")
COUNTRIES = ("AU", "US", "CA", "GB")
REGIONS = {
    "AU": ("NSW", "VIC", "QLD"),
    "US": ("CA", "TX", "FL"),
    "CA": ("ON", "BC", "AB"),
    "GB": ("ENG", "SCT", "WLS"),
}
POSTAL_CODES = {
    "AU": ("2000", "3000", "4000"),
    "US": ("90210", "73301", "33101"),
    "CA": ("M5V2T6", "V6B1A1", "T2P1J9"),
    "GB": ("SW1A1AA", "EH12NG", "CF101EP"),
}


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * percentile))
    return ordered[index]


def _template() -> dict[str, Any]:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as file:
        envelope = json.load(file)
    envelope["lcp"]["message"]["sender_id"] = "stress-publisher"
    envelope["lcp"]["message"]["receiver_id"] = "stress-platform"
    envelope["lcp"]["message"]["test"] = True
    envelope["lcp"]["payload"]["attributes"] = {
        "vertical": "home_services",
        "schema_version": "1.0.0",
        "service_type": "roofing",
        "service_subtype": "repair",
        "project_type": "repair",
        "urgency": "within_week",
        "property_type": "single_family",
        "property_age_band": "15_30",
        "budget_band": "1000_5000",
        "preferred_schedule": "asap",
        "has_existing_damage": True,
        "damage_type": "storm",
        "square_footage_band": "1500_2000",
        "number_of_rooms_band": "3_5",
        "material": "asphalt_shingle",
        "roof_type": "asphalt_shingle",
        "stories_band": "two",
        "has_insurance_claim": False,
        "occupancy": "owner_occupied",
    }
    return envelope


def _record(template: dict[str, Any], index: int) -> dict[str, Any]:
    envelope = copy.deepcopy(template)
    message = envelope["lcp"]["message"]
    payload = envelope["lcp"]["payload"]
    country = COUNTRIES[index % len(COUNTRIES)]
    region = REGIONS[country][(index // len(COUNTRIES)) % len(REGIONS[country])]
    postal = POSTAL_CODES[country][(index // 2) % len(POSTAL_CODES[country])]
    message["id"] = str(uuid4())
    message["idempotency_key"] = f"stress-{index}"
    message["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["lead_id"] = f"stress-lead-{index}"
    payload["external_id"] = f"dirty-source-{index % 17}-{index}"
    payload["attributes"]["service_type"] = CATEGORIES[index % len(CATEGORIES)]
    payload["attributes"]["service_subtype"] = SUBTYPES[index % len(SUBTYPES)]
    payload["attributes"]["budget_band"] = (
        "1000_5000" if index % 3 else "10000_25000"
    )
    payload["location"]["country_code"] = country
    payload["location"]["state_region"] = region
    payload["location"]["postal_code"] = postal
    payload["provenance"]["source_type"] = "publisher"
    payload["provenance"]["acquisition_method"] = (
        "paid" if index % 5 else "marketplace"
    )
    payload["provenance"]["campaign_id"] = f"campaign-{index % 31}"
    return envelope


def _offer(index: int, *, with_credentials: bool) -> tuple[dict[str, Any], str]:
    buyer_id = f"stress-buyer-{index}"
    category = CATEGORIES[index % len(CATEGORIES)]
    country = COUNTRIES[index % len(COUNTRIES)]
    region = REGIONS[country][index % len(REGIONS[country])]
    offer = {
        "offer_id": f"stress-offer-{index}",
        "buyer_id": buyer_id,
        "active": True,
        "routing_mode": "auction",
        "vertical": "home_services",
        "countries": [country],
        "state_regions": [region],
        "floor_price_cents": 1000 + index,
        "currency": "USD" if country == "US" else "AUD",
        "attribute_in": {"service_type": [category]},
        "extensions": {
            "lcp.platform.requirements": {
                "profile_id": f"stress-requirements-{index % 9}",
                "version": "stress-1",
                "predicates": [
                    {
                        "path": "attributes.service_subtype",
                        "operator": "in",
                        "values": list(SUBTYPES[: 1 + index % len(SUBTYPES)]),
                    },
                    {
                        "path": "provenance.acquisition_method",
                        "operator": "in",
                        "values": ["paid", "marketplace"],
                    },
                ],
            },
            "lcp.platform.service_area": {
                "profile_id": f"stress-area-{index % 13}",
                "version": "stress-1",
                "countries": [country],
                "state_regions": [region],
                "postal_codes": list(POSTAL_CODES[country]),
            },
        },
    }
    return offer, buyer_id


def _build_platform(database_path: Path, records: int, offers: int, route: bool) -> Platform:
    key = base64.urlsafe_b64encode(b"stress-only-key-0123456789012345").decode()
    platform = Platform(
        PlatformConfig(
            database_path=database_path,
            schema_root=ROOT / "schemas",
            platform_id="stress-platform",
            require_auth=False,
            test_mode=True,
            allow_insecure_webhooks=True,
            pii_encryption_key=key,
            rate_limit_per_minute=max(records * 2, 1_000_000),
        )
    )
    for index in range(offers):
        offer, buyer_id = _offer(index, with_credentials=route)
        platform.upsert_offer(offer)
        if route:
            platform.upsert_credential(buyer_id, hmac_secret=f"stress-secret-{index}")
    return platform


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--offers", type=int, default=24)
    parser.add_argument("--mode", choices=("match", "ingest", "route"), default="ingest")
    parser.add_argument("--batch-size", type=int, default=1_000)
    args = parser.parse_args()
    if args.records <= 0 or args.offers <= 0 or args.batch_size <= 0:
        parser.error("records, offers, and batch-size must be positive")
    return args


def main() -> int:
    args = _parse_args()
    template = _template()
    batch_times: list[float] = []
    errors = 0
    first_errors: list[str] = []
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="lcp-stress-") as directory:
        database_path = Path(directory) / "stress.sqlite3"
        platform = None
        try:
            if args.mode != "match":
                platform = _build_platform(
                    database_path,
                    args.records,
                    args.offers,
                    route=args.mode == "route",
                )
            offers = [_offer(index, with_credentials=False)[0] for index in range(args.offers)]
            batch_started = time.perf_counter()
            for index in range(args.records):
                envelope = _record(template, index)
                try:
                    if args.mode == "match":
                        payload = envelope["lcp"]["payload"]
                        for offer in offers:
                            match_offer(offer, payload, sender_id="stress-publisher")
                    else:
                        platform.ingest(envelope, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
                except Exception as exc:  # benchmark should report bad records, not stop
                    errors += 1
                    if len(first_errors) < 5:
                        first_errors.append(f"record {index}: {type(exc).__name__}: {exc}")
                if (index + 1) % args.batch_size == 0 or index + 1 == args.records:
                    batch_times.append(time.perf_counter() - batch_started)
                    batch_started = time.perf_counter()
                    if (index + 1) % max(args.batch_size * 10, 1) == 0:
                        print(f"processed={index + 1}/{args.records}", flush=True)
            elapsed = time.perf_counter() - started
            db_bytes = database_path.stat().st_size if database_path.exists() else 0
            print(json.dumps({
                "records": args.records,
                "offers": args.offers,
                "mode": args.mode,
                "elapsed_seconds": round(elapsed, 3),
                "records_per_second": round(args.records / elapsed, 2),
                "batch_p50_seconds": round(_percentile(batch_times, 0.50), 4),
                "batch_p95_seconds": round(_percentile(batch_times, 0.95), 4),
                "batch_count": len(batch_times),
                "errors": errors,
                "first_errors": first_errors,
                "peak_rss_mb": round(_rss_bytes() / 1024 / 1024, 2),
                "sqlite_bytes": db_bytes,
            }, indent=2))
        finally:
            if platform is not None:
                platform.close()
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

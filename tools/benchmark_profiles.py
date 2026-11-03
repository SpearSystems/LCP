#!/usr/bin/env python3
"""Benchmark requirement-profile matching (W4-02/W4-04) with N profiles/offers.

The benchmark builds an in-process profile resolver, generates offers that
reference pinned and unpinned profile versions, and measures match throughput
over a mixed lead corpus. It prints a table by default and a machine-readable
object with ``--json``. Nothing here asserts hardware-dependent thresholds.

Only ``lcp_platform.matching`` is imported, so the tool runs on a bare Python
interpreter without the platform's crypto and HTTP dependencies.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PLATFORM = ROOT / "implementations" / "reference-platform"
sys.path.insert(0, str(REFERENCE_PLATFORM))

from lcp_platform.matching import match_offer  # noqa: E402

SERVICE_TYPES = ("roofing", "gutters", "plumbing", "solar", "pest_control", "hvac")
COUNTRIES = ("AU", "US", "CA", "GB")
REGIONS = {
    "AU": ("NSW", "VIC", "QLD"),
    "US": ("CA", "TX", "FL"),
    "CA": ("ON", "BC", "AB"),
    "GB": ("ENG", "SCT", "WLS"),
}
POSTALS = {"AU": ("2000", "3000", "4000"), "US": ("90210", "73301", "33101")}


def _predicates(rng: random.Random, index: int) -> list[dict[str, Any]]:
    predicates: list[dict[str, Any]] = [
        {
            "path": "attributes.service_type",
            "operator": "in",
            "values": rng.sample(SERVICE_TYPES, rng.randint(1, 3)),
        },
        {
            "path": "attributes.project_value",
            "operator": "range",
            "min": 1000 * (index % 5),
            "max": 50_000 + 1000 * index,
        },
    ]
    if index % 2:
        predicates.append({"path": "channel", "operator": "eq", "value": "form"})
    if index % 3 == 0:
        predicates.append(
            {
                "path": "provenance.acquisition_method",
                "operator": "not_in",
                "values": ["scraped"],
            }
        )
    return predicates


def _profile(index: int, version: str, rng: random.Random) -> dict[str, Any]:
    return {
        "profile_id": f"bench-profile-{index}",
        "version": version,
        "predicates": _predicates(rng, index),
    }


class _Resolver:
    """The ``resolve_requirement_profile`` contract over in-memory records."""

    def __init__(self, records: dict[tuple[str, str], dict[str, Any]]):
        self._records = records

    def resolve_requirement_profile(
        self,
        profile_id: str,
        version: str | None = None,
        *,
        tenant_id: str = "default",
        at: datetime | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if version is not None:
            record = self._records.get((profile_id, version))
            if record is None:
                return None, "requirement_profile_unknown"
            return record, None
        candidates = [
            record for key, record in self._records.items() if key[0] == profile_id
        ]
        if not candidates:
            return None, "requirement_profile_unknown"
        return max(candidates, key=lambda record: record["version"]), None


def _offer(index: int, profiles: int) -> dict[str, Any]:
    target = index % profiles
    pinned = index % 2 == 0
    reference: dict[str, Any] = {"profile_id": f"bench-profile-{target}"}
    if pinned:
        reference["version"] = "1"
    country = COUNTRIES[index % len(COUNTRIES)]
    return {
        "offer_id": f"bench-offer-{index}",
        "buyer_id": f"bench-buyer-{index % 17}",
        "tenant_id": "default",
        "active": True,
        "vertical": "home_services",
        "countries": [country],
        "floor_price_cents": 1000 + index,
        "currency": "USD" if country == "US" else "AUD",
        "extensions": {"lcp.platform.requirements": reference},
    }


def _lead(index: int, rng: random.Random) -> dict[str, Any]:
    country = COUNTRIES[index % len(COUNTRIES)]
    return {
        "channel": "form" if index % 2 else "call",
        "attributes": {
            "vertical": "home_services",
            "service_type": rng.choice(SERVICE_TYPES),
            "project_value": rng.randint(500, 60_000),
        },
        "location": {
            "country_code": country,
            "state_region": rng.choice(REGIONS[country]),
            "postal_code": POSTALS.get(country, ("00001", "00002"))[index % 2],
        },
        "provenance": {
            "acquisition_method": "paid" if index % 3 else "scraped",
        },
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def _run_pass(
    name: str,
    offers: list[dict[str, Any]],
    leads: list[dict[str, Any]],
    resolver: _Resolver,
    prefilter: bool,
) -> dict[str, Any]:
    latencies: list[float] = []
    evaluations = 0
    matches = 0
    started = time.perf_counter()
    for lead in leads:
        country = lead["location"]["country_code"]
        pool = (
            [offer for offer in offers if country in offer["countries"]]
            if prefilter
            else offers
        )
        lead_started = time.perf_counter()
        for offer in pool:
            result = match_offer(offer, lead, profiles=resolver)
            evaluations += 1
            if result.matched:
                matches += 1
        latencies.append(time.perf_counter() - lead_started)
    elapsed = time.perf_counter() - started
    return {
        "pass": name,
        "offers": len(offers),
        "leads": len(leads),
        "evaluations": evaluations,
        "matches": matches,
        "elapsed_seconds": round(elapsed, 4),
        "leads_per_second": round(len(leads) / elapsed, 2) if elapsed else 0.0,
        "evaluations_per_second": round(evaluations / elapsed, 2) if elapsed else 0.0,
        "latency_p50_ms": round(_percentile(latencies, 0.50) * 1000, 3),
        "latency_p95_ms": round(_percentile(latencies, 0.95) * 1000, 3),
    }


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = (
        "pass",
        "offers",
        "leads",
        "evaluations",
        "matches",
        "elapsed_s",
        "leads/s",
        "evals/s",
        "p50_ms",
        "p95_ms",
    )
    keys = (
        "pass",
        "offers",
        "leads",
        "evaluations",
        "matches",
        "elapsed_seconds",
        "leads_per_second",
        "evaluations_per_second",
        "latency_p50_ms",
        "latency_p95_ms",
    )
    widths = [
        max([len(header)] + [len(str(row[key])) for row in rows])
        for header, key in zip(headers, keys)
    ]
    line = "  ".join(
        header.ljust(widths[position]) for position, header in enumerate(headers)
    )
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(
            "  ".join(
                str(row[key]).ljust(widths[position])
                for position, key in enumerate(keys)
            )
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=int, default=50)
    parser.add_argument("--offers", type=int, default=200)
    parser.add_argument("--leads", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON object instead of the human-readable table",
    )
    args = parser.parse_args()
    for name in ("profiles", "offers", "leads"):
        if getattr(args, name) <= 0:
            parser.error(f"--{name} must be positive")
    return args


def main() -> int:
    args = _parse_args()
    rng = random.Random(args.seed)
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for index in range(args.profiles):
        for version in ("1", "2"):
            record = _profile(index, version, rng)
            records[(record["profile_id"], version)] = record
    resolver = _Resolver(records)
    offers = [_offer(index, args.profiles) for index in range(args.offers)]
    leads = [_lead(index, rng) for index in range(args.leads)]

    rows = [
        _run_pass("candidate-prefilter", offers, leads, resolver, prefilter=True),
        _run_pass("full-scan", offers, leads, resolver, prefilter=False),
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "profiles": args.profiles,
            "offers": args.offers,
            "leads": args.leads,
            "seed": args.seed,
            "versions_per_profile": 2,
        },
        "rows": rows,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"profiles={args.profiles} offers={args.offers} leads={args.leads} "
            f"seed={args.seed}"
        )
        _print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

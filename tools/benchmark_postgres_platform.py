#!/usr/bin/env python3
"""Concurrent Postgres load benchmark for the LCP reference platform.

Measures the production persistence profile that the SQLite benchmark cannot:
multiple concurrent publisher processes, each with its own Postgres-backed
Platform connection, plus an optional end-to-end delivery mode that pushes
signed webhooks to a local sink with simulated buyer latency.

This benchmark:

- never sends real network webhooks (the sink is a local loopback server);
- never uses real consumer data (synthetic home-services envelopes);
- requires a DISPOSABLE Postgres database. It creates tables if missing and
  uses unique run-prefixed IDs, but it never drops or resets existing data;
- requires the ``lcp-reference-platform[postgres]`` extra (psycopg).

Run against a throwaway database, e.g. the same profile CI provisions:

    export LCP_TEST_POSTGRES_URL='postgresql://lcp_test:lcp_test@localhost:5432/lcp_test'
    python3 tools/benchmark_postgres_platform.py --mode ingest --records 10000 --workers 4
    python3 tools/benchmark_postgres_platform.py --mode deliver --records 5000 --workers 4 \\
        --delivery-workers 2 --webhook-latency-ms 50

Results are directional benchmarks, not production capacity claims.
"""

from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timezone
import http.server
import json
import multiprocessing as mp
import os
from pathlib import Path
import resource
import sys
import threading
import time
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PLATFORM = ROOT / "implementations" / "reference-platform"
TOOLS = ROOT / "tools"
sys.path.insert(0, str(REFERENCE_PLATFORM))
sys.path.insert(0, str(TOOLS))

# Reuse the synthetic category/country generators from the SQLite benchmark so
# both tools exercise the same messy multi-category/multi-country traffic.
from benchmark_reference_platform import (  # noqa: E402
    CATEGORIES,
    COUNTRIES,
    POSTAL_CODES,
    REGIONS,
    SUBTYPES,
)

from lcp_platform.config import PlatformConfig  # noqa: E402
from lcp_platform.router import Platform  # noqa: E402

ENCRYPTION_KEY = base64.urlsafe_b64encode(b"stress-only-key-0123456789012345").decode()
PLATFORM_ID = "pgstress-platform"
RATE_LIMIT = 10**9


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def _template(platform_id: str, publisher_id: str) -> dict[str, Any]:
    with (ROOT / "examples" / "lead.json").open(encoding="utf-8") as handle:
        envelope = json.load(handle)
    message = envelope["lcp"]["message"]
    message["sender_id"] = publisher_id
    message["receiver_id"] = platform_id
    message["test"] = True
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


def _record(template: dict[str, Any], worker_id: int, index: int, run_id: str) -> dict[str, Any]:
    envelope = copy.deepcopy(template)
    message = envelope["lcp"]["message"]
    payload = envelope["lcp"]["payload"]
    country = COUNTRIES[index % len(COUNTRIES)]
    region = REGIONS[country][(index // len(COUNTRIES)) % len(REGIONS[country])]
    postal = POSTAL_CODES[country][(index // 2) % len(POSTAL_CODES[country])]
    message["id"] = str(uuid4())
    message["idempotency_key"] = f"{run_id}-{worker_id}-{index}"
    message["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["lead_id"] = f"{run_id}-lead-{worker_id}-{index}"
    payload["external_id"] = f"dirty-source-{index % 17}-{index}"
    payload["attributes"]["service_type"] = CATEGORIES[index % len(CATEGORIES)]
    payload["attributes"]["service_subtype"] = SUBTYPES[index % len(SUBTYPES)]
    payload["attributes"]["budget_band"] = "1000_5000" if index % 3 else "10000_25000"
    payload["location"]["country_code"] = country
    payload["location"]["state_region"] = region
    payload["location"]["postal_code"] = postal
    payload["provenance"]["acquisition_method"] = "paid" if index % 5 else "marketplace"
    payload["provenance"]["campaign_id"] = f"campaign-{index % 31}"
    return envelope


def _offers(
    count: int,
    *,
    mode: str,
    run_id: str,
    webhook_url: str | None = None,
) -> list[dict[str, Any]]:
    """One offer per (category, country) pair so each record matches exactly one offer.

    Requirement profiles and service-area extensions are exercised with
    permissive predicates so the matching count stays deterministic.
    """
    offers: list[dict[str, Any]] = []
    for index in range(count):
        category = CATEGORIES[index % len(CATEGORIES)]
        country = COUNTRIES[(index // len(CATEGORIES)) % len(COUNTRIES)]
        offer: dict[str, Any] = {
            "offer_id": f"{run_id}-offer-{index}",
            "buyer_id": f"{run_id}-buyer-{index}",
            "active": True,
            "routing_mode": "direct" if mode == "deliver" else "auction",
            "vertical": "home_services",
            "countries": [country],
            "state_regions": list(REGIONS[country]),
            "floor_price_cents": 1000 + index,
            "currency": "USD" if country == "US" else "AUD",
            "attribute_in": {"service_type": [category]},
            "extensions": {
                "lcp.platform.requirements": {
                    "profile_id": f"{run_id}-requirements-{index % 9}",
                    "version": "stress-1",
                    "predicates": [
                        {
                            "path": "provenance.acquisition_method",
                            "operator": "in",
                            "values": ["paid", "marketplace"],
                        },
                    ],
                },
                "lcp.platform.service_area": {
                    "profile_id": f"{run_id}-area-{index % 13}",
                    "version": "stress-1",
                    "countries": [country],
                    "state_regions": list(REGIONS[country]),
                    "postal_codes": list(POSTAL_CODES[country]),
                },
            },
        }
        if webhook_url:
            offer["webhook_url"] = webhook_url
        offers.append(offer)
    return offers


def _platform(database_url: str, schema_root: Path) -> Platform:
    return Platform(
        PlatformConfig(
            database_path=Path(":memory:"),
            database_url=database_url,
            schema_root=schema_root,
            platform_id=PLATFORM_ID,
            require_auth=False,
            test_mode=True,
            allow_insecure_webhooks=True,
            pii_encryption_key=ENCRYPTION_KEY,
            rate_limit_per_minute=RATE_LIMIT,
        )
    )


def _open_platform(database_url: str, schema_root: Path) -> Platform:
    """Open a process-local platform, retrying concurrent Postgres DDL locks."""
    last_error: Exception | None = None
    for attempt in range(7):
        try:
            return _platform(database_url, schema_root)
        except Exception as exc:
            last_error = exc
            detail = f"{type(exc).__name__}: {exc}".lower()
            if "deadlock" not in detail and "lock" not in detail:
                raise
            time.sleep(0.25 * (2**attempt))
    assert last_error is not None
    raise last_error


def _setup(database_url: str, schema_root: Path, offers: list[dict[str, Any]]) -> None:
    platform = _open_platform(database_url, schema_root)
    try:
        for offer in offers:
            platform.upsert_credential(
                offer["buyer_id"], hmac_secret=f"pgstress-secret-{offer['buyer_id']}"
            )
            platform.upsert_offer(offer)
    finally:
        platform.close()


def _publisher_main(
    database_url: str,
    schema_root: str,
    records: int,
    worker_id: int,
    workers: int,
    run_id: str,
    queue: Any,
) -> None:
    processed = 0
    errors = 0
    first_errors: list[str] = []
    try:
        platform = _open_platform(database_url, Path(schema_root))
    except Exception as exc:
        queue.put(
            {
                "worker_id": worker_id,
                "records": 0,
                "errors": 1,
                "first_errors": [f"init: {type(exc).__name__}: {exc}"],
            }
        )
        return
    publisher_id = f"{run_id}-pub-{worker_id}"
    template = _template(PLATFORM_ID, publisher_id)
    start = (worker_id * records) // workers
    end = ((worker_id + 1) * records) // workers
    try:
        for index in range(start, end):
            envelope = _record(template, worker_id, index, run_id)
            try:
                platform.ingest(envelope, headers={"X-LCP-Test": "true"}, raw_body=b"{}")
                processed += 1
            except Exception as exc:
                errors += 1
                if len(first_errors) < 5:
                    first_errors.append(f"index {index}: {type(exc).__name__}: {exc}")
    finally:
        platform.close()
    queue.put(
        {
            "worker_id": worker_id,
            "records": processed,
            "errors": errors,
            "first_errors": first_errors,
        }
    )


def _delivery_main(
    database_url: str,
    schema_root: str,
    stop_event: Any,
) -> None:
    try:
        platform = _open_platform(database_url, Path(schema_root))
    except Exception:
        return
    try:
        while not stop_event.is_set():
            platform.process_once()
            time.sleep(0.05)
    finally:
        platform.close()


class _SinkHandler(http.server.BaseHTTPRequestHandler):
    latency_ms = 0
    _lock = threading.Lock()
    received_posts = 0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        message_type = ""
        if body:
            try:
                message_type = json.loads(body).get("lcp", {}).get("message", {}).get("type", "")
            except Exception:
                message_type = ""
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        if message_type == "post":
            with self._lock:
                type(self).received_posts += 1

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _database_size_bytes(database_url: str, schema_root: Path) -> int | None:
    try:
        platform = _open_platform(database_url, schema_root)
    except Exception:
        return None
    try:
        row = platform.store._connection.execute(
            "SELECT pg_database_size(current_database())", ()
        ).fetchone()
        return int(next(iter(row.values())))
    except Exception:
        return None
    finally:
        platform.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Postgres DSN; defaults to LCP_TEST_POSTGRES_URL",
    )
    parser.add_argument(
        "--mode",
        choices=("ingest", "deliver"),
        default="ingest",
        help="ingest: concurrent intake and persistence; deliver: also push webhooks to the local sink",
    )
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--offers", type=int, default=24)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delivery-workers", type=int, default=2)
    parser.add_argument("--webhook-latency-ms", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--min-records-per-second", type=float, default=None)
    parser.add_argument("--min-deliveries-per-second", type=float, default=None)
    parser.add_argument("--min-end-to-end-records-per-second", type=float, default=None)
    parser.add_argument("--json-output", type=Path, default=None)
    args = parser.parse_args()
    if args.records <= 0 or args.offers <= 0:
        parser.error("records and offers must be positive")
    if args.workers <= 0 or args.delivery_workers <= 0:
        parser.error("workers and delivery-workers must be positive")
    if args.webhook_latency_ms < 0:
        parser.error("webhook-latency-ms must be non-negative")
    if args.mode == "deliver" and args.offers % len(CATEGORIES) != 0:
        parser.error("deliver mode requires offers to be a multiple of 6 so each lead matches exactly one offer")
    return args


def main() -> int:
    args = _parse_args()
    database_url = args.database_url or os.environ.get("LCP_TEST_POSTGRES_URL")
    if not database_url:
        print(
            "error: a Postgres database URL is required (--database-url or LCP_TEST_POSTGRES_URL)",
            file=sys.stderr,
        )
        return 2
    schema_root = ROOT / "schemas"
    run_id = f"pgbench-{uuid4().hex[:10]}"
    mp.set_start_method("spawn", force=True)

    sink_server: http.server.ThreadingHTTPServer | None = None
    sink_thread: threading.Thread | None = None
    webhook_url: str | None = None
    if args.mode == "deliver":
        sink_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SinkHandler)
        _SinkHandler.latency_ms = args.webhook_latency_ms
        sink_thread = threading.Thread(target=sink_server.serve_forever, daemon=True)
        sink_thread.start()
        webhook_url = f"http://127.0.0.1:{sink_server.server_address[1]}/webhook"

    offers = _offers(
        args.offers,
        mode=args.mode,
        run_id=run_id,
        webhook_url=webhook_url,
    )
    _setup(database_url, schema_root, offers)

    queue: Any = mp.Queue()
    stop_event = mp.Event()
    publisher_processes = [
        mp.Process(
            target=_publisher_main,
            args=(database_url, str(schema_root), args.records, index, args.workers, run_id, queue),
            name=f"publisher-{index}",
        )
        for index in range(args.workers)
    ]
    delivery_processes: list[mp.Process] = []
    if args.mode == "deliver":
        delivery_processes = [
            mp.Process(
                target=_delivery_main,
                args=(database_url, str(schema_root), stop_event),
                name=f"delivery-worker-{index}",
            )
            for index in range(args.delivery_workers)
        ]

    started = time.perf_counter()
    for process in publisher_processes:
        process.start()
    for process in delivery_processes:
        process.start()
    for process in publisher_processes:
        process.join(timeout=max(args.timeout_seconds, 60))
    for process in publisher_processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
    intake_elapsed = time.perf_counter() - started

    worker_results: list[dict[str, Any]] = []
    for _ in publisher_processes:
        try:
            worker_results.append(queue.get(timeout=5))
        except Exception:
            break
    errors = sum(result["errors"] for result in worker_results)
    first_errors = [
        error
        for result in worker_results
        for error in result["first_errors"]
    ][:5]

    delivered_posts = 0
    drain_elapsed: float | None = None
    if args.mode == "deliver":
        assert sink_server is not None and delivery_processes
        drain_started = time.perf_counter()
        deadline = drain_started + args.timeout_seconds
        while _SinkHandler.received_posts < args.records and time.perf_counter() < deadline:
            time.sleep(0.2)
        drain_elapsed = time.perf_counter() - drain_started
        delivered_posts = min(_SinkHandler.received_posts, args.records)
        stop_event.set()
        for process in delivery_processes:
            process.join(timeout=15)
        for process in delivery_processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    total_elapsed = time.perf_counter() - started
    db_bytes = _database_size_bytes(database_url, schema_root)
    if sink_server is not None:
        sink_server.shutdown()
        sink_server.server_close()
        assert sink_thread is not None
        sink_thread.join(timeout=5)

    report: dict[str, Any] = {
        "run_id": run_id,
        "mode": args.mode,
        "records": args.records,
        "offers": args.offers,
        "workers": args.workers,
        "candidate_index": "enabled",
        "intake_elapsed_seconds": round(intake_elapsed, 3),
        "records_per_second": round(args.records / intake_elapsed, 2),
        "errors": errors,
        "first_errors": first_errors,
        "peak_rss_mb": round(_rss_bytes() / 1024 / 1024, 2),
        "postgres_bytes": db_bytes,
    }
    if args.mode == "deliver":
        drain_value = drain_elapsed or 0.0
        report.update(
            {
                "delivery_workers": args.delivery_workers,
                "webhook_latency_ms": args.webhook_latency_ms,
                "delivered_posts": delivered_posts,
                "drain_elapsed_seconds": round(drain_value, 3),
                "deliveries_per_second": round(delivered_posts / drain_value, 2) if drain_value else 0,
                "total_elapsed_seconds": round(total_elapsed, 3),
                "end_to_end_records_per_second": round(args.records / total_elapsed, 2),
            }
        )
    threshold_failures: list[str] = []
    if args.min_records_per_second is not None and report["records_per_second"] < args.min_records_per_second:
        threshold_failures.append(
            f"intake throughput {report['records_per_second']:.2f} records/sec is below "
            f"{args.min_records_per_second:.2f}"
        )
    if args.mode == "deliver":
        if (
            args.min_deliveries_per_second is not None
            and report["deliveries_per_second"] < args.min_deliveries_per_second
        ):
            threshold_failures.append(
                f"delivery throughput {report['deliveries_per_second']:.2f} deliveries/sec is below "
                f"{args.min_deliveries_per_second:.2f}"
            )
        if (
            args.min_end_to_end_records_per_second is not None
            and report["end_to_end_records_per_second"] < args.min_end_to_end_records_per_second
        ):
            threshold_failures.append(
                f"end-to-end throughput {report['end_to_end_records_per_second']:.2f} records/sec is below "
                f"{args.min_end_to_end_records_per_second:.2f}"
            )
    report["threshold_failures"] = threshold_failures
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    ok = (
        errors == 0
        and (args.mode != "deliver" or delivered_posts == args.records)
        and not threshold_failures
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

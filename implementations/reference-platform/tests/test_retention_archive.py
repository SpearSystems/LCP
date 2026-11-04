"""W4-05..W4-07: deferred history, retention, archive/restore, capacity gates."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from lcp_platform.config import PlatformConfig
from lcp_platform.router import Platform
from lcp_platform.storage import Store, _RETENTION_SWEEP_INTERVAL_SECONDS

ROOT = Path(__file__).resolve().parents[3]
ARCHIVE_TOOL = ROOT / "tools" / "archive_store.py"


def stale_envelope(lead_id: str, **overrides: object) -> dict:
    """A minimal, valid-shaped envelope for store-level tests (no schema run)."""
    message = {
        "id": f"550e8400-e29b-41d4-a716-44665544{lead_id[-4:]}",
        "type": "lead",
        "sender_id": "pub_001",
        "receiver_id": "platform_001",
        "idempotency_key": f"key-{lead_id}",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    message.update(overrides.pop("message", {}))
    return {
        "lcp": {
            "version": "1.1",
            "message": message,
            "payload": {"lead_id": lead_id, **overrides},
        }
    }


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.database = self.directory / "lcp.sqlite3"

    def tearDown(self) -> None:
        self._tmp.cleanup()


class DeferredHistoryTest(_StoreCase):
    """W4-05: history/audit writes leave the intake path, never lose the lead."""

    def setUp(self) -> None:
        super().setUp()
        self.platform = Platform(
            PlatformConfig(
                database_path=self.database,
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                history_queue_capacity=8,
            )
        )

    def tearDown(self) -> None:
        self.platform.close()
        super().tearDown()

    def load_lead(self) -> dict:
        lead = json.loads((ROOT / "examples" / "lead.json").read_text(encoding="utf-8"))
        lead["lcp"]["message"]["test"] = True
        lead["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440444"
        lead["lcp"]["message"]["idempotency_key"] = "deferred-history-001"
        lead["lcp"]["payload"]["lead_id"] = "lead_deferred_001"
        lead["lcp"]["payload"].pop("expiry", None)
        return lead

    def stored_audit_actions(self, resource_id: str) -> list[str]:
        """Read the table directly: the public reader flushes the queue."""
        with sqlite3.connect(self.database) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT action FROM audit_events WHERE resource_id = ? "
                    "ORDER BY created_at",
                    (resource_id,),
                )
            ]

    def test_intake_queues_history_and_the_worker_drains_it(self) -> None:
        self.platform.upsert_offer(
            {
                "offer_id": "offer-deferred",
                "buyer_id": "buyer_001",
                "active": True,
                "vertical": "mortgage",
                "countries": ["AU"],
                "floor_price_cents": 1500,
                "currency": "AUD",
                "require_consent_evidence": True,
                "ping_timeout_seconds": 30,
            }
        )
        ack = self.platform.ingest(self.load_lead(), headers={"X-LCP-Test": "true"})
        self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")

        # Neither the audit trail nor the decision rows are written yet: they
        # sit in the process-local queue for the worker.
        before = self.platform.store.history_metrics()
        self.assertGreaterEqual(before["queued"], 1)
        self.assertEqual(self.stored_audit_actions("lead_deferred_001"), [])

        self.platform.process_once()

        after = self.platform.store.history_metrics()
        self.assertEqual(after["queued"], 0)
        self.assertGreaterEqual(after["flushed"], 2)
        self.assertEqual(after["dropped"], 0)
        actions = [
            row["action"]
            for row in self.platform.store.list_audit_events(
                "lead", "lead_deferred_001"
            )
        ]
        self.assertIn("lead.routed", actions)
        self.assertEqual(
            len(self.platform.store.list_match_decisions("lead_deferred_001")), 1
        )
        self.assertEqual(
            len(self.platform.store.list_match_audits("lead_deferred_001")), 1
        )

    def test_reads_flush_the_queue_so_callers_see_their_own_writes(self) -> None:
        self.platform.store.insert_audit(
            tenant_id="default",
            actor_id="operator",
            action="lead.archived",
            resource_type="lead",
            resource_id="lead_read_your_write",
        )
        self.platform.store.enqueue_history(
            lambda: self.platform.store.insert_audit(
                tenant_id="default",
                actor_id="operator",
                action="lead.restored",
                resource_type="lead",
                resource_id="lead_read_your_write",
            )
        )
        rows = self.platform.store.list_audit_events("lead", "lead_read_your_write")
        self.assertEqual(
            sorted(row["action"] for row in rows),
            ["lead.archived", "lead.restored"],
        )

    def test_saturated_queue_commits_the_lead_and_records_drop_risk(self) -> None:
        """The saturation policy: full queue, no flush available, lead survives."""
        platform = Platform(
            PlatformConfig(
                database_path=self.directory / "saturated.sqlite3",
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                history_queue_capacity=1,
            )
        )
        try:
            store = platform.store
            real_flush = store.flush_history
            with mock.patch.object(store, "flush_history", lambda *a, **k: 0):
                self.assertTrue(store.enqueue_history(lambda: None))
                self.assertFalse(store.enqueue_history(lambda: None))
                lead = self.load_lead()
                lead["lcp"]["payload"]["lead_id"] = "lead_saturated_001"
                lead["lcp"]["message"]["idempotency_key"] = "saturated-001"
                ack = platform.ingest(lead, headers={"X-LCP-Test": "true"})
                # The lead is committed and acknowledged regardless.
                self.assertEqual(ack["lcp"]["payload"]["status"], "RECEIVED")
                self.assertIsNotNone(store.get_lead("lead_saturated_001"))
                metrics = store.history_metrics()
                self.assertGreaterEqual(metrics["drop_risk"], 1)
                self.assertGreaterEqual(metrics["dropped"], 1)
            self.assertEqual(real_flush(), 1)
            self.assertEqual(store.history_metrics()["queued"], 0)
            # The drop-risk signal is a durable audit row, not just a counter.
            risk = store.list_audit_events("history_queue", "default")
            self.assertIn("history.write_drop_risk", [row["action"] for row in risk])
            # A subsequent intake on the drained queue still writes its trail.
            recovered = self.load_lead()
            recovered["lcp"]["message"]["id"] = "550e8400-e29b-41d4-a716-446655440555"
            recovered["lcp"]["message"]["idempotency_key"] = "recovered-001"
            platform.ingest(recovered, headers={"X-LCP-Test": "true"})
            platform.process_once()
            actions = [
                row["action"]
                for row in store.list_audit_events("lead", "lead_deferred_001")
            ]
            self.assertIn("lead.routed", actions)
        finally:
            platform.close()

    def test_failed_history_write_is_counted_not_raised(self) -> None:
        def explode() -> None:
            raise RuntimeError("disk on fire")

        self.platform.store.enqueue_history(explode)
        self.assertEqual(self.platform.store.flush_history(), 0)
        metrics = self.platform.store.history_metrics()
        self.assertEqual(metrics["failed"], 1)
        actions = [
            row["action"]
            for row in self.platform.store.list_audit_events("history_queue", "default")
        ]
        self.assertIn("history.write_failed", actions)

    def test_close_does_not_discard_buffered_history(self) -> None:
        platform = Platform(
            PlatformConfig(
                database_path=self.directory / "closing.sqlite3",
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
            )
        )
        platform.store.enqueue_history(
            lambda: platform.store.insert_audit(
                tenant_id="default",
                actor_id="operator",
                action="lead.archived",
                resource_type="lead",
                resource_id="lead_closing_001",
            )
        )
        platform.close()
        reopened = Store(self.directory / "closing.sqlite3")
        try:
            rows = reopened.list_audit_events("lead", "lead_closing_001")
            self.assertEqual([row["action"] for row in rows], ["lead.archived"])
        finally:
            reopened.close()


class RetentionTest(_StoreCase):
    """W4-06: configurable windows, unreadable-before-deletion, batched sweep."""

    def test_defaults_and_environment_overrides(self) -> None:
        config = PlatformConfig(database_path=Path(":memory:"), test_mode=True)
        self.assertEqual(config.envelope_retention_days, 90)
        self.assertEqual(config.history_retention_days, 365)
        self.assertEqual(config.history_queue_capacity, 1024)
        with mock.patch.dict(
            os.environ,
            {
                "LCP_TEST_MODE": "1",
                "LCP_ENVELOPE_RETENTION_DAYS": "30",
                "LCP_HISTORY_RETENTION_DAYS": "90",
                "LCP_HISTORY_QUEUE_CAPACITY": "16",
            },
            clear=False,
        ):
            overridden = PlatformConfig.from_env()
        self.assertEqual(overridden.envelope_retention_days, 30)
        self.assertEqual(overridden.history_retention_days, 90)
        self.assertEqual(overridden.history_queue_capacity, 16)
        with self.assertRaises(ValueError):
            PlatformConfig(
                database_path=Path(":memory:"),
                test_mode=True,
                envelope_retention_days=0,
            )
        with self.assertRaises(ValueError):
            PlatformConfig(
                database_path=Path(":memory:"),
                test_mode=True,
                history_retention_days=0,
            )

    def _age(self, table: str, column: str, value: str) -> None:
        with sqlite3.connect(self.database) as connection:
            connection.execute(f"UPDATE {table} SET {column} = ?", (value,))
            connection.commit()

    def _count(self, table: str) -> int:
        with sqlite3.connect(self.database) as connection:
            return int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )

    def test_expired_rows_are_unreadable_before_deletion_then_deleted(self) -> None:
        platform = Platform(
            PlatformConfig(
                database_path=self.database,
                schema_root=ROOT / "schemas",
                platform_id="platform_001",
                require_auth=False,
                allow_insecure_webhooks=True,
                test_mode=True,
                envelope_retention_days=1,
                history_retention_days=1,
            )
        )
        try:
            store = platform.store
            lead = stale_envelope("lead_old_0001")
            self.assertTrue(store.insert_lead(lead, status="NEW"))
            store.complete_routing_job("lead_old_0001")
            store.insert_audit(
                tenant_id="default",
                actor_id="pub_001",
                action="lead.routed",
                resource_type="lead",
                resource_id="lead_old_0001",
            )
            store.insert_match_decision(
                lead_id="lead_old_0001",
                offer_id="offer-1",
                buyer_id="buyer-1",
                matched=True,
                reasons=["vertical_match"],
            )
            store.insert_match_audit(
                lead_id="lead_old_0001",
                offer_id="offer-1",
                buyer_id="buyer-1",
                matched=True,
                reasons=["vertical_match"],
            )
            old = "2020-01-01T00:00:00Z"
            self._age("leads", "created_at", old)
            self._age("audit_events", "created_at", old)
            self._age("match_decisions", "created_at", old)
            self._age("match_audits", "created_at", old)

            # Reachability is a read-time rule: the sweep has not run yet.
            self.assertEqual(store.list_audit_events("lead", "lead_old_0001"), [])
            self.assertEqual(store.list_match_decisions("lead_old_0001"), [])
            self.assertEqual(store.list_match_audits("lead_old_0001"), [])
            self.assertEqual(self._count("audit_events"), 1)

            platform.process_once()

            self.assertEqual(self._count("match_decisions"), 0)
            self.assertEqual(self._count("match_audits"), 0)
            # Envelope ciphertext is gone; the accounting row is not.
            self.assertEqual(self._count("leads"), 1)
            redacted = store.decode_envelope(
                store.get_lead("lead_old_0001")["envelope_json"]
            )
            self.assertEqual(set(redacted["lcp"]["payload"]), {"lead_id"})
            self.assertNotIn("consumer", json.dumps(redacted))
        finally:
            platform.close()

    def test_sweep_is_idempotent_and_interval_gated(self) -> None:
        store = Store(
            self.database, envelope_retention_days=1, history_retention_days=1
        )
        try:
            self.assertIsInstance(store.sweep_retention(), dict)
            self.assertEqual(store.sweep_retention(), {})  # gated for the interval
            self.assertIsInstance(store.sweep_retention(force=True), dict)
            self.assertGreater(_RETENTION_SWEEP_INTERVAL_SECONDS, 0)
        finally:
            store.close()

    def test_retention_keeps_live_deliveries_readable(self) -> None:
        store = Store(self.database, envelope_retention_days=1)
        try:
            store.insert_lead(stale_envelope("lead_live_0001"), status="NEW")
            store.complete_routing_job("lead_live_0001")
            store.insert_delivery(
                lead_id="lead_live_0001",
                ping_id=None,
                offer_id="offer-1",
                buyer_id="buyer-1",
                kind="post",
                envelope=stale_envelope("lead_live_0001", message={"type": "post"}),
                webhook_url="http://127.0.0.1/post",
            )
            self._age("deliveries", "created_at", "2020-01-01T00:00:00Z")
            store.sweep_retention(force=True)
            delivery = store._connection.execute(
                "SELECT status, envelope_json FROM deliveries"
            ).fetchone()
            self.assertEqual(delivery["status"], "PENDING")
            self.assertIn("lead_live_0001", delivery["envelope_json"])
        finally:
            store.close()


class ArchiveToolTest(_StoreCase):
    """W4-06: deterministic export, verified restore, erasure across archives."""

    def setUp(self) -> None:
        super().setUp()
        self.store = Store(self.database)
        self.store.insert_lead(stale_envelope("lead_arch_0001"), status="NEW")
        self.store.complete_routing_job("lead_arch_0001")
        self.store.insert_lead(stale_envelope("lead_arch_0002"), status="NEW")
        self.store.complete_routing_job("lead_arch_0002")
        self.store.insert_audit(
            tenant_id="default",
            actor_id="pub_001",
            action="lead.routed",
            resource_type="lead",
            resource_id="lead_arch_0001",
        )
        self.store.insert_match_decision(
            lead_id="lead_arch_0001",
            offer_id="offer-1",
            buyer_id="buyer-1",
            matched=False,
            reasons=["vertical_mismatch"],
        )
        self.store.flush_history()
        self.store.close()

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ARCHIVE_TOOL), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_works(self) -> None:
        result = self.run_tool("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export", result.stdout)
        self.assertIn("import", result.stdout)

    def test_export_is_deterministic_and_round_trips(self) -> None:
        first = self.directory / "first.jsonl"
        second = self.directory / "second.jsonl"
        tables = (
            "--table",
            "leads",
            "--table",
            "audit_events",
            "--table",
            "match_decisions",
        )
        for out in (first, second):
            result = self.run_tool(
                "export",
                "--database",
                str(self.database),
                *tables,
                "--before",
                "2030-01-01",
                "--out",
                str(out),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())

        manifest = json.loads(
            Path(f"{first}.manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            manifest["sha256"], hashlib.sha256(first.read_bytes()).hexdigest()
        )
        self.assertEqual(manifest["rows"], 4)
        self.assertEqual(manifest["before"], "2030-01-01T00:00:00Z")

        scratch = self.directory / "scratch.sqlite3"
        restored = self.run_tool(
            "import", "--file", str(first), "--database", str(scratch)
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        with sqlite3.connect(scratch) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute(
                    "SELECT lead_id, envelope_json FROM leads ORDER BY lead_id"
                ).fetchall(),
                sqlite3.connect(self.database)
                .execute("SELECT lead_id, envelope_json FROM leads ORDER BY lead_id")
                .fetchall(),
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM match_decisions").fetchone()[
                    0
                ],
                1,
            )

    def test_partial_archive_is_refused(self) -> None:
        archive = self.directory / "full.jsonl"
        result = self.run_tool(
            "export",
            "--database",
            str(self.database),
            "--table",
            "leads",
            "--before",
            "2030-01-01",
            "--out",
            str(archive),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        truncated = self.directory / "truncated.jsonl"
        truncated.write_bytes(archive.read_bytes().rsplit(b"\n", 2)[0] + b"\n")
        Path(f"{truncated}.manifest.json").write_text(
            Path(f"{archive}.manifest.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        scratch = self.directory / "partial.sqlite3"
        refused = self.run_tool(
            "import", "--file", str(truncated), "--database", str(scratch)
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refusing", refused.stderr.lower() + refused.stdout.lower())
        with sqlite3.connect(scratch) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertFalse(tables & {"leads", "audit_events"})

    def test_erased_subject_is_absent_from_a_fresh_export(self) -> None:
        store = Store(self.database)
        try:
            self.assertTrue(store.erase_lead("lead_arch_0002"))
        finally:
            store.close()

        archive = self.directory / "post-erasure.jsonl"
        result = self.run_tool(
            "export",
            "--database",
            str(self.database),
            "--table",
            "leads",
            "--table",
            "audit_events",
            "--before",
            "2030-01-01",
            "--out",
            str(archive),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = archive.read_text(encoding="utf-8")
        self.assertIn("lead_arch_0001", body)
        self.assertNotIn("lead_arch_0002", body)
        manifest = json.loads(
            Path(f"{archive}.manifest.json").read_text(encoding="utf-8")
        )
        self.assertGreaterEqual(manifest["refused_erased"], 1)

        # An explicit erasure set refuses a live subject the same way.
        erasure_set = self.directory / "erasures.txt"
        erasure_set.write_text("# dispute erasure\nlead_arch_0001\n", encoding="utf-8")
        explicit = self.directory / "explicit.jsonl"
        result = self.run_tool(
            "export",
            "--database",
            str(self.database),
            "--table",
            "leads",
            "--before",
            "2030-01-01",
            "--out",
            str(explicit),
            "--erasure-set",
            str(erasure_set),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("lead_arch_0001", explicit.read_text(encoding="utf-8"))

        # The archive digest is recorded for already-archived data.
        ledger = Path(f"{archive}.ledger.jsonl")
        entries = [
            json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(entries[-1]["sha256"], manifest["sha256"])
        self.assertIn("leads", entries[-1]["tables"])


class CapacityGateTest(unittest.TestCase):
    """W4-07: CI thresholds are regression detectors, not capacity guarantees."""

    def test_performance_workflow_keeps_measured_thresholds(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "performance.yml").read_text(
            encoding="utf-8"
        )
        for flag in (
            "--min-candidate-reduction-percent",
            "--min-records-per-second",
            "--min-deliveries-per-second",
            "--min-end-to-end-records-per-second",
        ):
            self.assertIn(flag, workflow)
        self.assertIn("Upload performance reports", workflow)

    def test_operations_states_thresholds_are_regression_detectors(self) -> None:
        operations = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        self.assertIn("regression", operations.lower())
        self.assertIn("not a capacity guarantee", operations.lower())
        self.assertIn("LCP_ENVELOPE_RETENTION_DAYS", operations)
        self.assertIn("LCP_HISTORY_RETENTION_DAYS", operations)
        self.assertIn("tools/archive_store.py", operations)


if __name__ == "__main__":
    unittest.main()

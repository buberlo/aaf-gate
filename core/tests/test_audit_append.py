import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.canon import hash_value
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.service import Service
from framework.store import Store

from seed_config import seed_config
from test_i1 import open_service

ROOT = Path(__file__).resolve().parents[2]


def template_body():
    return json.loads((ROOT / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "Wir rufen zurück, sobald ein Termin frei ist.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


def audit_triplets(path):
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT tenant_id, sequence, event_id FROM audit_events ORDER BY tenant_id, sequence"
    ).fetchall()
    conn.close()
    return [tuple(row) for row in rows]


def tenant_sequences(path, tenant):
    conn = sqlite3.connect(path)
    nums = [
        row[0]
        for row in conn.execute(
            "SELECT sequence FROM audit_events WHERE tenant_id=? ORDER BY sequence",
            (tenant,),
        ).fetchall()
    ]
    conn.close()
    return nums


class AuditAppendTests(unittest.TestCase):
    def test_au01_per_tenant_gapless_sequences(self):
        folder = tempfile.mkdtemp()
        path, service = open_service(folder)
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "a1", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        service.command(
            "bea",
            {"operation": "create_run", "idempotency_key": "b1", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "a2", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        for tenant in ("demo-a", "demo-b"):
            nums = tenant_sequences(path, tenant)
            self.assertEqual(sorted(nums), list(range(1, len(nums) + 1)))

    def test_au02_deterministic_event_id_across_databases(self):
        clock = FixedClock("2026-11-14T09:00:00Z")
        results = []
        for _ in range(2):
            folder = tempfile.mkdtemp()
            path, service = open_service(folder)
            service.clock = clock
            service.command(
                "ada",
                {"operation": "create_run", "idempotency_key": "s1", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
            )
            results.append(audit_triplets(path))
        self.assertEqual(results[0], results[1])
        self.assertTrue(results[0])

    def test_au03_config_release_audit_carries_command_id(self):
        folder = tempfile.mkdtemp()
        store = Store(str(Path(folder) / "cfg.sqlite"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane = ConfigPlane(store, FixedClock("2026-11-14T09:00:00Z"))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "k-au3")
        conn = sqlite3.connect(str(Path(folder) / "cfg.sqlite"))
        row = conn.execute(
            "SELECT operation, outcome, command_id FROM audit_events WHERE operation='config.release'"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("config.release", "committed", "k-au3"))

    def test_au04_single_audit_insert_site(self):
        framework = Path(__file__).resolve().parents[1] / "framework"
        inserts = []
        counters = []
        pattern = re.compile(r"(INSERT|UPDATE|DELETE|CREATE TABLE).*audit_counter", re.IGNORECASE)
        for path in framework.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "INSERT INTO audit_events" in text:
                inserts.append(path.name)
            if pattern.search(text):
                counters.append(path.name)
        self.assertEqual(inserts, ["audit.py"])
        self.assertEqual(counters, [])

    def test_au05_outdated_audit_counter_aborts(self):
        path = str(Path(tempfile.mkdtemp()) / "old.sqlite")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE audit_counter (id INTEGER PRIMARY KEY CHECK (id = 1), seq INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO audit_counter (id, seq) VALUES (1, 0)")
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError) as caught:
            Store(path)
        self.assertEqual(str(caught.exception), "schema_audit_outdated")

    def test_au06_event_id_matches_hash(self):
        folder = tempfile.mkdtemp()
        path, service = open_service(folder)
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "h1", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT tenant_id, sequence, event_id FROM audit_events LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row[2], hash_value({"sequence": row[1], "tenant_id": row[0]}))


if __name__ == "__main__":
    unittest.main()
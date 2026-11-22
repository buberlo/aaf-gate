import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.audit import verify
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.errors import CommandError
from framework.store import Store

from seed_config import seed_config
from test_i1 import Harness, fixture, open_service

ROOT = Path(__file__).resolve().parents[2]
CANARY_47 = "KANARIE-47-a8d2"


def template_body():
    return json.loads((ROOT / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "We call back as soon as a slot is available.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mon-Fri 8-17",
        "shop_name": "Synth-Workshop",
    }


class AuditChainTests(unittest.TestCase):
    def test_ac01_rejections_record_attempted_operation(self):
        folder = tempfile.mkdtemp()
        path, service = open_service(folder)
        service.store.add_principal("ops", "demo-a", "human")
        service.store.bind_role("ops", "operator")
        lab = Harness(folder)
        lab.path = path
        lab.service = service
        lab.principal_id = "ada"
        run = lab.create()
        for name in ("d1.json", "d2.json"):
            lab.register(run, fixture(name))
        validated = lab.call("validate", run, expected_state_version=1, expected_payload_revision=0)
        drafted = lab.call("build_draft", validated, expected_state_version=2, expected_payload_revision=0)
        reviewed = lab.call("request_review", drafted, expected_state_version=3, expected_payload_revision=1)
        exported = lab.call("export_run", reviewed["run_id"])
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        cases = (
            (
                "ops",
                {
                    "operation": "approve",
                    "run_id": reviewed["run_id"],
                    "idempotency_key": "k-approve",
                    "expected_state_version": reviewed["state_version"],
                    "expected_payload_revision": exported["run"]["payload_revision"],
                    "payload_hash": exported["run"]["artifact_hash"],
                    "rules_hash": exported["run"]["rules_hash"],
                    "payload": {
                        "conflict_disposition": disposition,
                        "input_manifest_hash": exported["run"]["input_manifest_hash"],
                        "payload_hash": exported["run"]["artifact_hash"],
                        "payload_revision": exported["run"]["payload_revision"],
                        "purpose": "commit_preparation",
                        "rules_hash": exported["run"]["rules_hash"],
                        "rules_version": exported["run"]["rules_version"],
                    },
                },
                "FORBIDDEN",
            ),
            (
                "ada",
                {"operation": "send", "idempotency_key": "k-send", "payload": {}},
                "FORBIDDEN",
            ),
            (
                "ada",
                {"operation": "foo", "idempotency_key": "k-foo", "payload": {}},
                "INVALID_TRANSITION",
            ),
        )
        for actor, body, code in cases:
            with self.assertRaises(CommandError) as caught:
                service.command(actor, body)
            self.assertEqual(caught.exception.code, code)
        conn = sqlite3.connect(path)
        rows = conn.execute(
            """SELECT operation, outcome, error_code, command_id FROM audit_events
               WHERE outcome='rejected' AND command_id IS NOT NULL ORDER BY sequence"""
        ).fetchall()
        conn.close()
        by_op = {row[0]: row for row in rows}
        self.assertEqual(by_op["approve"][1], "rejected")
        self.assertEqual(by_op["approve"][2], "FORBIDDEN")
        self.assertEqual(by_op["approve"][3], "k-approve")
        self.assertEqual(by_op["send"][2], "FORBIDDEN")
        self.assertEqual(by_op["unknown"][3], "k-foo")

    def test_ac02_register_template_invalid_body_audited(self):
        folder = tempfile.mkdtemp()
        store = Store(str(Path(folder) / "cfg.sqlite"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane = ConfigPlane(store, FixedClock("2026-11-14T09:30:00Z"))
        before = sqlite3.connect(str(Path(folder) / "cfg.sqlite")).execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        with self.assertRaises(ConfigError) as caught:
            plane.register_template("konrad", {"x": 1})
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        conn = sqlite3.connect(str(Path(folder) / "cfg.sqlite"))
        rows = conn.execute(
            "SELECT operation, outcome, error_code FROM audit_events WHERE operation='config.register_template'"
        ).fetchall()
        count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        conn.close()
        self.assertEqual(count, before + 1)
        self.assertEqual(rows[-1], ("config.register_template", "rejected", "INVALID_CONFIG"))

    def test_ac03_no_plaintext_in_audit(self):
        lab = Harness()
        store = lab.service.store
        seed_config(
            store,
            (("konrad", "synth-tenant", "human", ("author",)), ("cust", "synth-tenant", "human", ("customer",)),),
        )
        plane = ConfigPlane(store, FixedClock("2026-11-14T09:30:00Z"))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel")
        with self.assertRaises(ConfigError):
            plane.apply_changes("cust", "cfg-1", {"followup_questions": CANARY_47}, "c3")
        raw = Path(lab.path).read_bytes()
        self.assertNotIn(CANARY_47.encode("utf-8"), raw)

    def test_ac04_verify_matches_head(self):
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
        cfg_store = Store(path)
        plane = ConfigPlane(cfg_store, FixedClock("2026-11-14T09:30:00Z"))
        seed_config(cfg_store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-x", "auto-service-appointment-intake", "1.1.0", initial_values(), "rk")
        conn = cfg_store.connect()
        for tenant in ("demo-a", "demo-b", "synth-tenant"):
            result = verify(conn, tenant)
            head = conn.execute("SELECT head_hash FROM audit_heads WHERE tenant_id=?", (tenant,)).fetchone()
            count = conn.execute("SELECT COUNT(*) FROM audit_events WHERE tenant_id=?", (tenant,)).fetchone()[0]
            self.assertTrue(result["ok"], tenant)
            self.assertEqual(result["count"], count)
            if count:
                self.assertEqual(result["head"], head["head_hash"])
        conn.close()

    def test_ac05_triggers_block_update_and_delete(self):
        folder = tempfile.mkdtemp()
        path, service = open_service(folder)
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "t1", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        conn = sqlite3.connect(path)
        before = conn.execute("SELECT outcome FROM audit_events").fetchone()[0]
        with self.assertRaises(sqlite3.DatabaseError) as caught:
            conn.execute("UPDATE audit_events SET outcome='hacked' WHERE sequence=1")
        self.assertIn("AUDIT_APPEND_ONLY", str(caught.exception))
        with self.assertRaises(sqlite3.DatabaseError) as caught:
            conn.execute("DELETE FROM audit_events WHERE sequence=1")
        self.assertIn("AUDIT_APPEND_ONLY", str(caught.exception))
        after = conn.execute("SELECT outcome FROM audit_events").fetchone()[0]
        conn.close()
        self.assertEqual(before, after)

    def test_ac06_drop_update_trigger_breaks_chain(self):
        folder = tempfile.mkdtemp()
        path, service = open_service(folder)
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "x1", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "x2", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        store = Store(path)
        conn = store.connect()
        conn.execute("DROP TRIGGER audit_no_update")
        conn.execute("UPDATE audit_events SET outcome='hacked' WHERE sequence=2")
        conn.commit()
        result = verify(conn, "demo-a")
        conn.close()
        self.assertFalse(result["ok"])
        self.assertEqual(result["first_break"], 2)

    def test_ac07_delete_without_trigger_changes_head(self):
        folder = tempfile.mkdtemp()
        path, service = open_service(folder)
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "d1", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "d2", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        store = Store(path)
        conn = store.connect()
        conn.execute("DROP TRIGGER audit_no_delete")
        conn.execute("DELETE FROM audit_events WHERE sequence=2")
        conn.commit()
        result = verify(conn, "demo-a")
        head_row = conn.execute("SELECT head_hash FROM audit_heads WHERE tenant_id='demo-a'").fetchone()
        conn.close()
        self.assertFalse(result["ok"])
        self.assertTrue(result["head_mismatch"])
        self.assertNotEqual(result["head"], head_row["head_hash"])

    def test_ac08_old_audit_schema_aborts(self):
        path = str(Path(tempfile.mkdtemp()) / "old.sqlite")
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE audit_events (
                event_id TEXT PRIMARY KEY,
                sequence INTEGER NOT NULL,
                schema_version TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                UNIQUE (tenant_id, sequence)
            )"""
        )
        conn.execute("CREATE TABLE audit_heads (tenant_id TEXT PRIMARY KEY, seq INTEGER NOT NULL)")
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError) as caught:
            Store(path)
        self.assertEqual(str(caught.exception), "schema_audit_outdated")


if __name__ == "__main__":
    unittest.main()
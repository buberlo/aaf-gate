import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.errors import CommandError
from framework.releases import active_release_hash
from framework.rules import load_rules
from framework.service import ROOT, Service
from framework.store import Store

from seed_config import seed_config
from test_i1 import fixture, seed_principals

FIXTURES = ROOT / "fixtures"
TEMPLATE_ROOT = Path(__file__).resolve().parents[2]
CLOCK = FixedClock("2026-11-14T10:30:00Z")
CONFIG_ID = "cfg-run"


def template_body():
    return json.loads((TEMPLATE_ROOT / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "We call back as soon as a slot is available.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mon-Fri 8-17",
        "shop_name": "Synth-Workshop",
    }


class RunReleaseLab:
    def __init__(self, folder=None):
        self.folder = folder or tempfile.mkdtemp()
        self.path = str(Path(self.folder) / "run-release.sqlite")
        self.store = Store(self.path)
        seed_principals(self.store)
        seed_config(
            self.store,
            (
                ("konrad", "demo-a", "human", ("author",)),
                ("customer", "demo-a", "human", ("customer",)),
            ),
        )
        rules = load_rules(FIXTURES / "material-prep-1.json")
        self.service = Service(self.store, rules, clock=CLOCK)
        self.plane = ConfigPlane(self.store, CLOCK)
        self.plane.register_template("konrad", template_body())
        released = self.plane.release(
            "konrad",
            CONFIG_ID,
            "auto-service-appointment-intake",
            "1.1.0",
            initial_values(),
            "rel-1",
        )
        self.release_hash = released["release_hash"]
        self.keys = 0

    def call(self, operation, run=None, payload=None, actor="ada", **extra):
        self.keys += 1
        command = {"operation": operation, "idempotency_key": extra.pop("idempotency_key", f"rl{self.keys}")}
        if run is not None:
            command["run_id"] = run["run_id"] if isinstance(run, dict) else run
        if payload is not None:
            command["payload"] = payload
        command.update(extra)
        return self.service.command(actor, command)

    def to_review(self, config_id=CONFIG_ID):
        created = self.call(
            "create_run",
            payload={"rules_version": "material-prep-1", "config_id": config_id},
            expected_state_version=0,
        )
        run = created
        self.call("register_source", run, fixture("d1.json"), expected_state_version=1, expected_payload_revision=0)
        validated = self.call("validate", run, expected_state_version=1, expected_payload_revision=0)
        drafted = self.call(
            "build_draft",
            validated,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
        )
        reviewed = self.call(
            "request_review",
            drafted,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
        )
        return reviewed

    def approve(self, reviewed):
        exported = self.call("export_run", reviewed["run_id"])
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        return self.call(
            "approve",
            reviewed,
            {
                "conflict_disposition": disposition,
                "input_manifest_hash": exported["run"]["input_manifest_hash"],
                "payload_hash": exported["run"]["artifact_hash"],
                "payload_revision": exported["run"]["payload_revision"],
                "purpose": "commit_preparation",
                "rules_hash": exported["run"]["rules_hash"],
                "rules_version": exported["run"]["rules_version"],
            },
            expected_state_version=reviewed["state_version"],
            expected_payload_revision=exported["run"]["payload_revision"],
            payload_hash=exported["run"]["artifact_hash"],
            rules_hash=exported["run"]["rules_hash"],
        )


class RunReleaseTests(unittest.TestCase):
    def test_rl01_create_run_binds_active_release(self):
        lab = RunReleaseLab()
        created = lab.call(
            "create_run",
            payload={"rules_version": "material-prep-1", "config_id": CONFIG_ID},
            expected_state_version=0,
        )
        exported = lab.call("export_run", created)
        conn = lab.store.connect()
        active = active_release_hash(conn, "demo-a", CONFIG_ID)
        conn.close()
        self.assertEqual(exported["run"]["release_hash"], active)
        self.assertEqual(exported["run"]["config_id"], CONFIG_ID)

    def test_rl02_stale_release_on_approve_after_customer_change(self):
        lab = RunReleaseLab()
        reviewed = lab.to_review()
        exported = lab.call("export_run", reviewed)
        start_hash = exported["run"]["release_hash"]
        lab.plane.apply_changes("customer", CONFIG_ID, {"opening_hours": "Mon-Fri 9-18"}, "customer-hours")
        with self.assertRaises(CommandError) as ctx:
            lab.approve(reviewed)
        self.assertEqual(ctx.exception.code, "STALE_RELEASE")
        state = lab.call("export_run", reviewed)
        self.assertEqual(state["run"]["state"], "REVIEW")
        conn = lab.store.connect()
        row = conn.execute(
            """SELECT operation, outcome, error_code FROM audit_events
               WHERE operation='approve' AND outcome='rejected' ORDER BY sequence DESC LIMIT 1"""
        ).fetchone()
        active = active_release_hash(conn, "demo-a", CONFIG_ID)
        conn.close()
        self.assertEqual(row["error_code"], "STALE_RELEASE")
        self.assertNotEqual(start_hash, active)

    def test_rl03_rollback_restores_hash_for_approve(self):
        lab = RunReleaseLab()
        reviewed = lab.to_review()
        exported = lab.call("export_run", reviewed)
        bound = exported["run"]["release_hash"]
        lab.plane.apply_changes("customer", CONFIG_ID, {"opening_hours": "Mon-Fri 9-18"}, "chg")
        lab.plane.rollback("konrad", CONFIG_ID, 1, "back")
        conn = lab.store.connect()
        self.assertEqual(active_release_hash(conn, "demo-a", CONFIG_ID), bound)
        conn.close()
        approved = lab.approve(reviewed)
        self.assertEqual(approved["state"], "APPROVED")

    def test_rl04_stale_release_on_commit(self):
        lab = RunReleaseLab()
        reviewed = lab.to_review()
        approved = lab.approve(reviewed)
        exported = lab.call("export_run", approved)
        lab.plane.apply_changes("customer", CONFIG_ID, {"opening_hours": "Mon-Fri 10-16"}, "late")
        with self.assertRaises(CommandError) as ctx:
            lab.call(
                "commit",
                approved,
                expected_state_version=approved["state_version"],
                expected_payload_revision=exported["run"]["payload_revision"],
                approval_id=approved["approval_id"],
            )
        self.assertEqual(ctx.exception.code, "STALE_RELEASE")
        state = lab.call("export_run", approved)
        self.assertEqual(state["run"]["state"], "APPROVED")

    def test_rl05_create_run_config_guards(self):
        lab = RunReleaseLab()
        with self.assertRaises(CommandError) as ctx:
            lab.call(
                "create_run",
                payload={"rules_version": "material-prep-1", "config_id": CONFIG_ID},
                actor="bea",
                expected_state_version=0,
            )
        self.assertEqual(ctx.exception.code, "NOT_FOUND")
        with self.assertRaises(CommandError) as ctx:
            lab.call(
                "create_run",
                payload={"rules_version": "material-prep-1", "config_id": 5},
                expected_state_version=0,
            )
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_rl06_pipeline_without_config_succeeds(self):
        lab = RunReleaseLab()
        run = lab.call("create_run", payload={"rules_version": "material-prep-1"}, expected_state_version=0)
        lab.call("register_source", run, fixture("d1.json"), expected_state_version=1, expected_payload_revision=0)
        validated = lab.call("validate", run, expected_state_version=1, expected_payload_revision=0)
        drafted = lab.call(
            "build_draft",
            validated,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
        )
        reviewed = lab.call(
            "request_review",
            drafted,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
        )
        approved = lab.approve(reviewed)
        self.assertEqual(approved["state"], "APPROVED")
        exported = lab.call("export_run", approved)
        self.assertIsNone(exported["run"]["config_id"])
        self.assertIsNone(exported["run"]["release_hash"])
        conn = lab.store.connect()
        row = conn.execute(
            "SELECT config_id, release_hash FROM runs WHERE run_id=?",
            (approved["run_id"],),
        ).fetchone()
        conn.close()
        self.assertIsNone(row["config_id"])
        self.assertIsNone(row["release_hash"])

    def test_rl07_migrate_legacy_runs_table(self):
        path = str(Path(tempfile.mkdtemp()) / "legacy.sqlite")
        conn = sqlite3.connect(path)
        conn.executescript(
            """CREATE TABLE runs (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                object_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                module_id TEXT NOT NULL,
                module_version TEXT NOT NULL,
                rules_version TEXT NOT NULL,
                rules_hash TEXT NOT NULL,
                rules_body_json TEXT NOT NULL,
                input_manifest_hash TEXT NOT NULL,
                source_refs_json TEXT NOT NULL,
                state TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                payload_revision INTEGER NOT NULL,
                artifact_json TEXT,
                artifact_hash TEXT,
                blocked_from TEXT,
                origin_run_id TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, run_id)
            );"""
        )
        conn.close()
        store = Store(path)
        conn = store.connect()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        conn.close()
        self.assertIn("config_id", columns)
        self.assertIn("release_hash", columns)


if __name__ == "__main__":
    unittest.main()
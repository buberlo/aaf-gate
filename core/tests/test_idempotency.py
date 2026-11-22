import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.errors import CommandError
from framework.service import Service
from framework.store import Store

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


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def _config_plane(self):
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        for principal_id, roles in (("konrad", ("author",)), ("second-author", ("author",))):
            store.add_principal(principal_id, "synth-idem", "human")
            for role in roles:
                store.bind_role(principal_id, role)
        return ConfigPlane(store, FixedClock("2026-11-14T08:30:00Z"))

    def test_id01_two_authors_same_key_distinct_releases(self):
        plane = self._config_plane()
        plane.register_template("konrad", template_body())
        values = initial_values()
        first = plane.release("konrad", "cfg-a", "auto-service-appointment-intake", "1.1.0", values, "k1")
        second = plane.release("second-author", "cfg-b", "auto-service-appointment-intake", "1.1.0", values, "k1")
        self.assertEqual(first["release_no"], 1)
        self.assertEqual(second["release_no"], 1)
        self.assertNotEqual(first["release_hash"], second["release_hash"])

    def test_id02_replay_audits_config_and_service(self):
        plane = self._config_plane()
        plane.register_template("konrad", template_body())
        values = initial_values()
        first = plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", values, "rk")
        second = plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", values, "rk")
        self.assertEqual(first, second)
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        config_replayed = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE operation='config.release' AND outcome='replayed'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(config_replayed, 1)
        path = str(Path(self.folder) / "svc.sqlite")
        service = Service.open(path)
        for principal_id, roles in (("ada", ("operator",)),):
            service.store.add_principal(principal_id, "demo-a", "human")
            for role in roles:
                service.store.bind_role(principal_id, role)
        created = service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "sk", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        replayed = service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "sk", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        self.assertEqual(created["run_id"], replayed["run_id"])
        conn = sqlite3.connect(path)
        svc_replayed = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE operation='create_run' AND outcome='replayed'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(svc_replayed, 1)

    def test_id03_hash_mismatch_conflict_both_layers(self):
        plane = self._config_plane()
        plane.register_template("konrad", template_body())
        values = initial_values()
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", values, "ck")
        with self.assertRaises(ConfigError) as caught:
            plane.release(
                "konrad",
                "cfg-2",
                "auto-service-appointment-intake",
                "1.1.0",
                {**values, "shop_name": "Andere"},
                "ck",
            )
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        path = str(Path(self.folder) / "svc2.sqlite")
        service = Service.open(path)
        service.store.add_principal("ada", "demo-a", "human")
        service.store.bind_role("ada", "operator")
        service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "ck", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        with self.assertRaises(CommandError) as caught:
            service.command(
                "ada",
                {
                    "operation": "create_run",
                    "idempotency_key": "ck",
                    "expected_state_version": 1,
                    "payload": {"rules_version": "material-prep-1"},
                },
            )
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_id04_invalid_then_valid_under_same_key(self):
        plane = self._config_plane()
        plane.register_template("konrad", template_body())
        values = dict(initial_values())
        values["allow_unattended_send"] = True
        with self.assertRaises(ConfigError) as caught:
            plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", values, "k2")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        reject = conn.execute(
            "SELECT outcome, error_code FROM audit_events WHERE operation='config.release' AND outcome='rejected'"
        ).fetchone()
        idem_count = conn.execute("SELECT COUNT(*) FROM config_idempotency").fetchone()[0]
        conn.close()
        self.assertEqual(reject, ("rejected", "INVALID_CONFIG"))
        self.assertEqual(idem_count, 0)
        ok = plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "k2")
        self.assertEqual(ok["release_no"], 1)

    def test_id05_foreign_service_key_no_replay(self):
        path = str(Path(self.folder) / "svc3.sqlite")
        service = Service.open(path)
        for principal_id, roles in (
            ("ada", ("operator",)),
            ("bea", ("operator",)),
        ):
            service.store.add_principal(principal_id, "demo-a", "human")
            for role in roles:
                service.store.bind_role(principal_id, role)
        first = service.command(
            "ada",
            {"operation": "create_run", "idempotency_key": "fk", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        second = service.command(
            "bea",
            {"operation": "create_run", "idempotency_key": "bea-own", "expected_state_version": 0, "payload": {"rules_version": "material-prep-1"}},
        )
        self.assertNotEqual(first["run_id"], second["run_id"])
        with self.assertRaises(CommandError) as caught:
            service.command(
                "bea",
                {
                    "operation": "get_receipt",
                    "idempotency_key": "lookup",
                    "payload": {"operation": "create_run", "idempotency_key": "fk"},
                },
            )
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_id06_outdated_receipts_schema_aborts(self):
        path = str(Path(self.folder) / "old.sqlite")
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE receipts (
                tenant_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                object_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                actor_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                result_id TEXT NOT NULL,
                result_revision INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                result_json TEXT NOT NULL,
                run_id TEXT,
                PRIMARY KEY (tenant_id, operation, idempotency_key)
            )"""
        )
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError) as caught:
            Store(path)
        self.assertEqual(str(caught.exception), "schema_idempotency_outdated")

    def test_id07_idempotency_tables_only_in_module(self):
        root = Path(__file__).resolve().parents[1]
        self_file = Path(__file__).resolve()
        patterns = ("FROM receipts", "FROM config_idempotency")
        hits = []
        for path in (root / "framework").rglob("*.py"):
            if path.name == "idempotency.py":
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                if pattern in text:
                    hits.append(f"{path}:{pattern}")
        for path in (root / "tests").rglob("*.py"):
            if path.resolve() == self_file:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                if pattern in text:
                    hits.append(f"{path}:{pattern}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
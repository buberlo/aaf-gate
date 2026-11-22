import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.store import Store

ROOT = Path(__file__).resolve().parents[2]


def template_body():
    return json.loads((ROOT / "templates" / "kfz-terminannahme" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "Wir rufen zurück, sobald ein Termin frei ist.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


def seed_author(store, tenant="synth-cp", principal_id="konrad"):
    store.add_principal(principal_id, tenant, "human")
    store.bind_role(principal_id, "author")


class ConfigPrincipalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-14T08:00:00Z"))
        seed_author(self.store)

    def test_cp01_unknown_principal_release_forbidden_and_audited(self):
        with self.assertRaises(ConfigError) as caught:
            self.plane.release(
                "nobody",
                "cfg-1",
                "kfz-terminannahme",
                "1.1.0",
                initial_values(),
                "k1",
            )
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        row = conn.execute(
            "SELECT tenant_id, outcome, error_code FROM audit_events WHERE operation='config.release'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "unresolved")
        self.assertEqual(row[1], "rejected")
        self.assertEqual(row[2], "FORBIDDEN")

    def test_cp02_invalid_principal_pattern_audit_invalid_actor(self):
        with self.assertRaises(ConfigError) as caught:
            self.plane.register_template("Konrad X", template_body())
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        row = conn.execute(
            "SELECT actor_id FROM audit_events WHERE operation='config.register_template'"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "invalid")

    def test_cp03_disabled_author_register_forbidden(self):
        self.store.disable_principal("konrad")
        with self.assertRaises(ConfigError) as caught:
            self.plane.register_template("konrad", template_body())
        self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_cp04_agent_author_cannot_write_customer_can_read(self):
        self.store.add_principal("bot", "synth-cp", "agent")
        self.store.bind_role("bot", "author")
        self.store.add_principal("reader", "synth-cp", "agent")
        self.store.bind_role("reader", "customer")
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel")
        for op, fn in (
            ("register_template", lambda: self.plane.register_template("bot", template_body())),
            ("release", lambda: self.plane.release("bot", "cfg-2", "kfz-terminannahme", "1.1.0", initial_values(), "x")),
            ("rollback", lambda: self.plane.rollback("bot", "cfg-1", 1, "rb")),
            ("apply", lambda: self.plane.apply_changes("bot", "cfg-1", {"opening_hours": "immer"}, "a")),
        ):
            with self.assertRaises(ConfigError, msg=op) as caught:
                fn()
            self.assertEqual(caught.exception.code, "FORBIDDEN")
        active = self.plane.active("reader", "cfg-1")
        self.assertEqual(active["values"]["shop_name"], "Synth-Werkstatt")

    def test_cp05_customer_release_rollback_forbidden_apply_audited(self):
        self.store.add_principal("kunde", "synth-cp", "human")
        self.store.bind_role("kunde", "customer")
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel")
        with self.assertRaises(ConfigError) as caught:
            self.plane.release("kunde", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "bad")
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        with self.assertRaises(ConfigError) as caught:
            self.plane.rollback("kunde", "cfg-1", 1, "rb")
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.plane.apply_changes("kunde", "cfg-1", {"opening_hours": "Mo-Fr 9-18"}, "ok")
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        rows = conn.execute(
            "SELECT principal_kind, acting_role, outcome FROM audit_events WHERE actor_id='kunde' AND outcome='committed'"
        ).fetchall()
        conn.close()
        self.assertTrue(any(row[0] == "human" and row[1] == "customer" for row in rows))

    def test_cp06_foreign_tenant_active_not_found(self):
        self.store.add_principal("bea", "tenant-b", "human")
        self.store.bind_role("bea", "customer")
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel")
        with self.assertRaises(ConfigError) as caught:
            self.plane.active("bea", "cfg-1")
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_cp07_appliance_package_forbidden_no_destination(self):
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel")
        self.store.add_principal("bot", "synth-cp", "agent")
        self.store.bind_role("bot", "author")
        self.store.add_principal("kunde", "synth-cp", "human")
        self.store.bind_role("kunde", "customer")
        agent_dir = Path(self.folder) / "agent-pkg"
        cust_dir = Path(self.folder) / "cust-pkg"
        with self.assertRaises(ConfigError) as caught:
            build_appliance_package(self.plane, "bot", "cfg-1", agent_dir)
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertFalse(agent_dir.exists())
        with self.assertRaises(ConfigError) as caught:
            build_appliance_package(self.plane, "kunde", "cfg-1", cust_dir)
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertFalse(cust_dir.exists())

    def test_cp08_outdated_role_bindings_schema_aborts(self):
        path = str(Path(self.folder) / "old.sqlite")
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE role_bindings (
                principal_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('operator', 'reviewer')),
                PRIMARY KEY (principal_id, role)
            )"""
        )
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError) as caught:
            Store(path)
        self.assertEqual(str(caught.exception), "schema_role_bindings_outdated")

    def test_cp09_no_actor_in_framework_or_tests(self):
        root = Path(__file__).resolve().parents[1]
        self_file = Path(__file__).resolve()
        patterns = ("class Actor", "import Actor", "Actor(")
        hits = []
        for base in (root / "framework", root / "tests"):
            for path in base.rglob("*.py"):
                if path.resolve() == self_file:
                    continue
                text = path.read_text(encoding="utf-8")
                for pattern in patterns:
                    if pattern in text:
                        hits.append(f"{path}:{pattern}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
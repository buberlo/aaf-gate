import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from framework.cli import init_gate_db, main
from framework.gatekeeper import GateKeeper
from framework.principals import resolve
from framework.store import Store

from test_gate_e2e import PILOT_POLICY
from test_hermes_plugin import load_plugin


class CliStopSwitchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(self.db, "hermes-a", "demo-a", out=io.StringIO())
        self.plugin = load_plugin()
        self.request = self.plugin.build_request("gmail_read", {"id": "m1"})

    def _run(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(args))
        line = buf.getvalue().splitlines()[0]
        body = json.loads(line) if line else {}
        return code, body

    def test_cs01_principal_add_customer(self):
        code, body = self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "kim",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            body,
            {
                "kind": "human",
                "ok": True,
                "principal": "kim",
                "roles": ["customer"],
                "tenant": "demo-a",
            },
        )
        conn = Store(self.db).connect()
        principal = resolve(conn, "kim")
        conn.close()
        self.assertEqual(principal.kind, "human")
        self.assertEqual(principal.roles, frozenset({"customer"}))

    def test_cs02_dedupe_roles(self):
        code, body = self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "konrad",
            "--tenant",
            "demo-a",
            "--role",
            "author",
            "--role",
            "author",
            "--role",
            "reviewer",
        )
        self.assertEqual(code, 0)
        self.assertEqual(body["roles"], ["author", "reviewer"])

    def test_cs03_invalid_role(self):
        code, body = self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "x",
            "--tenant",
            "demo-a",
            "--role",
            "admin",
        )
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "invalid_role")
        conn = Store(self.db).connect()
        self.assertIsNone(resolve(conn, "x"))
        conn.close()

    def test_cs04_principal_exists(self):
        code, body = self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "hermes-a",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "principal_exists")

    def test_cs05_tenant_unknown(self):
        code, body = self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "extra",
            "--tenant",
            "demo-x",
            "--role",
            "customer",
        )
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "tenant_unknown")

    def test_cs06_db_path_checks(self):
        missing = self.folder + "/missing.sqlite"
        code, body = self._run(
            "gate-stop", "--db", missing, "--principal", "kim"
        )
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "gate_db_unreadable")
        self.assertFalse(os.path.exists(missing))
        code2, body2 = self._run(
            "gate-principal-add",
            "--db",
            "gate.sqlite",
            "--principal",
            "kim",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        self.assertEqual(code2, 2)
        self.assertEqual(body2["error"], "db_path_not_absolute")

    def test_cs07_stop_and_status(self):
        self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "kim",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        code, body = self._run("gate-stop", "--db", self.db, "--principal", "kim")
        self.assertEqual(code, 0)
        self.assertEqual(body["command"], "gate-stop")
        self.assertTrue(body["ok"])
        self.assertTrue(body["changed"])
        self.assertTrue(body["stopped"])
        status_code, status = self._run(
            "gate-status", "--db", self.db, "--tenant", "demo-a"
        )
        self.assertEqual(status_code, 0)
        self.assertTrue(status["stopped"])
        self.assertEqual(status["changed_by"], "kim")
        self.assertIsInstance(status["audit_sequence"], int)

    def test_cs08_agent_stop_forbidden(self):
        code, body = self._run(
            "gate-stop", "--db", self.db, "--principal", "hermes-a"
        )
        self.assertEqual(code, 1)
        self.assertEqual(body["reason"], "principal_kind_forbidden")
        status_code, status = self._run(
            "gate-status", "--db", self.db, "--tenant", "demo-a"
        )
        self.assertEqual(status_code, 0)
        self.assertFalse(status["stopped"])

    def test_cs09_customer_resume_forbidden(self):
        self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "kim",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        self._run("gate-stop", "--db", self.db, "--principal", "kim")
        code, body = self._run("gate-resume", "--db", self.db, "--principal", "kim")
        self.assertEqual(code, 1)
        self.assertEqual(body["reason"], "role_forbidden")
        _, status = self._run("gate-status", "--db", self.db, "--tenant", "demo-a")
        self.assertTrue(status["stopped"])

    def test_cs10_author_resume_and_noop(self):
        self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "kim",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "konrad",
            "--tenant",
            "demo-a",
            "--role",
            "author",
        )
        self._run("gate-stop", "--db", self.db, "--principal", "kim")
        code, body = self._run(
            "gate-resume", "--db", self.db, "--principal", "konrad"
        )
        self.assertEqual(code, 0)
        self.assertFalse(body["stopped"])
        again_code, again = self._run(
            "gate-resume", "--db", self.db, "--principal", "konrad"
        )
        self.assertEqual(again_code, 0)
        self.assertFalse(again["changed"])

    def test_cs11_unknown_principal(self):
        code, body = self._run(
            "gate-stop", "--db", self.db, "--principal", "ghost"
        )
        self.assertEqual(code, 1)
        self.assertEqual(body["reason"], "principal_unknown")

    def test_cs12_status_and_missing_table(self):
        code, body = self._run(
            "gate-status", "--db", self.db, "--tenant", "demo-a"
        )
        self.assertEqual(code, 0)
        self.assertFalse(body["stopped"])
        self.assertIsNone(body["changed_at"])
        mtime_before = os.path.getmtime(self.db)
        code2, _ = self._run("gate-status", "--db", self.db, "--tenant", "demo-a")
        self.assertEqual(code2, 0)
        self.assertEqual(os.path.getmtime(self.db), mtime_before)
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TABLE gate_stops")
        conn.commit()
        conn.close()
        code3, body3 = self._run(
            "gate-status", "--db", self.db, "--tenant", "demo-a"
        )
        self.assertEqual(code3, 2)
        self.assertEqual(body3["error"], "stop_state_unavailable")

    def test_cs13_audit_summary_stops(self):
        self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "kim",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "konrad",
            "--tenant",
            "demo-a",
            "--role",
            "author",
        )
        self._run("gate-stop", "--db", self.db, "--principal", "kim")
        keeper = GateKeeper(Store(self.db))
        result = keeper.check("hermes-a", self.request, PILOT_POLICY)
        self.assertEqual(result["reason"], "stopped")
        self._run("gate-resume", "--db", self.db, "--principal", "kim")
        self._run("gate-resume", "--db", self.db, "--principal", "konrad")
        code, body = self._run(
            "gate-audit", "--db", self.db, "--tenant", "demo-a", "--summary"
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            body["summary"]["stops"],
            {
                "denied_while_stopped": 1,
                "rejected": 1,
                "resume": 1,
                "stop": 1,
                "stopped": False,
            },
        )
        self.assertTrue(body["verify"]["ok"])


if __name__ == "__main__":
    unittest.main()
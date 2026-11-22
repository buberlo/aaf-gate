import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.audit import GENESIS, verify
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.store import Store

from test_gate import sample_policy, sample_request

EVENT_FIELDS = (
    "sequence",
    "occurred_at",
    "operation",
    "outcome",
    "error_code",
    "actor_id",
    "principal_kind",
    "payload_hash",
    "rule_hash",
    "approval_id",
    "context",
)


class GateAuditCliTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")

    def _init_db(self, tenant="demo-a"):
        init_gate_db(self.db, "hermes-a", tenant, out=io.StringIO())
        store = Store(self.db)
        return store

    def _run_audit(self, tenant="demo-a", limit=50, db=None):
        db = db if db is not None else self.db
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", db, "--tenant", tenant, "--limit", str(limit)])
        body = json.loads(buf.getvalue().splitlines()[0])
        return code, body

    def _three_decisions(self):
        store = self._init_db()
        keeper = GateKeeper(store, FixedClock("2026-11-14T15:00:00Z"))
        keeper.check("hermes-a", sample_request(), sample_policy())
        keeper.check(
            "hermes-a",
            sample_request(action="send"),
            sample_policy(),
        )
        keeper.check("hermes-a", sample_request(action="read"), sample_policy())
        return store

    def test_ga01_empty_db(self):
        self._init_db()
        code, body = self._run_audit()
        self.assertEqual(code, 0)
        self.assertEqual(body["events"], [])
        self.assertTrue(body["ok"])
        self.assertEqual(body["verify"]["count"], 0)
        self.assertFalse(body["verify"]["head_mismatch"])

    def test_ga02_three_events_and_limit(self):
        self._three_decisions()
        code, body = self._run_audit()
        self.assertEqual(code, 0)
        self.assertEqual(body["verify"]["count"], 3)
        sequences = [entry["sequence"] for entry in body["events"]]
        self.assertEqual(sequences, [1, 2, 3])
        for entry in body["events"]:
            self.assertEqual(sorted(entry.keys()), sorted(EVENT_FIELDS))
        limited_code, limited = self._run_audit(limit=2)
        self.assertEqual(limited_code, 0)
        self.assertEqual([e["sequence"] for e in limited["events"]], [2, 3])

    def test_ga03_readonly_db(self):
        self._three_decisions()
        before_bytes = Path(self.db).read_bytes()
        before_mtime = os.stat(self.db).st_mtime_ns
        parent = Path(self.db).parent
        before_sidecars = {
            p.name
            for p in parent.iterdir()
            if p.name.startswith(Path(self.db).name + "-")
        }
        self._run_audit()
        after_bytes = Path(self.db).read_bytes()
        after_mtime = os.stat(self.db).st_mtime_ns
        after_sidecars = {
            p.name
            for p in parent.iterdir()
            if p.name.startswith(Path(self.db).name + "-")
        }
        self.assertEqual(before_bytes, after_bytes)
        self.assertEqual(before_mtime, after_mtime)
        self.assertEqual(before_sidecars, after_sidecars)

    def test_ga04_truncated_chain(self):
        self._three_decisions()
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TRIGGER audit_no_delete")
        conn.execute("DELETE FROM audit_events WHERE sequence=3")
        conn.commit()
        conn.close()
        store = Store(self.db)
        result = verify(store.connect(), "demo-a")
        store.connect().close()
        self.assertFalse(result["ok"])
        self.assertTrue(result["head_mismatch"])
        self.assertIsNone(result["first_break"])
        code, body = self._run_audit()
        self.assertEqual(code, 1)
        self.assertFalse(body["ok"])

    def test_ga05_head_table_mismatch(self):
        store = self._three_decisions()
        conn = store.connect()
        conn.execute("DELETE FROM audit_heads WHERE tenant_id='demo-a'")
        conn.commit()
        conn.close()
        result = verify(store.connect(), "demo-a")
        store.connect().close()
        self.assertTrue(result["head_mismatch"])
        self.assertFalse(result["ok"])

        empty_db = str(Path(self.folder) / "empty.sqlite")
        init_gate_db(empty_db, "hermes-a", "demo-a", out=io.StringIO())
        conn = sqlite3.connect(empty_db)
        conn.execute(
            "INSERT INTO audit_heads (tenant_id, seq, head_hash) VALUES (?, ?, ?)",
            ("demo-a", 1, GENESIS),
        )
        conn.commit()
        conn.close()
        empty_store = Store(empty_db)
        empty_result = verify(empty_store.connect(), "demo-a")
        empty_store.connect().close()
        self.assertTrue(empty_result["head_mismatch"])
        self.assertFalse(empty_result["ok"])

    def test_ga06_error_paths(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rel = main(["gate-audit", "--db", "relative.sqlite", "--tenant", "demo-a"])
        self.assertEqual(rel, 2)
        self.assertEqual(
            json.loads(buf.getvalue().splitlines()[0])["error"],
            "db_path_not_absolute",
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bad_limit = main(["gate-audit", "--db", self.db, "--tenant", "demo-a", "--limit", "0"])
        self.assertEqual(bad_limit, 2)
        self.assertEqual(json.loads(buf.getvalue().splitlines()[0])["error"], "limit_invalid")
        buf = io.StringIO()
        missing = str(Path(self.folder) / "missing.sqlite")
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", missing, "--tenant", "demo-a"])
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(buf.getvalue().splitlines()[0])["error"],
            "gate_db_unreadable",
        )
        link = str(Path(self.folder) / "link.sqlite")
        os.symlink(missing, link)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", link, "--tenant", "demo-a"])
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(buf.getvalue().splitlines()[0])["error"],
            "gate_db_unreadable",
        )

    def test_ga08_db_path_special_characters(self):
        db = str(Path(self.folder) / "gate audit #1?.sqlite")
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, FixedClock("2026-11-14T15:00:00Z"))
        keeper.check("hermes-a", sample_request(), sample_policy())
        code, body = self._run_audit(db=db)
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        self.assertEqual(body["verify"]["count"], 1)
        self.assertEqual(len(body["events"]), 1)

    def test_ga07_tenant_isolation(self):
        store = self._init_db()
        store.add_principal("hermes-b", "demo-b", "agent")
        keeper = GateKeeper(store, FixedClock("2026-11-14T15:00:00Z"))
        keeper.check("hermes-a", sample_request(), sample_policy())
        keeper.check("hermes-a", sample_request(), sample_policy())
        keeper.check("hermes-b", sample_request(), sample_policy())
        code_a, body_a = self._run_audit(tenant="demo-a")
        self.assertEqual(code_a, 0)
        self.assertEqual(body_a["verify"]["count"], 2)
        self.assertEqual(len(body_a["events"]), 2)
        code_b, body_b = self._run_audit(tenant="demo-b")
        self.assertEqual(code_b, 0)
        self.assertEqual(body_b["verify"]["count"], 1)
        self.assertEqual(len(body_b["events"]), 1)


if __name__ == "__main__":
    unittest.main()
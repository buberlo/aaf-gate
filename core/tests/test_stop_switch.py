import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.audit import verify
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.stopswitch import RESUME_OPERATION, STOP_OPERATION, STOPPED_REASON
from framework.stopswitch import StopSwitch
from framework.store import Store

from test_gate_e2e import PILOT_POLICY
from test_hermes_plugin import load_plugin

CLOCK = "2026-11-21T14:00:00Z"


class StopSwitchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        self.store = Store(self.db)
        self.store.add_principal("hermes-a", "demo-a", "agent")
        self.store.add_principal("hermes-b", "demo-b", "agent")
        self.store.add_principal("kim", "demo-a", "human")
        self.store.bind_role("kim", "customer")
        self.store.add_principal("konrad", "demo-a", "human")
        self.store.bind_role("konrad", "author")
        self.store.add_principal("nobody", "demo-a", "human")
        self.clock = FixedClock(CLOCK)
        self.keeper = GateKeeper(self.store, self.clock)
        self.switch = StopSwitch(self.store, self.clock)
        self.plugin = load_plugin()
        self.valid = self.plugin.build_request("gmail_read", {"id": "m1"})

    def _rows(self, tenant="demo-a"):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY sequence",
            (tenant,),
        ).fetchall()
        conn.close()
        return rows

    def _last(self, tenant="demo-a"):
        rows = self._rows(tenant)
        return rows[-1] if rows else None

    def test_ss01_kim_stop(self):
        out = self.switch.stop("kim")
        self.assertTrue(out["ok"])
        self.assertTrue(out["changed"])
        self.assertTrue(out["stopped"])
        status = self.switch.status("demo-a")
        self.assertTrue(status["stopped"])
        self.assertEqual(status["changed_by"], "kim")
        row = self._last()
        self.assertEqual(row["operation"], STOP_OPERATION)
        self.assertEqual(row["outcome"], "allow")
        self.assertEqual(row["before_state"], "running")
        self.assertEqual(row["after_state"], "stopped")
        self.assertEqual(row["acting_role"], "customer")

    def test_ss02_check_denied_when_stopped(self):
        self.switch.stop("kim")
        result = self.keeper.check("hermes-a", self.valid, PILOT_POLICY)
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["reason"], STOPPED_REASON)
        self.assertIsNotNone(result["audit_sequence"])
        row = self._last()
        self.assertEqual(row["operation"], "gate_decide")
        self.assertEqual(row["outcome"], "deny")
        self.assertEqual(row["error_code"], STOPPED_REASON)
        self.assertIsNone(row["payload_hash"])

    def test_ss03_stopped_before_invalid_request(self):
        self.switch.stop("kim")
        result = self.keeper.check("hermes-a", {}, PILOT_POLICY)
        self.assertEqual(result["reason"], STOPPED_REASON)

    def test_ss04_tenant_isolated(self):
        self.switch.stop("kim")
        allowed = self.keeper.check("hermes-b", self.valid, PILOT_POLICY)
        self.assertEqual(allowed["decision"], "allow")

    def test_ss05_agent_cannot_stop(self):
        out = self.switch.stop("hermes-a")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "principal_kind_forbidden")
        row = self._last()
        self.assertEqual(row["operation"], STOP_OPERATION)
        self.assertEqual(row["outcome"], "deny")
        self.assertFalse(self.switch.status("demo-a")["stopped"])

    def test_ss06_agent_cannot_resume(self):
        self.switch.stop("kim")
        out = self.switch.resume("hermes-a")
        self.assertEqual(out["reason"], "principal_kind_forbidden")
        row = self._last()
        self.assertEqual(row["operation"], RESUME_OPERATION)
        self.assertEqual(row["outcome"], "deny")
        self.assertTrue(self.switch.status("demo-a")["stopped"])

    def test_ss07_customer_cannot_resume(self):
        self.switch.stop("kim")
        out = self.switch.resume("kim")
        self.assertEqual(out["reason"], "role_forbidden")
        row = self._last()
        self.assertEqual(row["outcome"], "deny")
        self.assertIsNone(row["acting_role"])
        self.assertTrue(self.switch.status("demo-a")["stopped"])

    def test_ss08_konrad_resumes(self):
        self.switch.stop("kim")
        out = self.switch.resume("konrad")
        self.assertTrue(out["ok"])
        self.assertTrue(out["changed"])
        self.assertFalse(out["stopped"])
        row = self._last()
        self.assertEqual(row["operation"], RESUME_OPERATION)
        self.assertEqual(row["outcome"], "allow")
        self.assertEqual(row["acting_role"], "author")
        allowed = self.keeper.check("hermes-a", self.valid, PILOT_POLICY)
        self.assertEqual(allowed["decision"], "allow")
        conn = self.store.connect()
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()

    def test_ss09_noop_on_repeat(self):
        self.switch.stop("kim")
        again = self.switch.stop("kim")
        self.assertTrue(again["ok"])
        self.assertFalse(again["changed"])
        row = self._last()
        self.assertEqual(row["outcome"], "noop")
        self.switch.resume("konrad")
        fresh = self.switch.resume("konrad")
        self.assertTrue(fresh["ok"])
        self.assertFalse(fresh["changed"])
        self.assertEqual(self._last()["outcome"], "noop")

    def test_ss10_nobody_stop_forbidden(self):
        out = self.switch.stop("nobody")
        self.assertEqual(out["reason"], "role_forbidden")
        row = self._last()
        self.assertEqual(row["outcome"], "deny")
        self.assertFalse(self.switch.status("demo-a")["stopped"])

    def test_ss11_unknown_principal(self):
        before = len(self._rows())
        stop = self.switch.stop("ghost")
        self.assertEqual(stop["reason"], "principal_unknown")
        resume = self.switch.resume("ghost")
        self.assertEqual(resume["reason"], "principal_unknown")
        self.assertEqual(len(self._rows()), before)

    def test_ss12_missing_table_fail_closed(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TABLE gate_stops")
        conn.commit()
        conn.close()
        result = self.keeper.check("hermes-a", self.valid, PILOT_POLICY)
        self.assertEqual(result["reason"], "audit_unavailable")


if __name__ == "__main__":
    unittest.main()
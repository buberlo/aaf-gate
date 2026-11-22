import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.audit import verify
from framework.clock import FixedClock
from framework.gate import decide
from framework.gatekeeper import BUDGET_EXCEEDED, BUDGET_UNCONFIGURED, GateKeeper
from framework.stopswitch import STOPPED_REASON, StopSwitch
from framework.store import Store

from test_gate_e2e import PILOT_POLICY
from test_hermes_plugin import load_plugin

T0 = "2026-11-21T14:00:00Z"


def policy_limits(**overrides):
    out = copy.deepcopy(PILOT_POLICY)
    out["limits"] = {
        "read": {"window_seconds": 3600, "max_calls": 2},
    }
    for key, value in overrides.items():
        if key == "levels":
            out["levels"].update(value)
        elif key == "limits":
            out["limits"] = value
        else:
            out[key] = value
    return out


class GateBudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        self.store = Store(self.db)
        self.store.add_principal("hermes-a", "demo-a", "agent")
        self.store.add_principal("hermes-b", "demo-b", "agent")
        self.store.add_principal("kim", "demo-a", "human")
        self.store.bind_role("kim", "customer")
        self.plugin = load_plugin()
        self.valid = self.plugin.build_request("gmail_read", {"id": "m1"})

    def _keeper(self, when):
        return GateKeeper(self.store, FixedClock(when))

    def _switch(self, when):
        return StopSwitch(self.store, FixedClock(when))

    def _calls(self, tenant="demo-a"):
        conn = sqlite3.connect(self.db)
        n = conn.execute(
            "SELECT COUNT(*) FROM gate_calls WHERE tenant_id=?", (tenant,)
        ).fetchone()[0]
        conn.close()
        return n

    def _last_audit(self, tenant="demo-a"):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY sequence DESC LIMIT 1",
            (tenant,),
        ).fetchone()
        conn.close()
        return row

    def test_bg01_no_limits(self):
        keeper = self._keeper(T0)
        for _ in range(5):
            r = keeper.check("hermes-a", self.valid, PILOT_POLICY)
            self.assertEqual(r["decision"], "allow")
        self.assertEqual(self._calls(), 0)

    def test_bg02_exceeds_limit(self):
        policy = policy_limits()
        keeper = self._keeper(T0)
        for _ in range(2):
            self.assertEqual(
                keeper.check("hermes-a", self.valid, policy)["decision"], "allow"
            )
        third = keeper.check("hermes-a", self.valid, policy)
        self.assertEqual(third["decision"], "deny")
        self.assertEqual(third["reason"], BUDGET_EXCEEDED)
        self.assertEqual(third["action_class"], "read")
        row = self._last_audit()
        self.assertEqual(row["error_code"], BUDGET_EXCEEDED)

    def test_bg03_count_after_deny(self):
        policy = policy_limits()
        keeper = self._keeper(T0)
        for _ in range(2):
            keeper.check("hermes-a", self.valid, policy)
        keeper.check("hermes-a", self.valid, policy)
        self.assertEqual(self._calls(), 2)

    def test_bg04_window_rollover(self):
        policy = policy_limits()
        k0 = self._keeper(T0)
        for _ in range(2):
            self.assertEqual(k0.check("hermes-a", self.valid, policy)["decision"], "allow")
        t1 = "2026-11-21T15:00:00Z"
        k1 = self._keeper(t1)
        self.assertEqual(k1.check("hermes-a", self.valid, policy)["decision"], "allow")

    def test_bg05_before_window_end(self):
        policy = policy_limits()
        k0 = self._keeper(T0)
        for _ in range(2):
            k0.check("hermes-a", self.valid, policy)
        t = "2026-11-21T14:59:59Z"
        denied = self._keeper(t).check("hermes-a", self.valid, policy)
        self.assertEqual(denied["decision"], "deny")
        self.assertEqual(denied["reason"], BUDGET_EXCEEDED)

    def test_bg06_tenant_isolated(self):
        policy = policy_limits()
        keeper = self._keeper(T0)
        for _ in range(3):
            keeper.check("hermes-a", self.valid, policy)
        other = keeper.check("hermes-b", self.valid, policy)
        self.assertEqual(other["decision"], "allow")
        self.assertEqual(self._calls("demo-b"), 1)

    def test_bg07_zero_max_calls(self):
        policy = policy_limits(limits={"read": {"window_seconds": 3600, "max_calls": 0}})
        r = self._keeper(T0).check("hermes-a", self.valid, policy)
        self.assertEqual(r["decision"], "deny")
        self.assertEqual(r["reason"], BUDGET_EXCEEDED)

    def test_bg08_missing_class(self):
        policy = policy_limits(limits={"send": {"window_seconds": 3600, "max_calls": 2}})
        r = self._keeper(T0).check("hermes-a", self.valid, policy)
        self.assertEqual(r["decision"], "deny")
        self.assertEqual(r["reason"], BUDGET_UNCONFIGURED)
        self.assertEqual(self._calls(), 0)

    def test_bg09_empty_limits(self):
        policy = policy_limits(limits={})
        r = self._keeper(T0).check("hermes-a", self.valid, policy)
        self.assertEqual(r["reason"], BUDGET_UNCONFIGURED)
        self.assertEqual(self._calls(), 0)

    def test_bg10_stop_no_count(self):
        policy = policy_limits()
        switch = self._switch(T0)
        switch.stop("kim")
        r = self._keeper(T0).check("hermes-a", self.valid, policy)
        self.assertEqual(r["reason"], STOPPED_REASON)
        self.assertEqual(self._calls(), 0)

    def test_bg11_needs_approval_counts(self):
        policy = policy_limits(levels={"read": "L3"}, limits={"read": {"window_seconds": 3600, "max_calls": 1}})
        keeper = self._keeper(T0)
        first = keeper.check("hermes-a", self.valid, policy)
        self.assertEqual(first["decision"], "needs_approval")
        second = keeper.check("hermes-a", self.valid, policy)
        self.assertEqual(second["decision"], "deny")
        self.assertEqual(second["reason"], BUDGET_EXCEEDED)

    def test_bg12_decide_deny_no_count(self):
        policy = policy_limits()
        bad = self.plugin.build_request("gmail_delete", {"id": "m1"})
        r = self._keeper(T0).check("hermes-a", bad, policy)
        self.assertEqual(r["reason"], "action_class_unknown")
        self.assertEqual(self._calls(), 0)

    def test_bg13_invalid_limits_policy(self):
        req = self.valid
        base_limits = {"read": {"window_seconds": 3600, "max_calls": 2}}
        cases = {
            "window_seconds_59": {"read": {"window_seconds": 59, "max_calls": 2}},
            "window_seconds_86401": {"read": {"window_seconds": 86401, "max_calls": 2}},
            "window_seconds_bool": {"read": {"window_seconds": True, "max_calls": 2}},
            "max_calls_neg": {"read": {"window_seconds": 3600, "max_calls": -1}},
            "max_calls_100001": {"read": {"window_seconds": 3600, "max_calls": 100001}},
            "extra_key": {
                "read": {"window_seconds": 3600, "max_calls": 2, "extra": 1}
            },
            "unknown_class": {"nope": {"window_seconds": 3600, "max_calls": 2}},
            "value_not_dict": {"read": 1},
        }
        for name, limits in cases.items():
            with self.subTest(name):
                policy = {**PILOT_POLICY, "limits": limits}
                self.assertEqual(decide(req, policy)["reason"], "invalid_policy")
        valid = {**PILOT_POLICY, "limits": base_limits}
        self.assertEqual(decide(req, valid)["decision"], "allow")

    def test_bg14_audit_verify(self):
        policy = policy_limits()
        keeper = self._keeper(T0)
        for _ in range(2):
            keeper.check("hermes-a", self.valid, policy)
        keeper.check("hermes-a", self.valid, policy)
        conn = self.store.connect()
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()


if __name__ == "__main__":
    unittest.main()
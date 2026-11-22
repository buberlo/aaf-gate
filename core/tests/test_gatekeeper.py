import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework.audit import verify
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.hermes_adapter import build_observation, register_with
from framework.store import Store

from test_gate import sample_policy, sample_request
from test_hermes_adapter import FakeCtx


class GateKeeperTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = str(Path(self.folder) / "gate.sqlite")
        self.store = Store(self.path)
        self.store.add_principal("hermes-a", "demo-a", "agent")
        self.store.add_principal("hermes-b", "demo-b", "agent")
        self.store.add_principal("ada", "demo-a", "human")
        self.store.add_principal("off", "demo-a", "agent")
        self.store.disable_principal("off")
        self.keeper = GateKeeper(self.store, FixedClock("2026-11-14T15:00:00Z"))

    def _audit_rows(self, tenant_id):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY sequence",
            (tenant_id,),
        ).fetchall()
        conn.close()
        return rows

    def test_gk01_agent_read_allow_audited(self):
        out = self.keeper.check("hermes-a", sample_request(), sample_policy())
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(out["audit_sequence"], 1)
        rows = self._audit_rows("demo-a")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["operation"], "gate_decide")
        self.assertEqual(row["outcome"], "allow")
        self.assertEqual(row["payload_hash"], out["request_hash"])
        self.assertEqual(row["rule_hash"], out["policy_hash"])
        self.assertEqual(row["after_state"], "L4")
        self.assertIsNone(row["error_code"])
        self.assertEqual(row["principal_kind"], "agent")
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()

    def test_gk02_send_needs_approval_audited(self):
        out = self.keeper.check(
            "hermes-a",
            sample_request(action="send"),
            sample_policy(),
        )
        self.assertEqual(out["decision"], "needs_approval")
        row = self._audit_rows("demo-a")[0]
        self.assertEqual(row["outcome"], "needs_approval")
        self.assertEqual(row["error_code"], "approval_required")

    def test_gk03_invalid_request_audited_without_payload_hash(self):
        req = sample_request()
        req["extra"] = "x"
        out = self.keeper.check("hermes-a", req, sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "invalid_request")
        row = self._audit_rows("demo-a")[0]
        self.assertIsNone(row["payload_hash"])

    def test_gk04_unknown_principal_no_audit(self):
        out = self.keeper.check("ghost", sample_request(), sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "principal_unknown")
        self.assertIsNone(out["audit_sequence"])
        self.assertEqual(len(self._audit_rows("demo-a")), 0)
        self.assertEqual(len(self._audit_rows("demo-b")), 0)

    def test_gk05_disabled_principal_no_audit(self):
        out = self.keeper.check("off", sample_request(), sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "principal_unknown")
        self.assertIsNone(out["audit_sequence"])
        self.assertEqual(len(self._audit_rows("demo-a")), 0)

    def test_gk06_human_forbidden_audited(self):
        out = self.keeper.check("ada", sample_request(), sample_policy())
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "principal_kind_forbidden")
        self.assertIsNone(out["request_hash"])
        rows = self._audit_rows("demo-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error_code"], "principal_kind_forbidden")

    def test_gk07_audit_failure_denies_without_row(self):
        with patch("framework.gatekeeper.audit_append", side_effect=sqlite3.OperationalError("x")):
            out = self.keeper.check("hermes-a", sample_request(), sample_policy())
            self.assertEqual(out["decision"], "deny")
            self.assertEqual(out["reason"], "audit_unavailable")
        self.assertEqual(len(self._audit_rows("demo-a")), 0)

    def test_gk08_tenant_isolation(self):
        one = self.keeper.check("hermes-a", sample_request(), sample_policy())
        two = self.keeper.check("hermes-b", sample_request(), sample_policy())
        self.assertEqual(one["audit_sequence"], 1)
        self.assertEqual(two["audit_sequence"], 1)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        self.assertTrue(verify(conn, "demo-b")["ok"])
        conn.close()

    def test_gk09_no_plaintext_target_in_audit(self):
        secret = "secret-ziel-4711"
        out = self.keeper.check(
            "hermes-a",
            sample_request(target=secret),
            sample_policy(),
        )
        self.assertEqual(out["decision"], "allow")
        for row in self._audit_rows("demo-a"):
            blob = " ".join(str(row[key]) for key in row.keys())
            self.assertNotIn(secret, blob)

    def _observation(self, **overrides):
        base = build_observation(
            "gmail",
            "gate_mismatch",
            "sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
        )
        base.update(overrides)
        return base

    def test_gk10_observe_mismatch_flagged(self):
        obs = self._observation()
        out = self.keeper.observe("hermes-a", obs)
        self.assertTrue(out["ok"])
        rows = self._audit_rows("demo-a")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["operation"], "gate_observe")
        self.assertEqual(row["outcome"], "flagged")
        self.assertEqual(row["error_code"], "gate_mismatch")
        self.assertEqual(row["payload_hash"], obs["observed_hash"])
        self.assertEqual(row["before_state"], obs["expected_hash"])
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()

    def test_gk11_observe_invalid_no_audit(self):
        base = self._observation()
        cases = [
            {**base, "extra": "x"},
            {**base, "schema_version": "wrong"},
            {**base, "kind": "unknown"},
            build_observation("gmail", "gate_unseen", "sha256:" + "0" * 64, "sha256:" + "1" * 64),
            {**base, "observed_hash": "bad"},
        ]
        for obs in cases:
            with self.subTest(obs=obs):
                out = self.keeper.observe("hermes-a", obs)
                self.assertFalse(out["ok"])
                self.assertEqual(out["reason"], "invalid_observation")
        self.assertEqual(len(self._audit_rows("demo-a")), 0)

    def test_gk12_observe_principal_rules(self):
        obs = build_observation("gmail", "gate_unseen", None, "sha256:" + "2" * 64)
        human = self.keeper.observe("ada", obs)
        self.assertFalse(human["ok"])
        self.assertEqual(human["reason"], "principal_kind_forbidden")
        self.assertEqual(len(self._audit_rows("demo-a")), 1)
        ghost = self.keeper.observe("ghost", obs)
        self.assertFalse(ghost["ok"])
        self.assertEqual(ghost["reason"], "principal_unknown")

    def test_gk13_register_observe_e2e_no_plaintext(self):
        ctx = FakeCtx()
        register_with(
            ctx,
            self.keeper.bind("hermes-a", sample_policy()),
            report=self.keeper.bind_observe("hermes-a"),
        )
        pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
        post = next(cb for name, cb in ctx.hooks if name == "post_tool_call")
        secret = "secret-inhalt-9191"
        args = {"body": secret}
        pre("gmail", args, tool_call_id="tc-1")
        post("gmail", {"body": "changed"}, tool_call_id="tc-1")
        rows = self._audit_rows("demo-a")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["operation"], "gate_decide")
        self.assertEqual(rows[1]["operation"], "gate_observe")
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()
        blob = " ".join(str(row[k]) for row in rows for k in row.keys())
        self.assertNotIn(secret, blob)
        self.assertNotIn("changed", blob)


if __name__ == "__main__":
    unittest.main()
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from framework.audit import verify
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.gatesocket import GateServer
from framework.store import Store

from test_hermes_plugin import ConfigFakeCtx, load_plugin

PILOT_POLICY = {
    "schema_version": "gate-policy-0.1-draft",
    "tool_classes": {
        "gmail_read:*": "read",
        "gmail_search:*": "read",
        "gmail_label:*": "internal_write",
        "gmail_draft:*": "draft",
        "gmail_send:*": "send",
    },
    "levels": {
        "read": "L4",
        "draft": "L4",
        "internal_write": "L4",
        "send": "L3",
    },
}


class GateE2ETests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        self.sock = self.folder + "/g.sock"
        self.store = Store(self.db)
        self.store.add_principal("hermes-a", "demo-a", "agent")
        self.store.add_principal("ada", "demo-a", "human")
        self.keeper = GateKeeper(self.store, FixedClock("2026-11-14T17:00:00Z"))
        self.plugin = load_plugin()
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.close()
            self.server = None

    def _plugin_cb(self, check):
        self.server = GateServer(self.sock, check)
        self.server.start()
        time.sleep(0.05)
        ctx = ConfigFakeCtx(socket_path=self.sock)
        self.plugin.register(ctx)
        return ctx.hooks[0][1]

    def _audit_rows(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id='demo-a' ORDER BY sequence"
        ).fetchall()
        conn.close()
        return rows

    def test_e2e01_bind_matches_check(self):
        req = self.plugin.build_request("gmail_read", {"id": "1"})
        bound = self.keeper.bind("hermes-a", PILOT_POLICY)(req)
        direct = self.keeper.check("hermes-a", req, PILOT_POLICY)
        for key in (
            "decision",
            "reason",
            "action_class",
            "effective_level",
            "request_hash",
            "policy_hash",
        ):
            self.assertEqual(bound[key], direct[key])

    def test_e2e02_pilot_flow(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", PILOT_POLICY))
        self.assertIsNone(cb("gmail_read", {"id": "m1"}))
        self.assertIsNone(cb("gmail_label", {"id": "m1", "label": "Rechnung"}))
        self.assertIsNone(cb("gmail_draft", {"thread": "t1", "body": "geheim-entwurf-4711"}))
        args_send = {"to": "x@example.invalid", "body": "geheim-body-0815"}
        approve = cb("gmail_send", args_send)
        self.assertEqual(approve["action"], "approve")
        self.assertEqual(
            approve["rule_key"],
            "aaf:gmail_send:call:" + self.plugin.hash_value(args_send),
        )
        rows = self._audit_rows()
        self.assertEqual(len(rows), 4)
        self.assertTrue(
            all(row["operation"] == "gate_decide" for row in rows)
        )
        self.assertEqual(
            [row["outcome"] for row in rows],
            ["allow", "allow", "allow", "needs_approval"],
        )
        calls = [
            ("gmail_read", {"id": "m1"}),
            ("gmail_label", {"id": "m1", "label": "Rechnung"}),
            ("gmail_draft", {"thread": "t1", "body": "geheim-entwurf-4711"}),
            ("gmail_send", args_send),
        ]
        for row, (tool, args) in zip(rows, calls):
            expected = self.plugin.hash_value(self.plugin.build_request(tool, args))
            self.assertEqual(row["payload_hash"], expected)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()

    def test_e2e03_no_raw_content_in_audit(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", PILOT_POLICY))
        cb("gmail_draft", {"thread": "t1", "body": "geheim-entwurf-4711"})
        cb("gmail_send", {"to": "x@example.invalid", "body": "geheim-body-0815"})
        forbidden = (
            "geheim-entwurf-4711",
            "geheim-body-0815",
            "x@example.invalid",
            "Rechnung",
        )
        blob = " ".join(
            str(row[key])
            for row in self._audit_rows()
            for key in row.keys()
        )
        for part in forbidden:
            self.assertNotIn(part, blob)

    def test_e2e04_unknown_tool_blocked_and_audited(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", PILOT_POLICY))
        out = cb("gmail_delete", {"id": "m1"})
        self.assertEqual(
            out,
            {"action": "block", "message": "Core: denied (action_class_unknown)"},
        )
        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "deny")
        self.assertEqual(rows[0]["error_code"], "action_class_unknown")

    def test_e2e05_unattended_caps_send(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", PILOT_POLICY, unattended=True))
        out = cb("gmail_send", {"to": "x@example.invalid", "body": "b"})
        self.assertEqual(out["message"], "Core: denied (level_L2)")
        self.assertIsNone(cb("gmail_read", {"id": "m1"}))

    def test_e2e06_human_principal_forbidden(self):
        cb = self._plugin_cb(self.keeper.bind("ada", PILOT_POLICY))
        out = cb("gmail_read", {"id": "m1"})
        self.assertEqual(out["message"], "Core: denied (principal_kind_forbidden)")
        rows = self._audit_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "deny")

    def test_e2e07_unknown_principal(self):
        cb = self._plugin_cb(self.keeper.bind("ghost", PILOT_POLICY))
        out = cb("gmail_read", {"id": "m1"})
        self.assertEqual(out["message"], "Core: denied (principal_unknown)")
        self.assertEqual(len(self._audit_rows()), 0)

    def test_e2e08_storage_unavailable(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", PILOT_POLICY))
        with patch.object(Store, "connect", side_effect=sqlite3.OperationalError("x")):
            out = cb("gmail_read", {"id": "m1"})
        self.assertEqual(out["message"], "Core: denied (storage_unavailable)")

    def test_e2e09_socket_post_observe_chain(self):
        plugin = load_plugin()
        policy = PILOT_POLICY
        self.server = GateServer(
            self.sock,
            self.keeper.bind("hermes-a", policy),
            self.keeper.bind_observe("hermes-a"),
        )
        self.server.start()
        time.sleep(0.05)
        ctx = ConfigFakeCtx(socket_path=self.sock)
        plugin.register(ctx)
        pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
        post = next(cb for name, cb in ctx.hooks if name == "post_tool_call")
        self.assertIsNone(pre("gmail_read", {"id": "m1"}, tool_call_id="c1"))
        post("gmail_read", {"id": "m2"}, tool_call_id="c1")
        post("gmail_read", {"id": "x"}, tool_call_id="c9")
        rows = self._audit_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["operation"], "gate_decide")
        self.assertEqual(rows[0]["outcome"], "allow")
        self.assertEqual(rows[1]["operation"], "gate_observe")
        self.assertEqual(rows[1]["outcome"], "flagged")
        self.assertEqual(rows[1]["error_code"], "gate_mismatch")
        self.assertEqual(rows[2]["operation"], "gate_observe")
        self.assertEqual(rows[2]["error_code"], "gate_unseen")
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()
        for row in rows:
            for key in row.keys():
                value = row[key]
                if value is None:
                    continue
                text = str(value)
                self.assertNotEqual(text, "m1")
                self.assertNotEqual(text, "m2")
                self.assertNotIn('"id"', text)


if __name__ == "__main__":
    unittest.main()
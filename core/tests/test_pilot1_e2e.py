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
from framework import hermes_mcp
from framework.store import Store

from test_hermes_plugin import ConfigFakeCtx, load_plugin

POLICY = hermes_mcp.pilot1_policy()
NAMES = [hermes_mcp.registered_name("gmail", t) for t in hermes_mcp.GMAIL_MCP_TOOLS]

LABEL_NAMES = [
    hermes_mcp.registered_name("gmail", t)
    for t in hermes_mcp.GMAIL_MCP_TOOLS
    if hermes_mcp.PILOT1_CLASSES[t] == "internal_write"
]

READ_DRAFT_NAMES = [n for n in NAMES if n not in LABEL_NAMES]

NON_GMAIL_TOOLS = (
    "terminal",
    "execute_code",
    "read_file",
    "write_file",
    "mcp_gmail_list_resources",
    "mcp_gmail_read_resource",
    "mcp_gmail_list_prompts",
    "mcp_gmail_get_prompt",
    "mcp_gmail_send_message",
    "mcp_gmailwork_get_message",
)


class Pilot1E2ETests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        self.sock = self.folder + "/g.sock"
        self.store = Store(self.db)
        self.store.add_principal("hermes-a", "demo-a", "agent")
        self.store.add_principal("ada", "demo-a", "human")
        self.keeper = GateKeeper(self.store, FixedClock("2026-11-14T18:40:00Z"))
        self.plugin = load_plugin()
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.close()
            self.server = None

    def _plugin_cb(self, check, label_ids=None):
        if label_ids is None:
            label_ids = ["Label_1"]
        self.server = GateServer(self.sock, check)
        self.server.start()
        time.sleep(0.05)
        ctx = ConfigFakeCtx(socket_path=self.sock, label_ids=label_ids)
        self.plugin.register(ctx)
        return next(cb for name, cb in ctx.hooks if name == "pre_tool_call")

    @staticmethod
    def _args_for_tool(name):
        if name.endswith("_label_thread") or name.endswith("_unlabel_thread"):
            return {"threadId": "t1", "labelIds": ["Label_1"]}
        if name.endswith("_label_message") or name.endswith("_unlabel_message"):
            return {"messageId": "m1", "labelIds": ["Label_1"]}
        return {"q": "x"}

    def _audit_rows(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id='demo-a' AND operation='gate_decide' ORDER BY sequence"
        ).fetchall()
        conn.close()
        return rows

    def _all_audit_text(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id='demo-a' ORDER BY sequence"
        ).fetchall()
        conn.close()
        return " ".join(str(row[key]) for row in rows for key in row.keys() if row[key] is not None)

    def test_p1e01_attended_all_ten_allowed(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", POLICY))
        for name in NAMES:
            self.assertIsNone(cb(name, self._args_for_tool(name)))
        rows = self._audit_rows()
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["outcome"] == "allow" for row in rows))
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()

    def test_p1e02_unattended_labels_denied(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", POLICY, unattended=True))
        block = {"action": "block", "message": "Core: denied (level_L2)"}
        for name in LABEL_NAMES:
            self.assertEqual(cb(name, self._args_for_tool(name)), block)
        for name in READ_DRAFT_NAMES:
            self.assertIsNone(cb(name, self._args_for_tool(name)))
        rows = self._audit_rows()
        self.assertEqual(len(rows), 10)
        denies = [row for row in rows if row["outcome"] == "deny"]
        allows = [row for row in rows if row["outcome"] == "allow"]
        self.assertEqual(len(denies), 4)
        self.assertEqual(len(allows), 6)
        self.assertTrue(all(row["error_code"] == "level_L2" for row in denies))

    def test_p1e03_non_gmail_tools_blocked(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", POLICY))
        block = {"action": "block", "message": "Core: denied (action_class_unknown)"}
        args = {"command": "cat ~/.hermes/mcp-tokens/gmail.json"}
        for tool in NON_GMAIL_TOOLS:
            self.assertEqual(cb(tool, args), block, msg=tool)
        rows = self._audit_rows()
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["outcome"] == "deny" for row in rows))
        self.assertTrue(all(row["error_code"] == "action_class_unknown" for row in rows))

    def test_p1e04_no_raw_content_in_audit(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", POLICY))
        cb("mcp_gmail_search_threads", {"query": "from:geheim-absender-4711"})
        cb("mcp_gmail_create_draft", {"body": "geheim-entwurf-0815", "to": "y@example.invalid"})
        cb("terminal", {"command": "cat ~/.hermes/mcp-tokens/gmail.json"})
        forbidden = (
            "geheim-absender-4711",
            "geheim-entwurf-0815",
            "y@example.invalid",
            "mcp-tokens",
        )
        blob = self._all_audit_text()
        for token in forbidden:
            self.assertNotIn(token, blob)

    def test_p1e05_payload_hash_matches_plugin(self):
        cb = self._plugin_cb(self.keeper.bind("hermes-a", POLICY))
        calls = [
            ("mcp_gmail_get_thread", {"id": "t1"}),
            ("mcp_gmail_create_draft", {"body": "b"}),
        ]
        for tool, args in calls:
            cb(tool, args)
        rows = self._audit_rows()
        self.assertEqual(len(rows), 2)
        for row, (tool, args) in zip(rows, calls):
            expected = self.plugin.hash_value(self.plugin.build_request(tool, args))
            self.assertEqual(row["payload_hash"], expected)

    def test_p1e06_server_name_binding(self):
        policy = hermes_mcp.pilot1_policy("gmailwork")
        cb = self._plugin_cb(self.keeper.bind("hermes-a", policy))
        self.assertIsNone(cb("mcp_gmailwork_get_message", {"id": "m1"}))
        self.assertEqual(
            cb("mcp_gmail_get_message", {"id": "m1"}),
            {"action": "block", "message": "Core: denied (action_class_unknown)"},
        )


if __name__ == "__main__":
    unittest.main()
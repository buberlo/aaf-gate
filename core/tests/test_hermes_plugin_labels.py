import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.gatesocket import GateServer
from framework import hermes_adapter, hermes_mcp
from framework.store import Store

from test_hermes_plugin import ConfigFakeCtx, load_plugin

BLOCK_UNKNOWN = {"action": "block", "message": "Kern: verweigert (action_class_unknown)"}
LABEL_1 = frozenset(["Label_1"])
READ_DRAFT = [
    hermes_mcp.registered_name("gmail", t)
    for t in hermes_mcp.GMAIL_MCP_TOOLS
    if hermes_mcp.PILOT1_CLASSES[t] in ("read", "draft")
]
LABEL_TOOLS = {
    hermes_mcp.registered_name("gmail", "label_message"): {
        "messageId": "m1",
        "labelIds": ["Label_1"],
    },
    hermes_mcp.registered_name("gmail", "label_thread"): {
        "threadId": "t1",
        "labelIds": ["Label_1"],
    },
    hermes_mcp.registered_name("gmail", "unlabel_message"): {
        "messageId": "m1",
        "labelIds": ["Label_1"],
    },
    hermes_mcp.registered_name("gmail", "unlabel_thread"): {
        "threadId": "t1",
        "labelIds": ["Label_1"],
    },
}


class HermesPluginLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = load_plugin()
        cls.policy = hermes_mcp.pilot1_policy()

    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        self.sock = self.folder + "/g.sock"
        self.store = Store(self.db)
        self.store.add_principal("hermes-a", "demo-a", "agent")
        self.keeper = GateKeeper(self.store, FixedClock("2026-11-14T18:40:00Z"))
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.close()
            self.server = None

    def _pre(self, label_ids_config):
        self.server = GateServer(self.sock, self.keeper.bind("hermes-a", self.policy))
        self.server.start()
        time.sleep(0.05)
        ctx = ConfigFakeCtx(socket_path=self.sock, label_ids=label_ids_config)
        self.plugin.register(ctx)
        return next(cb for name, cb in ctx.hooks if name == "pre_tool_call")

    def _last_audit(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT outcome, before_state FROM audit_events WHERE tenant_id='demo-a' "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row

    def _request_action(self, before_state):
        data = json.loads(before_state)
        return data["action"]

    def test_lb01_allow_listed_label(self):
        tool = hermes_mcp.registered_name("gmail", "label_thread")
        pre = self._pre(["Label_1"])
        self.assertIsNone(pre(tool, {"threadId": "t1", "labelIds": ["Label_1"]}))
        row = self._last_audit()
        self.assertEqual(row["outcome"], "allow")
        self.assertEqual(self._request_action(row["before_state"]), "call")

    def test_lb02_unlisted_or_system_ids_blocked(self):
        pre = self._pre(["Label_1"])
        cases = [
            (
                hermes_mcp.registered_name("gmail", "label_message"),
                {"messageId": "m1", "labelIds": ["Label_1", "TRASH"]},
            ),
            (
                hermes_mcp.registered_name("gmail", "label_thread"),
                {"threadId": "t1", "labelIds": ["SPAM"]},
            ),
            (
                hermes_mcp.registered_name("gmail", "unlabel_thread"),
                {"threadId": "t1", "labelIds": ["INBOX"]},
            ),
            (
                hermes_mcp.registered_name("gmail", "unlabel_message"),
                {"messageId": "m1", "labelIds": ["UNREAD"]},
            ),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool):
                self.assertEqual(pre(tool, args), BLOCK_UNKNOWN)
                row = self._last_audit()
                self.assertEqual(self._request_action(row["before_state"]), "label_unlisted")

    def test_lb03_no_config_blocks_labels_allows_read(self):
        pre = self._pre(None)
        for tool, args in LABEL_TOOLS.items():
            with self.subTest(tool=tool):
                self.assertEqual(pre(tool, args), BLOCK_UNKNOWN)
        for tool in READ_DRAFT:
            self.assertIsNone(pre(tool, {"q": "x"}))

    def test_lb04_invalid_arguments(self):
        pre = self._pre(["Label_1"])
        tool = hermes_mcp.registered_name("gmail", "label_thread")
        cases = [
            {"threadId": "t1"},
            {"threadId": "t1", "labelIds": []},
            {"threadId": "t1", "labelIds": [""]},
            {"threadId": "t1", "labelIds": [1]},
            {"threadId": "t1", "labelIds": "Label_1"},
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(pre(tool, args), BLOCK_UNKNOWN)
                row = self._last_audit()
                self.assertEqual(self._request_action(row["before_state"]), "label_invalid")

    def test_lb05_invalid_allowlist_config(self):
        tool = hermes_mcp.registered_name("gmail", "label_thread")
        args = {"threadId": "t1", "labelIds": ["Label_1"]}
        invalid_configs = (
            "Label_1",
            [],
            ["INBOX"],
            ["CHAT"],
            ["Label_1", "STARRED"],
            [1],
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                pre = self._pre(config)
                self.assertEqual(pre(tool, args), BLOCK_UNKNOWN)
                self.assertIsNone(pre(READ_DRAFT[0], {"q": "x"}))

    def test_lb06_build_request_equivalence(self):
        allow = LABEL_1
        cases = [
            (hermes_mcp.registered_name("gmail", "search_threads"), {"query": "a"}, None),
            (hermes_mcp.registered_name("gmail", "label_thread"), {"threadId": "t", "labelIds": ["Label_1"]}, allow),
            (hermes_mcp.registered_name("gmail", "label_thread"), {"threadId": "t", "labelIds": ["SPAM"]}, allow),
            (hermes_mcp.registered_name("gmail", "label_thread"), {"threadId": "t", "labelIds": []}, allow),
            (hermes_mcp.registered_name("gmail", "unlabel_message"), {"messageId": "m", "labelIds": ["Label_1"]}, allow),
            (hermes_mcp.registered_name("gmail", "label_thread"), {"threadId": "t", "labelIds": ["Label_1"]}, None),
            ("mcp_gmail_get_message", {"id": "1", "labelIds": ["INBOX"]}, allow),
            ("mcp_gmail_labelx_message", {"messageId": "m", "labelIds": ["Label_1"]}, allow),
            ("mcp_gmail_label_messages", {"messageId": "m", "labelIds": ["Label_1"]}, allow),
        ]
        for tool, tool_args, label_ids in cases:
            with self.subTest(tool=tool):
                a = self.plugin.build_request(tool, tool_args, label_ids=label_ids)
                b = hermes_adapter.build_request(tool, tool_args, label_ids=label_ids)
                self.assertEqual(a, b)

    def test_lb07_non_label_tools_keep_call_action(self):
        allow = LABEL_1
        cases = [
            (hermes_mcp.registered_name("gmail", "get_message"), {"id": "1", "labelIds": ["INBOX"]}),
            ("mcp_gmail_labelx_message", {"messageId": "m", "labelIds": ["Label_1"]}),
            ("mcp_gmail_label_messages", {"messageId": "m", "labelIds": ["Label_1"]}),
        ]
        for tool, args in cases:
            with self.subTest(tool=tool):
                req = self.plugin.build_request(tool, args, label_ids=allow)
                self.assertEqual(req["action"], "call")


if __name__ == "__main__":
    unittest.main()
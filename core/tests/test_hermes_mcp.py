import tempfile
import unittest
from pathlib import Path

from framework import gate
from framework import hermes_adapter
from framework import hermes_mcp
from framework.clock import FixedClock
from framework.gatekeeper import BUDGET_EXCEEDED, GateKeeper
from framework.store import Store

ALLOWED_LABELS = frozenset(["Label_1"])


def _tool_args(tool):
    if tool.endswith("label_thread") or tool.endswith("unlabel_thread"):
        return {"threadId": "t1", "labelIds": ["Label_1"]}
    if tool.endswith("label_message") or tool.endswith("unlabel_message"):
        return {"messageId": "m1", "labelIds": ["Label_1"]}
    return {"x": 1}


class HermesMcpTests(unittest.TestCase):
    def test_hm01_registered_name(self):
        self.assertEqual(hermes_mcp.registered_name("gmail", "create_draft"), "mcp_gmail_create_draft")
        for server in ("gmail-ro", "Gmail", "", "a" * 33):
            with self.subTest(server=server):
                with self.assertRaises(ValueError):
                    hermes_mcp.registered_name(server, "create_draft")
        for tool in ("create-draft", "query.data", ""):
            with self.subTest(tool=tool):
                with self.assertRaises(ValueError):
                    hermes_mcp.registered_name("gmail", tool)

    def test_hm02_tools_and_classes(self):
        self.assertEqual(set(hermes_mcp.PILOT1_CLASSES), set(hermes_mcp.GMAIL_MCP_TOOLS))
        self.assertEqual(len(hermes_mcp.GMAIL_MCP_TOOLS), 10)
        self.assertEqual(list(hermes_mcp.GMAIL_MCP_TOOLS), sorted(hermes_mcp.GMAIL_MCP_TOOLS))
        for name in hermes_mcp.GMAIL_MCP_TOOLS:
            self.assertNotIn("send", name)

    def test_hm03_pilot1_policy_shape_and_decide(self):
        policy = hermes_mcp.pilot1_policy()
        req = hermes_adapter.build_request(
            hermes_mcp.registered_name("gmail", "search_threads"),
            {"query": "is:unread"},
        )
        out = gate.decide(req, policy)
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(out["action_class"], "read")
        for key in policy["tool_classes"]:
            self.assertFalse(key.endswith(":*"))

    def test_hm04_each_tool_decide(self):
        policy = hermes_mcp.pilot1_policy()
        for tool in hermes_mcp.GMAIL_MCP_TOOLS:
            name = hermes_mcp.registered_name("gmail", tool)
            req = hermes_adapter.build_request(
                name,
                _tool_args(name),
                label_ids=ALLOWED_LABELS if "label" in tool else None,
            )
            out = gate.decide(req, policy)
            expected_class = hermes_mcp.PILOT1_CLASSES[tool]
            self.assertEqual(out["decision"], "allow", msg=tool)
            self.assertEqual(out["action_class"], expected_class, msg=tool)

    def test_hm05_unattended_label_deny(self):
        policy = hermes_mcp.pilot1_policy()
        label_tools = [
            t for t in hermes_mcp.GMAIL_MCP_TOOLS if hermes_mcp.PILOT1_CLASSES[t] == "internal_write"
        ]
        read_tools = [t for t in hermes_mcp.GMAIL_MCP_TOOLS if hermes_mcp.PILOT1_CLASSES[t] == "read"]
        draft_tools = [t for t in hermes_mcp.GMAIL_MCP_TOOLS if hermes_mcp.PILOT1_CLASSES[t] == "draft"]
        for tool in label_tools:
            name = hermes_mcp.registered_name("gmail", tool)
            req = hermes_adapter.build_request(
                name,
                _tool_args(name),
                label_ids=ALLOWED_LABELS,
            )
            out = gate.decide(req, policy, unattended=True)
            self.assertEqual(out["decision"], "deny", msg=tool)
            self.assertEqual(out["reason"], "level_L2", msg=tool)
        for tool in read_tools + draft_tools:
            name = hermes_mcp.registered_name("gmail", tool)
            req = hermes_adapter.build_request(name, {"x": 1})
            out = gate.decide(req, policy, unattended=True)
            self.assertEqual(out["decision"], "allow", msg=tool)

    def test_hm06_unknown_tools_deny(self):
        policy = hermes_mcp.pilot1_policy()
        unknown = [
            "mcp_gmail_send_message",
            "terminal",
            "mcp_gmail_read_resource",
            "mcp_gmailx_get_thread",
        ]
        for tool in unknown:
            req = hermes_adapter.build_request(tool, {"x": 1})
            out = gate.decide(req, policy)
            self.assertEqual(out["decision"], "deny", msg=tool)
            self.assertEqual(out["reason"], "action_class_unknown", msg=tool)
        work_policy = hermes_mcp.pilot1_policy("work")
        req = hermes_adapter.build_request("mcp_gmail_get_thread", {"x": 1})
        out = gate.decide(req, work_policy)
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "action_class_unknown")

    def test_hm08_pilot1_limits_shape(self):
        policy = hermes_mcp.pilot1_policy()
        self.assertEqual(set(policy["limits"]), {"read", "draft", "internal_write"})
        for klass, spec in hermes_mcp.PILOT1_LIMITS.items():
            self.assertEqual(policy["limits"][klass], spec)
        self.assertEqual(set(policy["limits"]), set(policy["levels"]))
        req = hermes_adapter.build_request(
            hermes_mcp.registered_name("gmail", "search_threads"),
            {"query": "is:unread"},
        )
        self.assertEqual(gate.decide(req, policy)["decision"], "allow")
        policy["limits"]["read"]["max_calls"] = 1
        self.assertEqual(hermes_mcp.PILOT1_LIMITS["read"]["max_calls"], 160)
        fresh = hermes_mcp.pilot1_policy()
        self.assertEqual(fresh["limits"]["read"]["max_calls"], 160)

    def test_hm09_pilot1_limits_enforced(self):
        policy = hermes_mcp.pilot1_policy()
        cases = [
            (
                "read",
                "search_threads",
                {"query": "a"},
                None,
                hermes_mcp.PILOT1_LIMITS["read"]["max_calls"],
            ),
            (
                "draft",
                "create_draft",
                {"threadId": "t1"},
                None,
                hermes_mcp.PILOT1_LIMITS["draft"]["max_calls"],
            ),
            (
                "internal_write",
                "label_message",
                {"messageId": "m1", "labelIds": ["Label_1"]},
                ALLOWED_LABELS,
                hermes_mcp.PILOT1_LIMITS["internal_write"]["max_calls"],
            ),
        ]
        for action_class, tool, args, labels, limit in cases:
            with self.subTest(action_class=action_class):
                folder = tempfile.mkdtemp(dir="/tmp")
                store = Store(str(Path(folder) / "gate.sqlite"))
                store.add_principal("hermes-a", "demo-a", "agent")
                keeper = GateKeeper(store, FixedClock("2026-11-22T14:00:00Z"))
                name = hermes_mcp.registered_name("gmail", tool)
                req = hermes_adapter.build_request(
                    name,
                    args,
                    label_ids=labels,
                )
                for _ in range(limit):
                    result = keeper.check("hermes-a", req, policy)
                    self.assertEqual(result["decision"], "allow")
                denied = keeper.check("hermes-a", req, policy)
                self.assertEqual(denied["decision"], "deny")
                self.assertEqual(denied["reason"], BUDGET_EXCEEDED)

    def test_hm07_hermes_server_entry(self):
        entry = hermes_mcp.hermes_server_entry()
        self.assertEqual(set(entry.keys()), {"gmail"})
        gmail = entry["gmail"]
        self.assertEqual(set(gmail.keys()), {"url", "tools"})
        self.assertEqual(gmail["url"], hermes_mcp.GMAIL_MCP_URL)
        self.assertEqual(gmail["tools"], {"include": list(hermes_mcp.GMAIL_MCP_TOOLS)})
        with self.assertRaises(ValueError):
            hermes_mcp.hermes_server_entry("gmail-ro")


if __name__ == "__main__":
    unittest.main()
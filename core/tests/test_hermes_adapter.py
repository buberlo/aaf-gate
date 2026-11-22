import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.audit import verify
from framework.canon import hash_value
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.hermes_adapter import (
    DecisionLedger,
    LEDGER_MAX,
    build_observation,
    build_request,
    make_post_tool_call,
    make_pre_tool_call,
    register_with,
    rule_key,
)
from framework.store import Store

E2E_POLICY = {
    "schema_version": "gate-policy-0.1-draft",
    "tool_classes": {
        "gmail_read:call": "read",
        "gmail_draft:call": "draft",
        "gmail_send:call": "send",
    },
    "levels": {"read": "L4", "draft": "L4", "send": "L3"},
}


class FakeCtx:
    def __init__(self):
        self.hooks = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


class HermesAdapterTests(unittest.TestCase):
    def test_ha01_register_pre_tool_call(self):
        ctx = FakeCtx()
        register_with(ctx, lambda req: {"decision": "allow"})
        self.assertEqual(len(ctx.hooks), 1)
        self.assertEqual(ctx.hooks[0][0], "pre_tool_call")
        cb = ctx.hooks[0][1]
        out = cb(
            "gmail",
            {"x": "1"},
            session_id="s",
            tool_call_id="t",
            conversation_id="c",
        )
        self.assertIsNone(out)

    def test_ha02_allow_passes_request(self):
        captured = []
        cb = make_pre_tool_call(lambda req: (captured.append(req) or {"decision": "allow"}))
        args = {"body": "hello"}
        self.assertIsNone(cb("gmail", args))
        self.assertEqual(len(captured), 1)
        req = captured[0]
        self.assertEqual(req["runtime"], "hermes")
        self.assertEqual(req["action"], "call")
        self.assertEqual(req["target"], "")
        self.assertEqual(req["content_hash"], hash_value(args))

    def test_ha03_needs_approval_approve(self):
        args = {"body": "x"}
        result = {
            "decision": "needs_approval",
            "reason": "approval_required",
            "action_class": "send",
        }
        cb = make_pre_tool_call(lambda req: result)
        out = cb("gmail", args)
        self.assertEqual(out["action"], "approve")
        self.assertIn("send", out["message"])
        self.assertEqual(out["rule_key"], "aaf:gmail:call:" + hash_value(args))

    def test_ha04_rule_key_content_sensitive(self):
        cb = make_pre_tool_call(
            lambda req: {
                "decision": "needs_approval",
                "action_class": "send",
            }
        )
        a = cb("gmail", {"a": "1"})
        b = cb("gmail", {"a": "2"})
        c = cb("gmail", {"a": "1"})
        self.assertNotEqual(a["rule_key"], b["rule_key"])
        self.assertEqual(a["rule_key"], c["rule_key"])

    def test_ha05_deny_unknown_action_class(self):
        cb = make_pre_tool_call(
            lambda req: {"decision": "deny", "reason": "action_class_unknown"}
        )
        out = cb("terminal", {})
        self.assertEqual(out["message"], "Kern: verweigert (action_class_unknown)")

    def test_ha06_check_raises_block(self):
        cb = make_pre_tool_call(lambda req: (_ for _ in ()).throw(RuntimeError("boom")))
        out = cb("gmail", {})
        self.assertEqual(out["message"], "Kern: verweigert (adapter_error)")

    def test_ha07_bad_check_result(self):
        cb = make_pre_tool_call(lambda req: None)
        for value in (None, "allow"):
            with self.subTest(value=value):
                if value is None:
                    out = cb("gmail", {})
                else:
                    out = make_pre_tool_call(lambda req: value)("gmail", {})
                self.assertEqual(out["message"], "Kern: verweigert (adapter_error)")

    def test_ha08_build_errors_no_check(self):
        calls = []
        cb = make_pre_tool_call(lambda req: calls.append(req) or {"decision": "allow"})
        for args in ({"bad": object()}, []):
            with self.subTest(args=args):
                calls.clear()
                out = cb("gmail", args)
                self.assertEqual(out["message"], "Kern: verweigert (invalid_args)")
                self.assertEqual(calls, [])

    def test_ha09_target_keys(self):
        captured = []
        cb = make_pre_tool_call(
            lambda req: (captured.append(req) or {"decision": "allow"}),
            target_keys={"gmail": "to"},
        )
        cb(
            "gmail",
            {"to": "kunde@example.invalid", "body": "x"},
        )
        self.assertEqual(captured[0]["target"], "kunde@example.invalid")
        captured.clear()
        cb2 = make_pre_tool_call(lambda req: (captured.append(req) or {"decision": "allow"}))
        cb2("gmail", {"to": "kunde@example.invalid", "body": "x"})
        self.assertEqual(captured[0]["target"], "")

    def test_ha10_e2e_gatekeeper(self):
        folder = tempfile.mkdtemp()
        path = str(Path(folder) / "e2e.sqlite")
        store = Store(path)
        store.add_principal("hermes-a", "demo-a", "agent")
        keeper = GateKeeper(store, FixedClock("2026-11-14T16:00:00Z"))
        check = lambda req: keeper.check("hermes-a", req, E2E_POLICY)
        cb = make_pre_tool_call(check)
        self.assertIsNone(cb("gmail_read", {"q": "inbox"}))
        self.assertIsNone(cb("gmail_draft", {"body": "text"}))
        approve = cb("gmail_send", {"to": "x@y.invalid"})
        self.assertEqual(approve["action"], "approve")
        block = cb("terminal", {})
        self.assertEqual(block["message"], "Kern: verweigert (action_class_unknown)")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE tenant_id='demo-a'"
        ).fetchone()[0]
        self.assertEqual(count, 4)
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()


class FakeCtx:
    def __init__(self):
        self.hooks = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))


class PostToolCallAdapterTests(unittest.TestCase):
    def test_pt01_ledger_records_pre_outcomes(self):
        args = {"id": "1"}
        expected_hash = hash_value(args)
        checks = [
            lambda req: {"decision": "allow"},
            lambda req: {"decision": "needs_approval", "action_class": "send"},
            lambda req: {"decision": "deny", "reason": "x"},
        ]
        for check in checks:
            with self.subTest(check=check):
                ledger = DecisionLedger()
                with_ledger = make_pre_tool_call(check, ledger=ledger)
                plain = make_pre_tool_call(check)
                out_l = with_ledger("gmail", args, tool_call_id="c1")
                out_p = plain("gmail", args, tool_call_id="c1")
                self.assertEqual(out_l, out_p)
                self.assertEqual(ledger.pop("c1"), expected_hash)
        empty = DecisionLedger()
        make_pre_tool_call(lambda req: {"decision": "allow"}, ledger=empty)("gmail", args)
        self.assertEqual(len(empty), 0)

    def test_pt02_post_matches_pre_silent(self):
        reports = []
        ledger = DecisionLedger()
        args = {"a": "1"}
        pre = make_pre_tool_call(lambda req: {"decision": "allow"}, ledger=ledger)
        post = make_post_tool_call(reports.append, ledger)
        pre("gmail", args, tool_call_id="t1")
        post("gmail", args, tool_call_id="t1")
        self.assertEqual(reports, [])
        self.assertEqual(len(ledger), 0)

    def test_pt03_post_mismatch_reports(self):
        reports = []
        ledger = DecisionLedger()
        args = {"a": "1"}
        pre = make_pre_tool_call(lambda req: {"decision": "allow"}, ledger=ledger)
        post = make_post_tool_call(reports.append, ledger)
        pre("gmail", args, tool_call_id="t2")
        post("gmail", {"a": "2"}, tool_call_id="t2")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["kind"], "gate_mismatch")
        self.assertEqual(reports[0]["expected_hash"], hash_value(args))
        self.assertEqual(reports[0]["observed_hash"], hash_value({"a": "2"}))

    def test_pt04_post_unseen_and_never_raises(self):
        reports = []
        ledger = DecisionLedger()
        post = make_post_tool_call(reports.append, ledger)
        post("gmail", {"x": "1"}, tool_call_id="missing")
        post("gmail", {"x": "2"})
        self.assertEqual(len(reports), 2)
        for item in reports:
            self.assertEqual(item["kind"], "gate_unseen")
            self.assertIsNone(item["expected_hash"])
        noisy = make_post_tool_call(lambda obs: (_ for _ in ()).throw(RuntimeError("x")), ledger)
        self.assertIsNone(noisy("gmail", {"a": 1}, tool_call_id="z"))
        bad_post = make_post_tool_call(reports.append, DecisionLedger())
        self.assertIsNone(bad_post("gmail", {"x": float("nan")}))

    def test_pt05_register_with_shared_ledger(self):
        ctx = FakeCtx()
        register_with(ctx, lambda req: {"decision": "allow"})
        self.assertEqual([name for name, _ in ctx.hooks], ["pre_tool_call"])
        ctx2 = FakeCtx()
        seen = []
        register_with(ctx2, lambda req: {"decision": "allow"}, report=seen.append)
        names = [name for name, _ in ctx2.hooks]
        self.assertEqual(names, ["pre_tool_call", "post_tool_call"])
        pre = ctx2.hooks[0][1]
        post = ctx2.hooks[1][1]
        args = {"n": 0}
        pre("tool", args, tool_call_id="k1")
        post("tool", args, tool_call_id="k1")
        self.assertEqual(seen, [])
        pre("tool", {"n": 1}, tool_call_id="k2")
        post("tool", {"n": 9}, tool_call_id="k2")
        self.assertEqual(len(seen), 1)
        ledger = DecisionLedger()
        for i in range(LEDGER_MAX + 1):
            ledger.record(f"id-{i}", f"sha256:{'0' * 64}")
        self.assertEqual(len(ledger), LEDGER_MAX)
        self.assertIsNone(ledger.pop("id-0"))


if __name__ == "__main__":
    unittest.main()
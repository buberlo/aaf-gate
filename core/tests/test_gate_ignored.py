import contextlib
import io
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from framework import hermes_adapter
from framework.audit import verify
from framework.canon import hash_value
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.gatesocket import GateServer
from framework.baseline import compute
from framework.store import Store

from test_hermes_plugin import ConfigFakeCtx, load_plugin
from test_hermes_adapter import build_observation
from test_pilot_baseline_observations import _with_observations
from test_pilot_baseline_observations import _bl01_bundle

DENY_POLICY = {
    "schema_version": "gate-policy-0.1-draft",
    "tool_classes": {"gmail_read:*": "read"},
    "levels": {"read": "L0"},
}


def _run_pair(module, check, tool_call_id, args, post_kwargs):
    ledger = module.DecisionLedger()
    reports = []
    pre = module.make_pre_tool_call(check, ledger=ledger)
    post = module.make_post_tool_call(reports.append, ledger)
    pre("tool", args, tool_call_id=tool_call_id)
    post("tool", args, tool_call_id=tool_call_id, **post_kwargs)
    return reports


class GateIgnoredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = load_plugin()

    def test_gi01_ledger_blocked_flag(self):
        args = {"k": "v"}
        content_hash = hash_value(args)
        cases = [
            (lambda r: {"decision": "deny", "reason": "x"}, "block"),
            (lambda r: {"decision": "allow"}, "allow"),
            (lambda r: {"decision": "needs_approval", "action_class": "send"}, "approve"),
        ]
        for module in (self.plugin, hermes_adapter):
            for check, decision in cases:
                with self.subTest(module=module.__name__, decision=decision):
                    ledger = module.DecisionLedger()
                    pre = module.make_pre_tool_call(check, ledger=ledger)
                    pre("t", args, tool_call_id="c1")
                    entry = ledger.pop_entry("c1")
                    self.assertEqual(entry, (content_hash, decision))
                    self.assertEqual(ledger.pop("c1"), None)
            ledger = module.DecisionLedger()
            pre = module.make_pre_tool_call(lambda r: (_ for _ in ()).throw(RuntimeError()), ledger=ledger)
            pre("t", args, tool_call_id="e1")
            self.assertEqual(ledger.pop_entry("e1"), (content_hash, "block"))

    def test_gi02_blocked_post_status_blocked_silent(self):
        args = {"a": "1"}
        for module in (self.plugin, hermes_adapter):
            reports = _run_pair(module, lambda r: {"decision": "deny", "reason": "x"}, "c1", args, {"status": "blocked"})
            self.assertEqual(reports, [])

    def test_gi03_blocked_post_status_ok_ignored(self):
        args = {"a": "1"}
        expected = hash_value(args)
        for module in (self.plugin, hermes_adapter):
            reports = _run_pair(module, lambda r: {"decision": "deny", "reason": "x"}, "c1", args, {"status": "ok"})
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["kind"], "gate_ignored")
            self.assertEqual(reports[0]["expected_hash"], expected)
            self.assertEqual(reports[0]["observed_hash"], expected)

    def test_gi04_blocked_ignored_variants(self):
        args = {"a": "1"}
        expected = hash_value(args)
        for module in (self.plugin, hermes_adapter):
            reports = _run_pair(module, lambda r: {"decision": "deny", "reason": "x"}, "c1", args, {"status": "error"})
            self.assertEqual(reports[0]["kind"], "gate_ignored")
            reports = _run_pair(module, lambda r: {"decision": "deny", "reason": "x"}, "c2", args, {})
            self.assertEqual(reports[0]["kind"], "gate_ignored")
            ledger = module.DecisionLedger()
            reports = []
            pre = module.make_pre_tool_call(lambda r: {"decision": "deny", "reason": "x"}, ledger=ledger)
            post = module.make_post_tool_call(reports.append, ledger)
            pre("t", args, tool_call_id="c3")
            post("t", {"a": "2"}, tool_call_id="c3", status="ok")
            self.assertEqual(reports[0]["kind"], "gate_ignored")

    def test_gi05_allow_not_flagged_when_blocked_status(self):
        args = {"a": "1"}
        for module in (self.plugin, hermes_adapter):
            blocked = _run_pair(module, lambda r: {"decision": "allow"}, "c1", args, {"status": "blocked"})
            ok = _run_pair(module, lambda r: {"decision": "allow"}, "c2", args, {"status": "ok"})
            self.assertEqual(blocked, [])
            self.assertEqual(ok, [])

    def test_gi06_plugin_framework_parity(self):
        args = {"n": 1}
        other = {"n": 2}
        plugin_reports = []
        frame_reports = []

        def run_sequence(module, sink):
            ledger = module.DecisionLedger()
            pre_deny = module.make_pre_tool_call(lambda r: {"decision": "deny", "reason": "x"}, ledger=ledger)
            post = module.make_post_tool_call(sink.append, ledger)
            pre_deny("t", args, tool_call_id="d1")
            post("t", args, tool_call_id="d1", status="ok")
            allow_ledger = module.DecisionLedger()
            pre_allow = module.make_pre_tool_call(lambda r: {"decision": "allow"}, ledger=allow_ledger)
            post_allow = module.make_post_tool_call(sink.append, allow_ledger)
            pre_allow("t", args, tool_call_id="a1")
            post_allow("t", other, tool_call_id="a1", status="ok")
            unseen_ledger = module.DecisionLedger()
            post_unseen = module.make_post_tool_call(sink.append, unseen_ledger)
            post_unseen("t", args, tool_call_id="missing", status="ok")

        run_sequence(self.plugin, plugin_reports)
        run_sequence(hermes_adapter, frame_reports)
        self.assertEqual(plugin_reports, frame_reports)

    def test_gi07_gatekeeper_accepts_ignored(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        db = str(Path(folder) / "gate.sqlite")
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, FixedClock("2026-11-14T17:00:00Z"))
        good = build_observation(
            "gmail",
            "gate_ignored",
            "sha256:" + "a" * 64,
            "sha256:" + "a" * 64,
        )
        out = keeper.observe("hermes-a", good)
        self.assertTrue(out["ok"])
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT operation, outcome, error_code FROM audit_events WHERE tenant_id='demo-a'"
        ).fetchone()
        self.assertEqual(row["operation"], "gate_observe")
        self.assertEqual(row["outcome"], "flagged")
        self.assertEqual(row["error_code"], "gate_ignored")
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()
        bad = build_observation("gmail", "gate_ignored", None, "sha256:" + "b" * 64)
        invalid = keeper.observe("hermes-a", bad)
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["reason"], "invalid_observation")

    def test_gi08_summary_and_baseline(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        db = str(Path(folder) / "gate.sqlite")
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, FixedClock("2026-11-14T17:00:00Z"))
        keeper.observe(
            "hermes-a",
            build_observation(
                "gmail",
                "gate_ignored",
                "sha256:" + "c" * 64,
                "sha256:" + "c" * 64,
            ),
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", db, "--tenant", "demo-a", "--summary"])
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertEqual(
            body["summary"]["observations"],
            {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 1, "other": 0},
        )
        summary, timesheet = _bl01_bundle()
        dirty = _with_observations(
            summary,
            {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 1, "other": 0},
        )
        result = compute(dirty, timesheet, 5)
        self.assertFalse(result["observations_clean"])
        legacy = _with_observations(
            summary, {"gate_mismatch": 0, "gate_unseen": 0, "other": 0}
        )
        with self.assertRaises(ValueError) as caught:
            compute(legacy, timesheet, 5)
        self.assertEqual(str(caught.exception), "summary_invalid")

    def test_gi09_socket_e2e_ignored(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        db = str(Path(folder) / "gate.sqlite")
        sock = folder + "/g.sock"
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, FixedClock("2026-11-14T17:00:00Z"))
        server = GateServer(
            sock,
            keeper.bind("hermes-a", DENY_POLICY),
            keeper.bind_observe("hermes-a"),
        )
        server.start()
        time.sleep(0.05)
        try:
            ctx = ConfigFakeCtx(socket_path=sock)
            self.plugin.register(ctx)
            pre = next(cb for name, cb in ctx.hooks if name == "pre_tool_call")
            post = next(cb for name, cb in ctx.hooks if name == "post_tool_call")
            args = {"id": "m1"}
            block = pre("gmail_read", args, tool_call_id="c1")
            self.assertEqual(block["action"], "block")
            post("gmail_read", args, tool_call_id="c1", status="ok")
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT operation, outcome, error_code FROM audit_events WHERE tenant_id='demo-a' ORDER BY sequence"
            ).fetchall()
            conn.close()
            observe = [row for row in rows if row["operation"] == "gate_observe"]
            self.assertEqual(len(observe), 1)
            self.assertEqual(observe[0]["outcome"], "flagged")
            self.assertEqual(observe[0]["error_code"], "gate_ignored")
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
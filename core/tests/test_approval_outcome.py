import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework import hermes_adapter
from framework.baseline import compute
from framework.canon import hash_value
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.store import Store

from test_hermes_plugin import load_plugin
from test_hermes_adapter import build_observation
from test_pilot_baseline_observations import _bl01_bundle, _with_observations


def _run_pair(module, check, tool_call_id, args, post_kwargs):
    ledger = module.DecisionLedger()
    reports = []
    pre = module.make_pre_tool_call(check, ledger=ledger)
    post = module.make_post_tool_call(reports.append, ledger)
    pre("tool", args, tool_call_id=tool_call_id)
    post("tool", post_kwargs.get("_post_args", args), tool_call_id=tool_call_id, **{
        k: v for k, v in post_kwargs.items() if k != "_post_args"
    })
    return reports


NEEDS_APPROVAL = lambda r: {"decision": "needs_approval", "action_class": "send"}


class ApprovalOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = load_plugin()

    def test_ao01_ledger_decisions(self):
        args = {"k": "v"}
        content_hash = hash_value(args)
        cases = [
            (lambda r: {"decision": "deny", "reason": "x"}, "block"),
            (lambda r: {"decision": "allow"}, "allow"),
            (NEEDS_APPROVAL, "approve"),
        ]
        for module in (self.plugin, hermes_adapter):
            for check, decision in cases:
                with self.subTest(module=module.__name__, decision=decision):
                    ledger = module.DecisionLedger()
                    pre = module.make_pre_tool_call(check, ledger=ledger)
                    pre("t", args, tool_call_id="c1")
                    self.assertEqual(ledger.pop_entry("c1"), (content_hash, decision))
            ledger = module.DecisionLedger()
            ledger.record("x", content_hash, "xyzzy")
            self.assertEqual(ledger.pop_entry("x"), (content_hash, "block"))

    def test_ao02_approval_denied(self):
        args = {"a": "1"}
        h = hash_value(args)
        for module in (self.plugin, hermes_adapter):
            reports = _run_pair(
                module, NEEDS_APPROVAL, "c1", args, {"status": "blocked"}
            )
            with self.subTest(module=module.__name__):
                self.assertEqual(len(reports), 1)
                self.assertEqual(reports[0]["kind"], "approval_denied")
                self.assertEqual(reports[0]["expected_hash"], h)
                self.assertEqual(reports[0]["observed_hash"], h)

    def test_ao03_approval_granted_variants(self):
        args = {"a": "1"}
        for module in (self.plugin, hermes_adapter):
            for status in ("ok", "error", None):
                with self.subTest(module=module.__name__, status=status):
                    kw = {} if status is None else {"status": status}
                    reports = _run_pair(module, NEEDS_APPROVAL, "c1", args, kw)
                    self.assertEqual(len(reports), 1)
                    self.assertEqual(reports[0]["kind"], "approval_granted")

    def test_ao04_approve_mismatch_not_approval(self):
        args = {"a": "1"}
        for module in (self.plugin, hermes_adapter):
            reports = _run_pair(
                module,
                NEEDS_APPROVAL,
                "c1",
                args,
                {"_post_args": {"a": "2"}, "status": "ok"},
            )
            with self.subTest(module=module.__name__):
                self.assertEqual(len(reports), 1)
                self.assertEqual(reports[0]["kind"], "gate_mismatch")

    def test_ao05_allow_and_block_status_blocked_silent(self):
        args = {"a": "1"}
        for module in (self.plugin, hermes_adapter):
            allow = _run_pair(
                module,
                lambda r: {"decision": "allow"},
                "a1",
                args,
                {"status": "blocked"},
            )
            blocked = _run_pair(
                module,
                lambda r: {"decision": "deny", "reason": "x"},
                "b1",
                args,
                {"status": "blocked"},
            )
            with self.subTest(module=module.__name__):
                self.assertEqual(allow, [])
                self.assertEqual(blocked, [])

    def test_ao06_gatekeeper_approval_validation(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        db = str(Path(folder) / "gate.sqlite")
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, FixedClock("2026-11-14T17:00:00Z"))
        h = "sha256:" + "f" * 64
        for kind in ("approval_granted", "approval_denied"):
            out = keeper.observe("hermes-a", build_observation("gmail", kind, h, h))
            self.assertTrue(out["ok"])
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT error_code FROM audit_events WHERE error_code=?",
                (kind,),
            ).fetchone()
            conn.close()
            self.assertEqual(row["error_code"], kind)
        bad_cases = [
            build_observation("gmail", "approval_granted", None, h),
            build_observation("gmail", "approval_denied", h, "sha256:" + "e" * 64),
        ]
        for obs in bad_cases:
            invalid = keeper.observe("hermes-a", obs)
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["reason"], "invalid_observation")

    def test_ao07_summary_approvals_vs_observations(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        db = str(Path(folder) / "gate.sqlite")
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, FixedClock("2026-11-14T17:00:00Z"))
        h = "sha256:" + "a" * 64
        keeper.observe(
            "hermes-a",
            build_observation("gmail", "approval_granted", h, h),
        )
        keeper.observe(
            "hermes-a",
            build_observation("gmail", "approval_denied", h, h),
        )
        keeper.observe(
            "hermes-a",
            build_observation("gmail", "gate_mismatch", h, "sha256:" + "b" * 64),
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", db, "--tenant", "demo-a", "--summary"])
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertEqual(body["summary"]["approvals"], {"granted": 1, "denied": 1})
        self.assertEqual(body["summary"]["observations"]["gate_mismatch"], 1)
        self.assertEqual(body["summary"]["observations"]["other"], 0)

    def test_ao08_baseline_approvals(self):
        summary, timesheet = _bl01_bundle()
        good = dict(summary)
        inner = dict(good["summary"])
        inner["approvals"] = {"granted": 2, "denied": 1}
        good["summary"] = inner
        result = compute(good, timesheet, 5)
        self.assertEqual(result["approvals"], {"granted": 2, "denied": 1})
        base = compute(
            _with_observations(
                summary,
                {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0},
            ),
            timesheet,
            5,
        )
        self.assertIsNone(base["approvals"])
        self.assertTrue(base["observations_clean"])
        invalid_shapes = [
            {"granted": 0},
            {"granted": 0, "denied": 0, "extra": 0},
            {"granted": -1, "denied": 0},
            {"granted": True, "denied": 0},
        ]
        for broken in invalid_shapes:
            bad = dict(summary)
            inner = dict(bad["summary"])
            inner["approvals"] = broken
            bad["summary"] = inner
            with self.assertRaises(ValueError) as caught:
                compute(bad, timesheet, 5)
            self.assertEqual(str(caught.exception), "summary_invalid")


if __name__ == "__main__":
    unittest.main()
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from framework.baseline import compute
from framework.cli import init_gate_db, main
from framework.clock import Clock
from framework.gatekeeper import GateKeeper
from framework import hermes_adapter, hermes_mcp
from framework.store import Store


def _verified_summary(by_class, **extra):
    return {
        "ok": True,
        "since": extra.get("since"),
        "summary": {"by_class": by_class, "by_tool": {}, "total": sum(sum(v.values()) for v in by_class.values())},
        "tenant": "demo-a",
        "until": extra.get("until"),
        "verify": {"count": 0, "first_break": None, "head": "sha256:" + "0" * 64, "head_mismatch": False, "ok": True},
    }


def _timesheet(samples):
    return {"schema_version": 1, "unit": "minutes", "samples": samples}


def _keys_contain_forbidden(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower = key.lower()
            if "eur" in lower or "cost" in lower:
                return True
            if _keys_contain_forbidden(value):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _keys_contain_forbidden(item):
                return True
    return False


class PilotBaselineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")

    def test_bl01_measured_and_unmeasured(self):
        summary = _verified_summary(
            {
                "draft": {"allow": 4},
                "read": {"allow": 1},
            }
        )
        timesheet = _timesheet(
            {
                "draft": [6, 8, 10, 12, 14],
                "internal_write": [],
                "read": [],
            }
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["median_minutes"], 10)
        self.assertEqual(result["classes"]["draft"]["manual_minutes"], 40)
        self.assertEqual(result["classes"]["draft"]["basis"], "gemessen")
        self.assertEqual(result["classes"]["read"]["basis"], "nicht_gemessen")
        self.assertIsNone(result["classes"]["read"]["manual_minutes"])
        self.assertEqual(result["manual_minutes_total"], 40)

    def test_bl02_not_executed_and_excluded(self):
        summary = _verified_summary(
            {
                "draft": {"allow": 2, "deny": 1, "needs_approval": 1},
                "action_class_unknown": {"deny": 3},
                "no_context": {"allow": 1},
            }
        )
        timesheet = _timesheet({"draft": [5, 6, 7, 8, 9], "internal_write": [], "read": []})
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["manual_minutes"], 14)
        self.assertEqual(result["classes"]["draft"]["not_executed"], {"deny": 1, "needs_approval": 1})
        self.assertEqual(result["excluded"], {"deny": 3, "allow": 1})

    def test_bl03_too_few_samples(self):
        summary = _verified_summary({"draft": {"allow": 1}})
        timesheet = _timesheet({"draft": [4, 6, 8], "internal_write": [], "read": []})
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["basis"], "zu_wenig_messungen")
        self.assertIsNone(result["classes"]["draft"]["manual_minutes"])
        self.assertEqual(result["classes"]["draft"]["median_minutes"], 6)

    def test_bl04_summary_not_verified(self):
        summary = _verified_summary({"draft": {"allow": 1}})
        summary["ok"] = False
        with self.assertRaises(ValueError) as caught:
            compute(summary, _timesheet({"draft": [5], "internal_write": [], "read": []}), 5)
        self.assertEqual(str(caught.exception), "summary_not_verified")
        summary["ok"] = True
        summary["verify"]["ok"] = False
        with self.assertRaises(ValueError) as caught:
            compute(summary, _timesheet({"draft": [5], "internal_write": [], "read": []}), 5)
        self.assertEqual(str(caught.exception), "summary_not_verified")

    def test_bl05_timesheet_invalid(self):
        base = _timesheet({"draft": [5, 6, 7, 8, 9], "internal_write": [5, 6, 7, 8, 9], "read": [5, 6, 7, 8, 9]})
        summary = _verified_summary({"draft": {"allow": 1}})
        cases = [
            {"draft": [5], "internal_write": [5], "read": [5], "extra": []},
            {"draft": [0], "internal_write": [5], "read": [5]},
            {"draft": [481], "internal_write": [5], "read": [5]},
            {"draft": [True], "internal_write": [5], "read": [5]},
            {"draft": [5.5], "internal_write": [5], "read": [5]},
            {"draft": [5], "internal_write": [5], "read": [5], "unknown": [5]},
        ]
        for broken_samples in cases:
            sheet = {"schema_version": 1, "unit": "minutes", "samples": broken_samples}
            with self.assertRaises(ValueError) as caught:
                compute(summary, sheet, 5)
            self.assertEqual(str(caught.exception), "timesheet_invalid")
        hours = dict(base)
        hours["unit"] = "hours"
        with self.assertRaises(ValueError):
            compute(summary, hours, 5)
        folder = self.folder
        summary_path = str(Path(folder) / "summary.json")
        ts_path = str(Path(folder) / "ts.json")
        Path(summary_path).write_text(json.dumps(_verified_summary({"draft": {"allow": 1}})), encoding="utf-8")
        Path(ts_path).write_text('{"schema_version":1,"unit":"minutes","samples":{"draft":[5],"draft":[6],"internal_write":[5],"read":[5]}}', encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["pilot-baseline", "--summary", summary_path, "--timesheet", ts_path])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buf.getvalue().splitlines()[0])["error"], "timesheet_invalid")

    def test_bl06_cli_end_to_end(self):
        db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, Clock())
        policy = hermes_mcp.pilot1_policy()
        tool = hermes_mcp.registered_name("gmail", "create_draft")
        for _ in range(2):
            keeper.check("hermes-a", hermes_adapter.build_request(tool, {"body": "x"}), policy)
        summary_path = str(Path(self.folder) / "summary.json")
        ts_path = str(Path(self.folder) / "timesheet.json")
        audit_buf = io.StringIO()
        with contextlib.redirect_stdout(audit_buf):
            code = main(["gate-audit", "--db", db, "--tenant", "demo-a", "--summary"])
        self.assertEqual(code, 0)
        Path(summary_path).write_text(audit_buf.getvalue().strip(), encoding="utf-8")
        Path(ts_path).write_text(
            json.dumps(_timesheet({"draft": [4, 6, 8, 10, 12], "internal_write": [], "read": []})),
            encoding="utf-8",
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                [
                    "pilot-baseline",
                    "--summary",
                    summary_path,
                    "--timesheet",
                    ts_path,
                    "--min-samples",
                    "5",
                ]
            )
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertTrue(body["ok"])
        self.assertEqual(body["baseline"]["classes"]["draft"]["allow"], 2)
        rel = Path(self.folder) / "rel.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["pilot-baseline", "--summary", "rel.json", "--timesheet", ts_path]), 2)
        link = Path(self.folder) / "link.json"
        os.symlink(summary_path, link)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["pilot-baseline", "--summary", str(link), "--timesheet", ts_path]), 2)

    def test_bl07_no_cost_keys(self):
        summary = _verified_summary({"draft": {"allow": 2, "deny": 1}})
        timesheet = _timesheet({"draft": [5, 6, 7, 8, 9], "internal_write": [], "read": []})
        result = compute(summary, timesheet, 5)
        self.assertFalse(_keys_contain_forbidden(result))


if __name__ == "__main__":
    unittest.main()
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from framework.baseline import compute
from framework.cli import init_gate_db, main
from framework.clock import Clock
from framework.gatekeeper import GateKeeper
from framework import hermes_adapter, hermes_mcp
from framework.store import Store

from test_hermes_adapter import build_observation
from test_pilot_baseline import _timesheet, _verified_summary


def _with_observations(summary, observations):
    out = copy.deepcopy(summary)
    inner = dict(out["summary"])
    inner["observations"] = observations
    out["summary"] = inner
    return out


def _bl01_bundle():
    by_class = {"draft": {"allow": 4}, "read": {"allow": 1}}
    summary = _verified_summary(by_class)
    timesheet = _timesheet(
        {"draft": [6, 8, 10, 12, 14], "internal_write": [], "read": []}
    )
    return summary, timesheet


def _baseline_without_observations(result):
    trimmed = dict(result)
    trimmed.pop("observations", None)
    trimmed.pop("observations_clean", None)
    return trimmed


class PilotBaselineObservationsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")

    def test_po01_clean_observations_passthrough(self):
        summary, timesheet = _bl01_bundle()
        base = compute(summary, timesheet, 5)
        observed = _with_observations(
            summary, {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0}
        )
        result = compute(observed, timesheet, 5)
        self.assertTrue(result["observations_clean"])
        self.assertEqual(
            result["observations"],
            {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0},
        )
        self.assertEqual(
            _baseline_without_observations(result),
            _baseline_without_observations(base),
        )

    def test_po02_gate_unseen_does_not_change_totals(self):
        summary, timesheet = _bl01_bundle()
        clean = compute(
            _with_observations(summary, {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0}),
            timesheet,
            5,
        )
        dirty = compute(
            _with_observations(
                summary,
                {"gate_mismatch": 0, "gate_unseen": 1, "gate_ignored": 0, "other": 0},
            ),
            timesheet,
            5,
        )
        self.assertFalse(dirty["observations_clean"])
        self.assertEqual(dirty["manual_minutes_total"], clean["manual_minutes_total"])
        self.assertEqual(dirty["net_minutes_total"], clean["net_minutes_total"])
        self.assertEqual(
            _baseline_without_observations(dirty),
            _baseline_without_observations(clean),
        )

    def test_po03_mismatch_and_other_rules(self):
        summary, timesheet = _bl01_bundle()
        mismatch = compute(
            _with_observations(
                summary,
                {"gate_mismatch": 2, "gate_unseen": 0, "gate_ignored": 0, "other": 0},
            ),
            timesheet,
            5,
        )
        self.assertFalse(mismatch["observations_clean"])
        other_only = compute(
            _with_observations(
                summary,
                {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 3},
            ),
            timesheet,
            5,
        )
        self.assertTrue(other_only["observations_clean"])

    def test_po04_legacy_summary_without_observations(self):
        summary, timesheet = _bl01_bundle()
        result = compute(summary, timesheet, 5)
        self.assertIsNone(result["observations"])
        self.assertIsNone(result["observations_clean"])

    def test_po05_summary_invalid_shapes(self):
        summary, timesheet = _bl01_bundle()
        valid = {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0}
        cases = [
            [],
            {"gate_mismatch": 0, "gate_unseen": 0},
            {**valid, "extra": 0},
            {**valid, "gate_mismatch": -1},
            {**valid, "gate_unseen": True},
            {**valid, "other": 1.0},
        ]
        for broken in cases:
            observed = _with_observations(summary, broken)
            with self.assertRaises(ValueError) as caught:
                compute(observed, timesheet, 5)
            self.assertEqual(str(caught.exception), "summary_invalid")
            summary_path = str(Path(self.folder) / "summary.json")
            ts_path = str(Path(self.folder) / "timesheet.json")
            Path(summary_path).write_text(json.dumps(observed), encoding="utf-8")
            Path(ts_path).write_text(json.dumps(timesheet), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(
                    ["pilot-baseline", "--summary", summary_path, "--timesheet", ts_path]
                )
            self.assertEqual(code, 2)
            self.assertEqual(
                json.loads(buf.getvalue().splitlines()[0])["error"],
                "summary_invalid",
            )

    def test_po06_roundtrip_gate_unseen(self):
        db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(db, "hermes-a", "demo-a", out=io.StringIO())
        store = Store(db)
        keeper = GateKeeper(store, Clock())
        policy = hermes_mcp.pilot1_policy()
        tool = hermes_mcp.registered_name("gmail", "search_threads")
        keeper.check("hermes-a", hermes_adapter.build_request(tool, {"query": "q"}), policy)
        keeper.observe(
            "hermes-a",
            build_observation("gmail", "gate_unseen", None, "sha256:" + "d" * 64),
        )
        audit_buf = io.StringIO()
        with contextlib.redirect_stdout(audit_buf):
            code = main(["gate-audit", "--db", db, "--tenant", "demo-a", "--summary"])
        self.assertEqual(code, 0)
        summary_path = str(Path(self.folder) / "summary.json")
        ts_path = str(Path(self.folder) / "timesheet.json")
        Path(summary_path).write_text(audit_buf.getvalue().strip(), encoding="utf-8")
        Path(ts_path).write_text(
            json.dumps(_timesheet({"draft": [5, 6, 7, 8, 9], "internal_write": [], "read": []})),
            encoding="utf-8",
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                ["pilot-baseline", "--summary", summary_path, "--timesheet", ts_path]
            )
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertTrue(body["ok"])
        self.assertFalse(body["baseline"]["observations_clean"])


    def test_po07_require_clean_rejects_dirty(self):
        summary, timesheet = _bl01_bundle()
        observed = _with_observations(
            summary,
            {"gate_mismatch": 0, "gate_unseen": 1, "gate_ignored": 0, "other": 0},
        )
        summary_path = str(Path(self.folder) / "summary.json")
        ts_path = str(Path(self.folder) / "timesheet.json")
        Path(summary_path).write_text(json.dumps(observed), encoding="utf-8")
        Path(ts_path).write_text(json.dumps(timesheet), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                [
                    "pilot-baseline",
                    "--summary",
                    summary_path,
                    "--timesheet",
                    ts_path,
                    "--require-clean",
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            json.loads(buf.getvalue().splitlines()[0])["error"],
            "observations_not_clean",
        )

    def test_po08_require_clean_accepts_clean(self):
        summary, timesheet = _bl01_bundle()
        observed = _with_observations(
            summary, {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0}
        )
        summary_path = str(Path(self.folder) / "summary.json")
        ts_path = str(Path(self.folder) / "timesheet.json")
        Path(summary_path).write_text(json.dumps(observed), encoding="utf-8")
        Path(ts_path).write_text(json.dumps(timesheet), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                [
                    "pilot-baseline",
                    "--summary",
                    summary_path,
                    "--timesheet",
                    ts_path,
                    "--require-clean",
                ]
            )
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertTrue(body["ok"])
        self.assertTrue(body["baseline"]["observations_clean"])


if __name__ == "__main__":
    unittest.main()
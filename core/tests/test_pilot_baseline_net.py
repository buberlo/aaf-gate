import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from framework.baseline import compute
from framework.cli import main

from test_pilot_baseline import _verified_summary, _timesheet


def _timesheet_v2(samples, review_samples):
    return {
        "schema_version": 2,
        "unit": "minutes",
        "samples": samples,
        "review_samples": review_samples,
    }


class PilotBaselineNetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")

    def test_nt01_net_measured(self):
        summary = _verified_summary(
            {
                "draft": {"allow": 4},
                "read": {"allow": 0},
                "internal_write": {"allow": 0},
            }
        )
        timesheet = _timesheet_v2(
            {
                "draft": [6, 8, 10, 12, 14],
                "internal_write": [],
                "read": [],
            },
            {
                "draft": [2, 2, 3, 3, 4],
                "internal_write": [],
                "read": [],
            },
        )
        result = compute(summary, timesheet, 5)
        draft = result["classes"]["draft"]
        self.assertEqual(draft["median_minutes"], 10)
        self.assertEqual(draft["median_review_minutes"], 3)
        self.assertEqual(draft["net_minutes"], 28)
        self.assertEqual(draft["net_basis"], "gemessen")
        self.assertEqual(result["net_incomplete"], [])
        self.assertEqual(result["net_minutes_total"], 28)

    def test_nt02_negative_net(self):
        summary = _verified_summary({"draft": {"allow": 2}})
        timesheet = _timesheet_v2(
            {"draft": [5, 6, 7, 8, 9], "internal_write": [], "read": []},
            {"draft": [20, 20, 20, 20, 20], "internal_write": [], "read": []},
        )
        result = compute(summary, timesheet, 5)
        self.assertLess(result["classes"]["draft"]["net_minutes"], 0)

    def test_nt03_net_incomplete(self):
        summary = _verified_summary({"draft": {"allow": 2}, "read": {"allow": 3}})
        timesheet = _timesheet_v2(
            {"draft": [4, 6, 8, 10, 12], "internal_write": [], "read": [4, 6, 8, 10, 12]},
            {"draft": [1, 1, 2, 2, 3], "internal_write": [], "read": []},
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["net_incomplete"], ["read"])
        self.assertIsNone(result["net_minutes_total"])

    def test_nt04_schema_v1_unchanged_except_net(self):
        summary = _verified_summary({"draft": {"allow": 4}, "read": {"allow": 1}})
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
        self.assertEqual(result["manual_minutes_total"], 40)
        for key in ("draft", "internal_write", "read"):
            self.assertEqual(result["classes"][key]["net_basis"], "pruefzeit_nicht_gemessen")
            self.assertIsNone(result["classes"][key]["net_minutes"])
        self.assertIsNone(result["net_minutes_total"])

    def test_nt05_timesheet_v2_invalid(self):
        summary = _verified_summary({"draft": {"allow": 1}})
        base_samples = {"draft": [5, 6, 7, 8, 9], "internal_write": [5, 6, 7, 8, 9], "read": [5, 6, 7, 8, 9]}
        base_review = {"draft": [1, 2, 3, 4, 5], "internal_write": [1, 2, 3, 4, 5], "read": [1, 2, 3, 4, 5]}
        cases = [
            _timesheet_v2(base_samples, {**base_review, "draft": [-1, 2, 3, 4, 5]}),
            _timesheet_v2(base_samples, {**base_review, "draft": [481, 2, 3, 4, 5]}),
            _timesheet_v2(base_samples, {**base_review, "draft": [True, 2, 3, 4, 5]}),
            _timesheet_v2(base_samples, {"draft": [1, 2, 3, 4, 5], "read": [1, 2, 3, 4, 5]}),
            {
                "schema_version": 1,
                "unit": "minutes",
                "samples": base_samples,
                "review_samples": base_review,
            },
            {"schema_version": 2, "unit": "minutes", "samples": base_samples},
        ]
        for sheet in cases:
            with self.assertRaises(ValueError) as caught:
                compute(summary, sheet, 5)
            self.assertEqual(str(caught.exception), "timesheet_invalid")

    def test_nt06_bool_allow_summary_invalid(self):
        summary = _verified_summary({"draft": {"allow": True}})
        timesheet = _timesheet({"draft": [5, 6, 7, 8, 9], "internal_write": [5, 6, 7, 8, 9], "read": [5, 6, 7, 8, 9]})
        with self.assertRaises(ValueError) as caught:
            compute(summary, timesheet, 5)
        self.assertEqual(str(caught.exception), "summary_invalid")
        folder = Path(self.folder)
        summary_path = str(folder / "summary.json")
        ts_path = str(folder / "timesheet.json")
        bad_summary = _verified_summary({"draft": {"allow": True}})
        Path(summary_path).write_text(json.dumps(bad_summary), encoding="utf-8")
        Path(ts_path).write_text(json.dumps(timesheet), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["pilot-baseline", "--summary", summary_path, "--timesheet", ts_path])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buf.getvalue().splitlines()[0])["error"], "summary_invalid")


if __name__ == "__main__":
    unittest.main()
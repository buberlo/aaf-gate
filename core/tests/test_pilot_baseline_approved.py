import unittest

from framework.baseline import compute

from test_pilot_baseline import _timesheet, _verified_summary


def _with_approvals(summary, approvals_by_class, unlinked=None):
    inner = dict(summary["summary"])
    granted = sum(bucket.get("granted", 0) for bucket in approvals_by_class.values())
    denied = sum(bucket.get("denied", 0) for bucket in approvals_by_class.values())
    inner["approvals"] = {"granted": granted, "denied": denied}
    inner["approvals_by_class"] = dict(approvals_by_class)
    if unlinked is not None:
        inner["approvals_by_class"]["unlinked"] = unlinked
        inner["approvals"]["granted"] += unlinked.get("granted", 0)
        inner["approvals"]["denied"] += unlinked.get("denied", 0)
    out = dict(summary)
    out["summary"] = inner
    return out


class PilotBaselineApprovedTests(unittest.TestCase):
    def test_pa01_without_approvals_by_class(self):
        summary = _verified_summary({"draft": {"allow": 4}, "read": {"allow": 1}})
        timesheet = _timesheet(
            {
                "draft": [6, 8, 10, 12, 14],
                "internal_write": [],
                "read": [],
            }
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["manual_minutes"], 40)
        self.assertEqual(result["manual_minutes_total"], 40)
        for key in ("draft", "internal_write", "read"):
            self.assertIsNone(result["classes"][key]["approved_executed"])
            self.assertIsNone(result["classes"][key]["manual_minutes_approved"])
            self.assertIsNone(result["classes"][key]["net_minutes_approved"])
        self.assertIsNone(result["approved_incomplete"])
        self.assertIsNone(result["manual_minutes_approved_total"])
        self.assertIsNone(result["net_minutes_approved_total"])

    def test_pa02_measured_approved_totals(self):
        summary = _with_approvals(
            _verified_summary({"draft": {"allow": 10}}),
            {"draft": {"granted": 4, "denied": 0}},
        )
        timesheet = {
            "schema_version": 2,
            "unit": "minutes",
            "samples": {
                "draft": [10, 10, 10, 10, 10],
                "internal_write": [],
                "read": [],
            },
            "review_samples": {
                "draft": [2, 2, 2, 2, 2],
                "internal_write": [],
                "read": [],
            },
        }
        result = compute(summary, timesheet, 5)
        draft = result["classes"]["draft"]
        self.assertEqual(draft["approved_executed"], 4)
        self.assertEqual(draft["manual_minutes_approved"], 40)
        self.assertEqual(draft["net_minutes_approved"], 32)
        self.assertEqual(draft["manual_minutes"], 100)
        self.assertEqual(result["manual_minutes_total"], 100)
        self.assertEqual(result["manual_minutes_approved_total"], 40)
        self.assertEqual(result["net_minutes_approved_total"], 32)
        self.assertEqual(result["approved_incomplete"], [])

    def test_pa03_few_samples_incomplete(self):
        summary = _with_approvals(
            _verified_summary({"draft": {"allow": 2}}),
            {"draft": {"granted": 1, "denied": 0}},
        )
        timesheet = _timesheet(
            {
                "draft": [10, 12],
                "internal_write": [],
                "read": [],
            }
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["approved_executed"], 1)
        self.assertIsNone(result["classes"]["draft"]["manual_minutes_approved"])
        self.assertEqual(result["approved_incomplete"], ["draft"])
        self.assertIsNone(result["manual_minutes_approved_total"])
        self.assertIsNone(result["net_minutes_approved_total"])

    def test_pa04_no_review_net_incomplete(self):
        summary = _with_approvals(
            _verified_summary({"draft": {"allow": 2}}),
            {"draft": {"granted": 2, "denied": 0}},
        )
        timesheet = _timesheet(
            {
                "draft": [10, 10, 10, 10, 10],
                "internal_write": [],
                "read": [],
            }
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["manual_minutes_approved"], 20)
        self.assertIsNone(result["classes"]["draft"]["net_minutes_approved"])
        self.assertEqual(result["approved_incomplete"], ["draft"])
        self.assertIsNone(result["manual_minutes_approved_total"])
        self.assertIsNone(result["net_minutes_approved_total"])

    def test_pa05_unlinked_blocks_totals(self):
        summary = _with_approvals(
            _verified_summary({"draft": {"allow": 2}}),
            {"draft": {"granted": 2, "denied": 0}},
            unlinked={"granted": 1, "denied": 0},
        )
        timesheet = _timesheet(
            {
                "draft": [10, 10, 10, 10, 10],
                "internal_write": [],
                "read": [],
            }
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["manual_minutes_approved"], 20)
        self.assertEqual(result["approvals_unlinked"], 1)
        self.assertIsNone(result["manual_minutes_approved_total"])
        self.assertIsNone(result["net_minutes_approved_total"])

    def test_pa06_all_granted_zero(self):
        summary = _with_approvals(
            _verified_summary({"draft": {"allow": 2}}),
            {"draft": {"granted": 0, "denied": 1}},
        )
        timesheet = _timesheet(
            {
                "draft": [10, 10, 10, 10, 10],
                "internal_write": [],
                "read": [],
            }
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["approved_executed"], 0)
        self.assertEqual(result["approved_incomplete"], [])
        self.assertEqual(result["manual_minutes_approved_total"], 0)
        self.assertEqual(result["net_minutes_approved_total"], 0)

    def test_pa07_denied_not_in_minutes(self):
        summary = _with_approvals(
            _verified_summary({"draft": {"allow": 5}}),
            {"draft": {"granted": 0, "denied": 3}},
        )
        timesheet = _timesheet(
            {
                "draft": [10, 10, 10, 10, 10],
                "internal_write": [],
                "read": [],
            }
        )
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["draft"]["approved_executed"], 0)
        self.assertEqual(result["manual_minutes_approved_total"], 0)
        self.assertEqual(result["net_minutes_approved_total"], 0)


if __name__ == "__main__":
    unittest.main()
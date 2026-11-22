import unittest

from framework.errors import CommandError
from framework.releases import diff

from test_run_release import CONFIG_ID, RunReleaseLab, initial_values


class ReleaseDiffTests(unittest.TestCase):
    def test_rd01_fresh_run_not_stale(self):
        lab = RunReleaseLab()
        run = lab.call(
            "create_run",
            payload={"rules_version": "material-prep-1", "config_id": CONFIG_ID},
            expected_state_version=0,
        )
        result = lab.call("release_diff", run)
        self.assertTrue(result["bound"])
        self.assertFalse(result["stale"])
        self.assertEqual(result["changed"], [])
        self.assertEqual(result["run_release_hash"], result["active_release_hash"])

    def test_rd02_customer_edit_shows_editable_value(self):
        lab = RunReleaseLab()
        run = lab.to_review()
        lab.plane.apply_changes("kunde", CONFIG_ID, {"opening_hours": "Mo-Fr 9-18"}, "hours")
        result = lab.call("release_diff", run)
        self.assertTrue(result["stale"])
        self.assertEqual(result["changed"], ["opening_hours"])
        self.assertEqual(result["values"]["opening_hours"]["from"], "Mo-Fr 8-17")
        self.assertEqual(result["values"]["opening_hours"]["to"], "Mo-Fr 9-18")
        self.assertFalse(result["template_changed"])
        self.assertFalse(result["floor_changed"])

    def test_rd03_locked_field_name_only(self):
        lab = RunReleaseLab()
        run = lab.to_review()
        values = dict(initial_values())
        values["followup_questions"] = "Neue Leitfragen"
        lab.plane.release("konrad", CONFIG_ID, "kfz-terminannahme", "1.1.0", values, "rel-lock")
        result = lab.call("release_diff", run)
        self.assertIn("followup_questions", result["changed"])
        self.assertNotIn("followup_questions", result["values"])

    def test_rd04_rollback_clears_stale(self):
        lab = RunReleaseLab()
        run = lab.to_review()
        lab.plane.apply_changes("kunde", CONFIG_ID, {"opening_hours": "Mo-Fr 9-18"}, "hours")
        lab.plane.rollback("konrad", CONFIG_ID, 1, "back")
        result = lab.call("release_diff", run)
        self.assertFalse(result["stale"])
        self.assertEqual(result["changed"], [])

    def test_rd05_unbound_run(self):
        lab = RunReleaseLab()
        run = lab.call("create_run", payload={"rules_version": "material-prep-1"}, expected_state_version=0)
        result = lab.call("release_diff", run)
        self.assertEqual(result, {"bound": False})

    def test_rd06_access_and_input_guards(self):
        lab = RunReleaseLab()
        run = lab.to_review()
        with self.assertRaises(CommandError) as ctx:
            lab.call("release_diff", run, actor="bea")
        self.assertEqual(ctx.exception.code, "NOT_FOUND")
        with self.assertRaises(CommandError) as ctx:
            lab.call("release_diff", run, payload={"x": 1})
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        with self.assertRaises(CommandError) as ctx:
            lab.call("release_diff", run, actor="konrad")
        self.assertEqual(ctx.exception.code, "FORBIDDEN")

    def test_rd07_diff_function_edges(self):
        old = {"values": {"opening_hours": "Mo-Fr 8-17"}, "field_access": {"opening_hours": "customer_editable"}}
        new = {
            "values": {"opening_hours": "Mo-Fr 8-17", "shop_name": "Neu"},
            "field_access": {"opening_hours": "customer_editable", "shop_name": "customer_editable"},
        }
        result = diff(old, new)
        self.assertEqual(result["changed"], ["shop_name"])
        self.assertIsNone(result["values"]["shop_name"]["from"])
        self.assertEqual(result["values"]["shop_name"]["to"], "Neu")
        without_floor = {"values": {}}
        with_floor = {"values": {}, "core_floor_hash": "sha256:ab"}
        self.assertTrue(diff(without_floor, with_floor)["floor_changed"])
        locked_old = {"values": {"followup_questions": "Alt"}, "field_access": {"followup_questions": "locked"}}
        locked_new = {"values": {"followup_questions": "Neu"}, "field_access": {"followup_questions": "locked"}}
        locked = diff(locked_old, locked_new)
        self.assertEqual(locked["changed"], ["followup_questions"])
        self.assertEqual(locked["values"], {})


if __name__ == "__main__":
    unittest.main()
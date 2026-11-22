import json
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.canon import FIELD_NAME, TEMPLATE_ID, render_value
from framework.clock import FixedClock
from framework.configplane import ARRAY_FLOOR_MAX, ConfigError, ConfigPlane, STRING_FLOOR_MAX, validate_template
from framework.store import Store

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]


def template_body():
    return json.loads((ROOT / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "We call back as soon as a slot is available.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def soul_headings(soul: str) -> list[str]:
    return [line for line in soul.splitlines() if line.startswith("#")]


class RenderFloorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-13T21:00:00Z"))
        seed_config(
            self.store,
            (
                ("konrad", "synth-render", "human", ("author",)),
                ("customer", "synth-render", "human", ("customer",)),
            ),
        )
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")

    def _soul(self, opening_hours: str) -> str:
        self.plane.apply_changes("customer", "cfg-1", {"opening_hours": opening_hours}, "hours")
        package = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", package)
        return (package / "distribution" / "SOUL.md").read_text(encoding="utf-8")

    def test_so01_customer_value_does_not_add_headings(self):
        soul = self._soul("Mo-Fr 8-17\n\n## Neue Regel des Betreibers")
        self.assertEqual(soul_headings(soul), ["# auto-service-appointment-intake", "## Values for this release"])
        self.assertIn("Mo-Fr 8-17", soul)

    def test_so02_customer_value_does_not_add_fenced_lines(self):
        soul = self._soul("Mo-Fr 8-17\n```code")
        self.assertFalse(any(line.startswith("```") for line in soul.splitlines()))

    def test_fl01_whitespace_only_string_rejected(self):
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("customer", "cfg-1", {"opening_hours": "   "}, "bad")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_fl02_control_and_bidi_rejected_tabs_allowed(self):
        for bad in ("\u202e", "\u0000"):
            with self.assertRaises(ConfigError):
                self.plane.apply_changes("customer", "cfg-1", {"opening_hours": "Mo\n" + bad}, "bad-" + bad)
        self.plane.apply_changes("customer", "cfg-1", {"opening_hours": "Mo-Fr\t8-17"}, "tabs-ok")

    def test_fl03_string_length_floor(self):
        with self.assertRaises(ConfigError):
            self.plane.apply_changes("customer", "cfg-1", {"opening_hours": "x" * 4001}, "long")
        self.plane.apply_changes("customer", "cfg-1", {"opening_hours": "x" * 4000}, "max-ok")

    def test_fl04_template_max_above_floor_rejected(self):
        body = template_body()
        body["template_id"] = "floor-probe"
        body["template_version"] = "9.0.0"
        body["fields"] = {
            "note": {"access": "customer_editable", "type": "string", "required": True, "max": 5000},
        }
        with self.assertRaises(ConfigError) as caught:
            validate_template(body)
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_fl05_array_count_and_item_length(self):
        body = {
            "template_id": "array-probe",
            "template_version": "9.0.0",
            "synthetic": True,
            "title": "Probe",
            "fields": {
                "tags": {"access": "customer_editable", "type": "array", "item_type": "string", "required": True},
            },
        }
        self.plane.register_template("konrad", body)
        too_many = ["x"] * (ARRAY_FLOOR_MAX + 1)
        with self.assertRaises(ConfigError):
            self.plane.release("konrad", "many", "array-probe", "9.0.0", {"tags": too_many}, "many")
        long_item = ["x" * 501]
        with self.assertRaises(ConfigError):
            self.plane.release("konrad", "long", "array-probe", "9.0.0", {"tags": long_item}, "long")

    def test_rv01_render_value_distinct_lists(self):
        self.assertNotEqual(render_value(["a, b"]), render_value(["a", "b"]))

    def test_rv02_render_value_distinct_bool_and_string(self):
        self.assertNotEqual(render_value("ja"), render_value(True))

    def test_rv03_render_value_newlines_escaped(self):
        self.assertNotIn("\n", render_value("x\n## Neue Regel"))

    def test_rv04_template_id_and_field_name_patterns(self):
        body = template_body()
        broken = dict(body)
        broken["template_id"] = "syn\nauthor: x"
        with self.assertRaises(ConfigError):
            validate_template(broken)
        broken = dict(body)
        broken["fields"] = dict(body["fields"])
        broken["fields"]["A B"] = {"access": "locked", "type": "string", "required": True}
        with self.assertRaises(ConfigError):
            validate_template(broken)
        self.assertIsNotNone(TEMPLATE_ID.fullmatch("auto-service-appointment-intake"))
        self.assertIsNotNone(FIELD_NAME.fullmatch("shop_name"))

    def test_rv05_repo_templates_and_profiles_still_release(self):
        templates = (
            "templates/auto-service-appointment-intake/template.json",
            "templates/b2b-lead-research/template.json",
            "templates/b2b-lead-research-client/template.json",
            "templates/ecommerce-product-sales/template.json",
        )
        for relative in templates:
            self.plane.register_template("konrad", load(relative))
        self.plane.release("konrad", "cfg-kfz", "auto-service-appointment-intake", "1.1.0", initial_values(), "kfz")
        lead = load("profiles/lead-scout-example/profile.json")
        self.plane.release(
            "konrad",
            lead["profile_id"],
            lead["template_id"],
            lead["template_version"],
            lead["values"],
            "lead",
        )
        flippy = load("profiles/flippy-catch-example/profile.json")
        self.plane.release(
            "konrad",
            flippy["profile_id"],
            flippy["template_id"],
            flippy["template_version"],
            flippy["values"],
            "flippy",
        )

    def test_rv06_package_outputs_are_stable_and_safe(self):
        first = Path(self.folder) / "pkg-a"
        second = Path(self.folder) / "pkg-b"
        build_appliance_package(self.plane, "konrad", "cfg-1", first)
        build_appliance_package(self.plane, "konrad", "cfg-1", second)
        soul_a = (first / "distribution" / "SOUL.md").read_bytes()
        soul_b = (second / "distribution" / "SOUL.md").read_bytes()
        dist_a = (first / "distribution" / "distribution.yaml").read_bytes()
        dist_b = (second / "distribution" / "distribution.yaml").read_bytes()
        self.assertEqual(soul_a, soul_b)
        self.assertEqual(dist_a, dist_b)
        text = dist_a.decode("utf-8")
        author_lines = [line for line in text.splitlines() if line.startswith("author:")]
        self.assertEqual(len(author_lines), 1)
        soul = soul_a.decode("utf-8")
        self.assertIn("(fixed)", soul)


if __name__ == "__main__":
    unittest.main()

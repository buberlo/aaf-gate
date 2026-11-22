import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package, dry_run_lines
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.store import Store

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]


def template_body():
    return json.loads((ROOT / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "Wir rufen zurück, sobald ein Termin frei ist.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class AppliancePackageTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            self.store,
            (
                ("konrad", "synth-tenant", "human", ("author",)),
                ("cust", "synth-tenant", "human", ("customer",)),
            ),
        )
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")

    def test_package_matches_release_and_contains_no_secrets(self):
        first = Path(self.folder) / "pkg-a"
        second = Path(self.folder) / "pkg-b"
        built = build_appliance_package(self.plane, "konrad", "cfg-1", first)
        again = build_appliance_package(self.plane, "konrad", "cfg-1", second)
        self.assertEqual(built["package_hash"], again["package_hash"])
        self.assertEqual(built["release_hash"], self.plane.active("konrad", "cfg-1")["release_hash"])
        soul = (first / "distribution" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("Mo-Fr 8-17", soul)
        self.assertIn("Welche Arbeit?", soul)
        managed = (first / "managed" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("mode: manual", managed)
        self.assertIn("cron_mode: deny", managed)
        self.assertIn("permission_mode: standard", managed)
        self.assertNotIn("OPENAI", managed)
        self.assertFalse((first / "distribution" / ".env").exists())
        self.assertFalse((first / "distribution" / "auth.json").exists())
        manifest = (first / "distribution" / "distribution.yaml").read_text(encoding="utf-8")
        self.assertIn('name: "auto-service-appointment-intake"', manifest)
        assumptions = (first / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn("nicht belegt", assumptions.lower())
        self.assertIn("macOS", assumptions)
        script = (first / "provision_dry_run.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", script)
        self.assertNotIn("os.system", script)
        completed = subprocess.run([sys.executable, str(first / "provision_dry_run.py")], check=True, capture_output=True, text=True)
        self.assertIn("DRY-RUN", completed.stdout)
        self.assertIn("kein sudo", completed.stdout)
        self.assertEqual(dry_run_lines(first)[0], "DRY-RUN")

    def test_customer_edit_changes_profile_and_rollback_restores_it(self):
        self.plane.apply_changes("cust", "cfg-1", {"opening_hours": "Mo-Fr 9-18"}, "hours")
        updated = Path(self.folder) / "updated"
        built = build_appliance_package(self.plane, "konrad", "cfg-1", updated)
        soul = (updated / "distribution" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("Mo-Fr 9-18", soul)
        rollback = json.loads((updated / "rollback.json").read_text(encoding="utf-8"))
        self.assertEqual(rollback["previous_release_no"], 1)
        self.assertIsNotNone(rollback["previous_release_hash"])
        self.plane.rollback("konrad", "cfg-1", 1, "back")
        restored = Path(self.folder) / "restored"
        restored_build = build_appliance_package(self.plane, "konrad", "cfg-1", restored)
        self.assertNotEqual(restored_build["package_hash"], built["package_hash"])
        self.assertIn("Mo-Fr 8-17", (restored / "distribution" / "SOUL.md").read_text(encoding="utf-8"))
        self.assertEqual(restored_build["release_no"], 1)

    def test_release_rejects_fixed_allow_values_in_profile(self):
        values = dict(initial_values())
        values["allow_unattended_send"] = True
        with self.assertRaises(ConfigError) as caught:
            self.plane.release("konrad", "cfg-open", "auto-service-appointment-intake", "1.1.0", values, "open")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_packages_carry_allow_false_from_release(self):
        lead = load("profiles/lead-scout-example/profile.json")
        flippy = load("profiles/flippy-catch-example/profile.json")
        for relative in (
            "templates/b2b-lead-research/template.json",
            "templates/ecommerce-product-sales/template.json",
        ):
            self.plane.register_template("konrad", load(relative))
        cases = (
            ("cfg-kfz", "auto-service-appointment-intake", "1.1.0", initial_values()),
            (lead["profile_id"], lead["template_id"], lead["template_version"], lead["values"]),
            (flippy["profile_id"], flippy["template_id"], flippy["template_version"], flippy["values"]),
        )
        for config_id, template_id, version, values in cases:
            key = "rel-" + config_id
            self.plane.release("konrad", config_id, template_id, version, values, key)
            package = Path(self.folder) / config_id
            build_appliance_package(self.plane, "konrad", config_id, package)
            record = self.plane.release_record("konrad", config_id)
            for name, value in record["document"]["values"].items():
                if name.startswith("allow_"):
                    self.assertIs(value, False, msg=config_id + ":" + name)
            soul = (package / "distribution" / "SOUL.md").read_text(encoding="utf-8")
            self.assertIn("allow_unattended_send", soul)
            self.assertIn("(fixed)", soul)

    def test_customer_cannot_build_package(self):
        with self.assertRaises(ConfigError) as caught:
            build_appliance_package(self.plane, "cust", "cfg-1", Path(self.folder) / "nope")
        self.assertEqual(caught.exception.code, "FORBIDDEN")


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.store import Store

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class ProfileFlowTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        seed_config(
            store,
            (
                ("konrad", "synth-profile", "human", ("author",)),
                ("customer", "synth-profile", "human", ("customer",)),
            ),
        )
        self.plane = ConfigPlane(store, FixedClock("2026-11-12T17:20:00Z"))
        for relative in (
            "templates/b2b-lead-research/template.json",
            "templates/b2b-lead-research-client/template.json",
            "templates/ecommerce-product-sales/template.json",
        ):
            self.plane.register_template("konrad", load(relative))

    def test_lead_scout_gates_and_package(self):
        profile = load("profiles/lead-scout-example/profile.json")
        self.plane.release("konrad", profile["profile_id"], profile["template_id"], profile["template_version"], profile["values"], "lead-release")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("customer", profile["profile_id"], {"blocked_domains": ["example.com"]}, "lead-block")
        self.assertEqual(caught.exception.code, "LOCKED_FIELD")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("customer", profile["profile_id"], {"allow_platform_login": True}, "lead-login")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("customer", profile["profile_id"], {"daily_limit_per_client": 11}, "lead-over")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("customer", profile["profile_id"], {"request_char_limit_default": 301}, "lead-chars")
        self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        changed = self.plane.apply_changes("customer", profile["profile_id"], {"daily_limit_per_client": 8}, "lead-ok")
        self.assertEqual(changed["values"]["daily_limit_per_client"], 8)
        self.assertEqual(changed["values"]["blocked_domains"], ["linkedin.com"])
        original = self.plane.release_record("konrad", profile["profile_id"], 1)["release_hash"]
        rolled = self.plane.rollback("konrad", profile["profile_id"], 1, "lead-back")
        self.assertEqual(rolled["release_hash"], original)
        for letter in ("a", "b", "c", "d"):
            child = load(f"profiles/lead-scout-example/client/{letter}.json")
            self.plane.release("konrad", child["profile_id"], child["template_id"], child["template_version"], child["values"], f"child-{letter}")
            with self.assertRaises(ConfigError) as caught:
                self.plane.apply_changes("customer", child["profile_id"], {"approved_for_live_research": True}, f"child-lock-{letter}")
            self.assertEqual(caught.exception.code, "LOCKED_FIELD")
        with self.assertRaises(ConfigError):
            self.plane.apply_changes("customer", "lead-scout-example-a", {"tone": "Hallo"}, "tone")
        package = Path(self.folder) / "lead-package"
        built = build_appliance_package(self.plane, "konrad", profile["profile_id"], package)
        self.assertEqual(built["release_no"], 1)
        self.assertIn("linkedin.com", (package / "distribution" / "SOUL.md").read_text(encoding="utf-8"))

    def test_flippy_locks_and_package(self):
        profile = load("profiles/flippy-catch-example/profile.json")
        self.plane.release("konrad", profile["profile_id"], profile["template_id"], profile["template_version"], profile["values"], "flippy-release")
        for field, value in (
            ("allow_review_solicitation", True),
            ("allow_listing_write", True),
            ("allow_price_change", True),
            ("allow_ad_spend_change", True),
        ):
            with self.assertRaises(ConfigError) as caught:
                self.plane.apply_changes("customer", profile["profile_id"], {field: value}, "flippy-" + field)
            self.assertEqual(caught.exception.code, "INVALID_CONFIG")
        with self.assertRaises(ConfigError) as caught:
            self.plane.apply_changes("customer", profile["profile_id"], {"blocked_automation_domains": ["example.com"]}, "flippy-block")
        self.assertEqual(caught.exception.code, "LOCKED_FIELD")
        changed = self.plane.apply_changes("customer", profile["profile_id"], {"brand_voice": "SYN: Sie, knapp"}, "flippy-voice")
        self.assertEqual(changed["values"]["brand_voice"], "SYN: Sie, knapp")
        self.assertIn("amazon.de", changed["values"]["blocked_automation_domains"])
        self.assertFalse(changed["values"]["allow_unattended_send"])
        first = self.plane.release_record("konrad", profile["profile_id"], 1)["release_hash"]
        rolled = self.plane.rollback("konrad", profile["profile_id"], 1, "flippy-back")
        self.assertEqual(rolled["release_hash"], first)
        package = Path(self.folder) / "flippy-package"
        built = build_appliance_package(self.plane, "konrad", profile["profile_id"], package)
        text = (package / "distribution" / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("amazon.de", text)
        self.assertEqual(built["release_no"], 1)
        self.assertIn("DRY-RUN", (package / "provision_dry_run.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
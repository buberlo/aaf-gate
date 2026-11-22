import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package, dry_run_lines
from framework.canon import hash_value
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.devicemode import load_mode
from framework.store import Store
from framework.templates import expected_hashes

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]
HASHES_PATH = ROOT / "templates" / "HASHES.json"
CLOCK = FixedClock("2026-11-14T12:00:00Z")


def template_body():
    return json.loads((ROOT / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "We call back as soon as a slot is available.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


class PackageDeviceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        seed_config(self.store, (("konrad", "synth-tenant", "human", ("author",)),))
        self.plane = ConfigPlane(self.store, CLOCK)
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        self.expected = expected_hashes(HASHES_PATH)

    def test_pd01_package_contains_device_files_and_provenance(self):
        package = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", package, expected=self.expected)
        self.assertTrue((package / "device" / "device-mode.json").is_file())
        self.assertTrue((package / "device" / "HASHES.json").is_file())
        provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["device_mode"], "appliance")
        self.assertEqual(provenance["hashes_file_hash"], hash_value(self.expected))

    def test_pd02_device_files_loadable_on_device(self):
        package = Path(self.folder) / "pkg-device"
        build_appliance_package(self.plane, "konrad", "cfg-1", package, expected=self.expected)
        mode_path = package / "device" / "device-mode.json"
        hashes_path = package / "device" / "HASHES.json"
        os.chmod(mode_path, 0o644)
        os.chmod(hashes_path, 0o644)
        loaded = load_mode(mode_path)
        self.assertEqual(loaded["mode"], "appliance")
        self.assertEqual(loaded["expected"], self.expected)
        device_store = Store(str(Path(self.folder) / "device.sqlite"))
        seed_config(device_store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane = ConfigPlane.for_device(device_store, mode_path, CLOCK)
        plane.register_template("konrad", template_body())
        body = copy.deepcopy(template_body())
        body["title"] = "Abweichend"
        with self.assertRaises(ConfigError) as ctx:
            plane.register_template("konrad", body)
        self.assertEqual(ctx.exception.code, "TEMPLATE_HASH_MISMATCH")

    def test_pd03_unlisted_package_has_no_device_tree(self):
        package = Path(self.folder) / "pkg-open"
        build_appliance_package(self.plane, "konrad", "cfg-1", package)
        self.assertFalse((package / "device").exists())
        provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["device_mode"], "unlisted")
        self.assertIsNone(provenance["hashes_file_hash"])

    def test_pd04_dry_run_reports_device_mode(self):
        with_expected = Path(self.folder) / "pkg-a"
        without_expected = Path(self.folder) / "pkg-b"
        build_appliance_package(self.plane, "konrad", "cfg-1", with_expected, expected=self.expected)
        build_appliance_package(self.plane, "konrad", "cfg-1", without_expected)
        self.assertIn("device_mode appliance", dry_run_lines(with_expected))
        self.assertIn("device_mode unlisted", dry_run_lines(without_expected))

    def test_pd05_package_hash_reflects_device_mode(self):
        listed_a = Path(self.folder) / "listed-a"
        listed_b = Path(self.folder) / "listed-b"
        unlisted = Path(self.folder) / "unlisted"
        built_a = build_appliance_package(self.plane, "konrad", "cfg-1", listed_a, expected=self.expected)
        built_b = build_appliance_package(self.plane, "konrad", "cfg-1", listed_b, expected=self.expected)
        built_open = build_appliance_package(self.plane, "konrad", "cfg-1", unlisted)
        self.assertEqual(built_a["package_hash"], built_b["package_hash"])
        self.assertNotEqual(built_a["package_hash"], built_open["package_hash"])


if __name__ == "__main__":
    unittest.main()
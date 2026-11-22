import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.installcheck import package_files, verify_installation
from framework.store import Store
from framework.templates import expected_hashes

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]
HASHES_PATH = ROOT / "templates" / "HASHES.json"
CLOCK = FixedClock("2026-11-14T12:30:00Z")


def template_body():
    return json.loads((ROOT / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "We call back as soon as a slot is available.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mon-Fri 8-17",
        "shop_name": "Synth-Workshop",
    }


class InstallCheckLab:
    def __init__(self):
        self.folder = tempfile.mkdtemp()
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane = ConfigPlane(store, CLOCK)
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        self.package_dir = Path(self.folder) / "package"
        self.expected = expected_hashes(HASHES_PATH)
        self.built = build_appliance_package(plane, "konrad", "cfg-1", self.package_dir, expected=self.expected)
        self.installed = Path(self.folder) / "installed"
        shutil.copytree(self.package_dir, self.installed)
        for relative in ("device/device-mode.json", "device/HASHES.json"):
            target = self.installed / relative
            if target.is_file():
                os.chmod(target, 0o644)


class InstallCheckTests(unittest.TestCase):
    def setUp(self):
        self.lab = InstallCheckLab()

    def test_ic01_clean_copy_passes(self):
        issues = verify_installation(self.lab.package_dir, self.lab.installed, self.lab.built["package_hash"])
        self.assertEqual(issues, [])

    def test_ic02_changed_managed_config(self):
        path = self.lab.installed / "managed" / "config.yaml"
        path.write_text("approvals:\n  mode: smart\n", encoding="utf-8")
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "changed", "path": "managed/config.yaml"}])

    def test_ic03_missing_distribution_file(self):
        (self.lab.installed / "distribution" / "SOUL.md").unlink()
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "missing", "path": "distribution/SOUL.md"}])

    def test_ic04_extra_files_scope(self):
        (self.lab.installed / "distribution" / "extra.md").write_text("x", encoding="utf-8")
        (self.lab.installed / "other").mkdir()
        (self.lab.installed / "other" / "x.txt").write_text("y", encoding="utf-8")
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "extra", "path": "distribution/extra.md"}])

    @unittest.skipUnless(os.name == "posix", "posix permissions")
    def test_ic05_device_mode_invalid_permissions(self):
        mode_path = self.lab.installed / "device" / "device-mode.json"
        os.chmod(mode_path, 0o666)
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "device_mode_invalid", "path": "device/device-mode.json"}])

    def test_ic06_package_hash_guard(self):
        (self.lab.package_dir / "provenance.json").write_text("{}\n", encoding="utf-8")
        issues = verify_installation(
            self.lab.package_dir,
            self.lab.installed,
            self.lab.built["package_hash"],
        )
        self.assertEqual(issues, [{"kind": "package_hash_mismatch", "path": ""}])

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_ic07_symlink_in_package_invalid(self):
        link = self.lab.package_dir / "link.txt"
        link.symlink_to("provenance.json")
        with self.assertRaises(ConfigError) as ctx:
            package_files(self.lab.package_dir)
        self.assertEqual(ctx.exception.code, "PACKAGE_INVALID")

    def test_ic08_root_extra_file(self):
        (self.lab.installed / "evil.md").write_text("x", encoding="utf-8")
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "extra", "path": "evil.md"}])

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_ic09_extra_distribution_symlink_file(self):
        target = Path(self.lab.folder) / "outside.md"
        target.write_text("outside", encoding="utf-8")
        link = self.lab.installed / "distribution" / "link.md"
        link.symlink_to(target)
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "symlink", "path": "distribution/link.md"}])

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_ic10_package_file_replaced_by_symlink(self):
        soul = self.lab.installed / "distribution" / "SOUL.md"
        original = soul.read_text(encoding="utf-8")
        target = Path(self.lab.folder) / "soul-copy.md"
        target.write_text(original, encoding="utf-8")
        soul.unlink()
        soul.symlink_to(target)
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "symlink", "path": "distribution/SOUL.md"}])

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_ic11_distribution_dir_symlink(self):
        target = Path(self.lab.folder) / "skills-target"
        target.mkdir()
        link = self.lab.installed / "distribution" / "skills-link"
        link.symlink_to(target, target_is_directory=True)
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "symlink", "path": "distribution/skills-link"}])

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_ic12_top_level_distribution_symlink(self):
        dist_copy = Path(self.lab.folder) / "dist-copy"
        shutil.move(str(self.lab.installed / "distribution"), dist_copy)
        (self.lab.installed / "distribution").symlink_to(dist_copy, target_is_directory=True)
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "symlink", "path": "distribution"}])

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_ic13_device_mode_symlink_skips_invalid_check(self):
        mode = self.lab.installed / "device" / "device-mode.json"
        target = Path(self.lab.folder) / "mode-copy.json"
        shutil.copy(mode, target)
        os.chmod(target, 0o666)
        mode.unlink()
        mode.symlink_to(target)
        issues = verify_installation(self.lab.package_dir, self.lab.installed)
        self.assertEqual(issues, [{"kind": "symlink", "path": "device/device-mode.json"}])


if __name__ == "__main__":
    unittest.main()
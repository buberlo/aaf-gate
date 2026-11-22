import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.devicemode import load_mode
from framework.store import Store

from seed_config import seed_config

ROOT = Path(__file__).resolve().parents[2]
HASHES_PATH = ROOT / "templates" / "HASHES.json"
TEMPLATE_DIR = ROOT / "templates"
CLOCK = FixedClock("2026-11-14T11:45:00Z")


def template_body():
    return json.loads((TEMPLATE_DIR / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def modified_body():
    body = copy.deepcopy(template_body())
    body["title"] = "Geändert im Gerätetest"
    return body


class DeviceModeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        seed_config(self.store, (("konrad", "synth-tenant", "human", ("author",)),))

    def _write_mode(self, body: dict) -> Path:
        path = Path(self.folder) / "device-mode.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        os.chmod(path, 0o644)
        return path

    def _copy_hashes(self, name: str = "HASHES.json") -> Path:
        target = Path(self.folder) / name
        shutil.copy(HASHES_PATH, target)
        os.chmod(target, 0o644)
        return target

    def test_dm01_appliance_rejects_modified_template(self):
        self._copy_hashes()
        mode_path = self._write_mode({"mode": "appliance", "hashes_path": "HASHES.json"})
        plane = ConfigPlane.for_device(self.store, mode_path, CLOCK)
        self.assertEqual(plane.mode, "appliance")
        self.assertIsNotNone(plane.expected)
        with self.assertRaises(ConfigError) as ctx:
            plane.register_template("konrad", modified_body())
        self.assertEqual(ctx.exception.code, "TEMPLATE_HASH_MISMATCH")

    def test_dm02_development_allows_modified_template(self):
        mode_path = self._write_mode({"mode": "development"})
        plane = ConfigPlane.for_device(self.store, mode_path, CLOCK)
        self.assertEqual(plane.mode, "development")
        self.assertIsNone(plane.expected)
        result = plane.register_template("konrad", modified_body())
        self.assertEqual(result["template_id"], "auto-service-appointment-intake")

    def test_dm03_missing_mode_file(self):
        missing = Path(self.folder) / "missing.json"
        with self.assertRaises(ConfigError) as ctx:
            ConfigPlane.for_device(self.store, missing, CLOCK)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    def test_dm04_invalid_mode_shapes(self):
        cases = [
            {"mode": "test", "hashes_path": "HASHES.json"},
            {"mode": "development", "hashes_path": "HASHES.json", "extra": 1},
            {"mode": "appliance"},
            {"mode": "development", "hashes_path": "HASHES.json"},
        ]
        for body in cases:
            if body.get("mode") == "appliance" and body.get("hashes_path"):
                self._copy_hashes()
            mode_path = self._write_mode(body)
            with self.assertRaises(ConfigError) as ctx:
                ConfigPlane.for_device(self.store, mode_path, CLOCK)
            self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    @unittest.skipUnless(os.name == "posix", "posix permissions")
    def test_dm05_world_or_group_writable_files_rejected(self):
        self._copy_hashes()
        mode_path = self._write_mode({"mode": "appliance", "hashes_path": "HASHES.json"})
        os.chmod(mode_path, 0o666)
        with self.assertRaises(ConfigError) as ctx:
            ConfigPlane.for_device(self.store, mode_path, CLOCK)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")
        os.chmod(mode_path, 0o644)
        hashes = Path(self.folder) / "HASHES.json"
        os.chmod(hashes, 0o664)
        with self.assertRaises(ConfigError) as ctx:
            ConfigPlane.for_device(self.store, mode_path, CLOCK)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    def test_dm06_invalid_hashes_content_rejected(self):
        bad = Path(self.folder) / "bad-hashes.json"
        bad.write_text(json.dumps({"auto-service-appointment-intake@1.1.0": "not-a-hash"}), encoding="utf-8")
        os.chmod(bad, 0o644)
        mode_path = self._write_mode({"mode": "appliance", "hashes_path": "bad-hashes.json"})
        with self.assertRaises(ConfigError) as ctx:
            ConfigPlane.for_device(self.store, mode_path, CLOCK)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    def test_dm07_relative_hashes_path_resolved(self):
        self._copy_hashes()
        mode_path = self._write_mode({"mode": "appliance", "hashes_path": "HASHES.json"})
        loaded = load_mode(mode_path)
        self.assertEqual(loaded["mode"], "appliance")
        plane = ConfigPlane.for_device(self.store, mode_path, CLOCK)
        plane.register_template("konrad", template_body())

    def test_dm08_absolute_hashes_path_rejected(self):
        hashes = self._copy_hashes()
        mode_path = self._write_mode({"mode": "appliance", "hashes_path": str(hashes)})
        with self.assertRaises(ConfigError) as ctx:
            load_mode(mode_path)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    def test_dm09_parent_traversal_rejected(self):
        outside = Path(self.folder) / "outside"
        outside.mkdir()
        shutil.copy(HASHES_PATH, outside / "HASHES.json")
        device = Path(self.folder) / "device"
        device.mkdir()
        mode_path = device / "device-mode.json"
        mode_path.write_text(
            json.dumps({"mode": "appliance", "hashes_path": "../outside/HASHES.json"}),
            encoding="utf-8",
        )
        os.chmod(mode_path, 0o644)
        with self.assertRaises(ConfigError) as ctx:
            load_mode(mode_path)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    def test_dm10_symlinked_hashes_rejected(self):
        real = self._copy_hashes("real-hashes.json")
        link = Path(self.folder) / "HASHES.json"
        try:
            link.symlink_to(real)
        except OSError as exc:
            self.skipTest(str(exc))
        mode_path = self._write_mode({"mode": "appliance", "hashes_path": "HASHES.json"})
        with self.assertRaises(ConfigError) as ctx:
            load_mode(mode_path)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    def test_dm11_symlinked_mode_file_rejected(self):
        real = Path(self.folder) / "real-mode.json"
        real.write_text(json.dumps({"mode": "development"}), encoding="utf-8")
        os.chmod(real, 0o644)
        linked = Path(self.folder) / "linked-mode.json"
        try:
            linked.symlink_to(real)
        except OSError as exc:
            self.skipTest(str(exc))
        with self.assertRaises(ConfigError) as ctx:
            load_mode(linked)
        self.assertEqual(ctx.exception.code, "DEVICE_MODE_INVALID")

    def test_dm12_subfolder_hashes_allowed(self):
        sub = Path(self.folder) / "sub"
        sub.mkdir()
        shutil.copy(HASHES_PATH, sub / "HASHES.json")
        os.chmod(sub / "HASHES.json", 0o644)
        mode_path = self._write_mode({"mode": "appliance", "hashes_path": "sub/HASHES.json"})
        loaded = load_mode(mode_path)
        self.assertEqual(loaded["mode"], "appliance")


if __name__ == "__main__":
    unittest.main()
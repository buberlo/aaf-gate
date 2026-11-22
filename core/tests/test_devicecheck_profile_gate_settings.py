import tempfile
import unittest
from pathlib import Path

from framework.appliance import _profile_config
from framework.devicecheck import check_profile_plugins, profile_gate_settings_present
from framework.pinaudit import disabled_from_config

BASE = _profile_config()
REQUIRED = disabled_from_config(BASE)


class DevicecheckProfileGateSettingsTests(unittest.TestCase):
    def test_pg01_base_not_flagged(self):
        self.assertFalse(profile_gate_settings_present(BASE))

    def test_pg02_entries_block_flagged(self):
        text = (
            BASE
            + "  entries:\n"
            + "    aaf-gate:\n"
            + "      settings:\n"
            + '        label_ids: ["Label_1"]\n'
        )
        self.assertTrue(profile_gate_settings_present(text))

    def test_pg03_socket_line_only_flagged(self):
        text = BASE + '        socket_path: "/tmp/x.sock"\n'
        self.assertTrue(profile_gate_settings_present(text))

    def test_pg04_flow_entries_flagged(self):
        text = BASE + '  entries: {"aaf-gate": {settings: {label_ids: []}}}\n'
        self.assertTrue(profile_gate_settings_present(text))

    def test_pg05_check_profile_plugins_clean(self):
        root = Path(tempfile.mkdtemp(dir="/tmp"))
        home = root / "hermes"
        home.mkdir()
        (home / "config.yaml").write_text(BASE, encoding="utf-8")
        result = check_profile_plugins(home, REQUIRED)
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])

    def test_pg06_check_profile_plugins_gate_settings(self):
        bad_text = (
            BASE
            + "  entries:\n"
            + "    aaf-gate:\n"
            + "      settings:\n"
            + '        label_ids: ["Label_1"]\n'
        )
        root = Path(tempfile.mkdtemp(dir="/tmp"))
        home = root / "hermes"
        home.mkdir()
        (home / "config.yaml").write_text(bad_text, encoding="utf-8")
        result = check_profile_plugins(home, REQUIRED)
        self.assertFalse(result["ok"])
        self.assertIn("gate_settings_in_profile", result["issues"])
        profile_home = root / "profiles" / "kunde"
        profile_home.mkdir(parents=True)
        (profile_home / "config.yaml").write_text(bad_text, encoding="utf-8")
        sub = check_profile_plugins(profile_home, REQUIRED)
        self.assertFalse(sub["ok"])
        self.assertIn("gate_settings_in_profile", sub["issues"])


if __name__ == "__main__":
    unittest.main()
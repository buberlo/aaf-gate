import os
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.hookcheck import verify_hermes_home
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body


def plugin_list_names(text, key):
    lines = text.splitlines()
    in_plugins = False
    in_list = False
    current = None
    names = []
    for line in lines:
        stripped = line.strip()
        if stripped == "plugins:":
            in_plugins = True
            in_list = False
            current = None
            continue
        if not in_plugins:
            continue
        if stripped == key + ":":
            in_list = True
            current = key
            continue
        if in_list and current == key and stripped.startswith("- "):
            names.append(stripped[2:].strip())
            continue
        if line and not line.startswith(" "):
            in_plugins = False
            in_list = False
        elif stripped.endswith(":") and not stripped.startswith("- "):
            in_list = current == key and stripped == key + ":"
            if stripped in ("enabled:", "disabled:"):
                current = stripped[:-1]
                in_list = True
            else:
                in_list = False
    return names


class AppliancePluginsDisabledTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            store,
            (("konrad", "synth-tenant", "human", ("author",)),),
        )
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        self.target = Path(self.folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", self.target)

    def test_pdis01_disabled_lines_order(self):
        disabled_block = [
            "  disabled:",
            "    - disk-cleanup",
            "    - raft-platform",
            "    - security-guidance",
        ]
        for rel in ("managed/config.yaml", "distribution/config.yaml"):
            text = (self.target / rel).read_text(encoding="utf-8")
            lines = text.splitlines()
            gate = lines.index("    - aaf-gate")
            block = lines[gate + 1 : gate + 5]
            self.assertEqual(block, disabled_block)

    def test_pdis02_no_overlap_enabled_disabled(self):
        text = (self.target / "managed/config.yaml").read_text(encoding="utf-8")
        enabled = set(plugin_list_names(text, "enabled"))
        disabled = set(plugin_list_names(text, "disabled"))
        self.assertFalse(enabled & disabled)

    def test_pdis03_assumptions(self):
        text = (self.target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn("disk-cleanup, raft-platform und security-guidance Tool-Hooks", text)
        self.assertNotIn("nur disk-cleanup und security-guidance", text)
        marker = "Nicht belegt und deshalb nicht als Funktion dieses Pakets behauptet:"
        submodule = "Dass mitgelieferte Plugins Hooks nur über ctx.register_hook anmelden"
        self.assertIn(submodule, text)
        self.assertGreater(text.index(submodule), text.index(marker))

    def test_pdis05_disabled_list_exact(self):
        text = (self.target / "managed/config.yaml").read_text(encoding="utf-8")
        disabled = plugin_list_names(text, "disabled")
        self.assertEqual(disabled, ["disk-cleanup", "raft-platform", "security-guidance"])
        enabled = plugin_list_names(text, "enabled")
        self.assertNotIn("raft-platform", enabled)

    def test_pdis04_verify_hooks_clean(self):
        managed = (self.target / "managed/config.yaml").read_text(encoding="utf-8")
        home = Path(self.folder) / "hermes-home"
        home.mkdir()
        os.chmod(home, 0o755)
        config = home / "config.yaml"
        config.write_text(managed, encoding="utf-8")
        os.chmod(config, 0o644)
        self.assertEqual(verify_hermes_home(home), [])


if __name__ == "__main__":
    unittest.main()
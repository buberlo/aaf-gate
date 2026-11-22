import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.hookcheck import verify_hermes_home
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body

PROFILE_CONFIG_SNIPPET = (
    "approvals:\n"
    "  mode: manual\n"
    "  cron_mode: deny\n"
    "  single_query_mode: deny\n"
    "  unattended_mode: deny\n"
    "computer_use:\n"
    "  permission_mode: standard\n"
    "plugins:\n"
    "  enabled:\n"
    "    - aaf-gate\n"
)


class HookCheckFailClosedTests(unittest.TestCase):
    def profile(self):
        root = Path(tempfile.mkdtemp(dir="/tmp"))
        home = root / "hermes"
        home.mkdir()
        os.chmod(home, 0o755)
        config = home / "config.yaml"
        config.write_text(PROFILE_CONFIG_SNIPPET, encoding="utf-8")
        os.chmod(config, 0o644)
        return home

    def test_fc01_config_read_permission_error(self):
        home = self.profile()
        original = Path.read_text

        def read_text(self, *args, **kwargs):
            if self == home / "config.yaml":
                raise PermissionError("denied")
            return original(self, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read_text):
            issues = verify_hermes_home(home)
        self.assertIn("unreadable:config.yaml", issues)
        self.assertNotEqual(issues, [])

    def test_fc02_hooks_listdir_permission_error(self):
        home = self.profile()
        hooks = home / "hooks"
        hooks.mkdir()
        os.chmod(hooks, 0o755)

        def listdir(path):
            if path == str(hooks) or path == hooks:
                raise PermissionError("denied")
            return os.listdir(path)

        with mock.patch("framework.hookcheck.os.listdir", listdir):
            issues = verify_hermes_home(home)
        self.assertIn("unreadable:hooks", issues)

    def test_fc03_package_plugins_and_assumptions(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            store,
            (("konrad", "synth-tenant", "human", ("author",)),),
        )
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        target = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", target)
        expected_lines = ["plugins:", "  enabled:", "    - aaf-gate"]
        for rel in ("managed/config.yaml", "distribution/config.yaml"):
            text = (target / rel).read_text(encoding="utf-8")
            lines = [line for line in text.splitlines() if line in expected_lines]
            self.assertEqual(lines, expected_lines)
        home = self.profile()
        issues = verify_hermes_home(home)
        self.assertFalse(any(item.startswith("shell_hooks") for item in issues))
        assumptions = (target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn("plugins.disabled hat immer Vorrang", assumptions)
        marker = "Nicht belegt und deshalb nicht als Funktion dieses Pakets behauptet:"
        scoped = "Dass der Managed Scope plugins.enabled sperrt"
        self.assertIn(scoped, assumptions)
        self.assertGreater(assumptions.index(scoped), assumptions.index(marker))


if __name__ == "__main__":
    unittest.main()
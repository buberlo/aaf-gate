import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.devicecheck import check_profile_set, run_device_checks

from test_appliance_plugin import copy_plugin_tree
from test_devicecheck import CORE, make_hermes_home, make_profile_dir
from test_dumpcheck import GOOD_DUMP


class DeviceCheckProfileSetTests(unittest.TestCase):
    def test_ps01_missing_profiles_dir(self):
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        home = make_hermes_home(folder, "plugins:\n  enabled:\n    - aaf-gate\n")
        ok = check_profile_set(home, ())
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["present"], [])
        missing = check_profile_set(home, ("kunde",))
        self.assertIn("profile_missing:kunde", missing["issues"])

    def test_ps02_allowed_kunde_present(self):
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        home = make_hermes_home(folder, "plugins:\n  enabled:\n    - aaf-gate\n")
        make_profile_dir(home / "profiles" / "kunde")
        profiles = home / "profiles"
        profiles.mkdir(exist_ok=True)
        ok = check_profile_set(home, ("kunde",))
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["present"], ["kunde"])

    def test_ps03_unexpected_profile(self):
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        home = make_hermes_home(folder, "plugins:\n  enabled:\n    - aaf-gate\n")
        (home / "profiles").mkdir()
        make_profile_dir(home / "profiles" / "kunde")
        make_profile_dir(home / "profiles" / "zweit")
        bad = check_profile_set(home, ("kunde",))
        self.assertIn("profile_unexpected:zweit", bad["issues"])

    def test_ps04_file_and_symlink_entries(self):
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        home = make_hermes_home(folder, "plugins:\n  enabled:\n    - aaf-gate\n")
        profiles = home / "profiles"
        profiles.mkdir()
        (profiles / "notiz.txt").write_text("x", encoding="utf-8")
        target = folder / "real-profile"
        target.mkdir()
        (profiles / "link").symlink_to(target)
        issues = check_profile_set(home, ())["issues"]
        self.assertIn("profile_not_dir:notiz.txt", issues)
        self.assertIn("profile_symlink:link", issues)

    def test_ps05_profiles_symlink_and_invalid_allowed(self):
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        home = make_hermes_home(folder, "plugins:\n  enabled:\n    - aaf-gate\n")
        target = folder / "profiles-target"
        target.mkdir()
        (home / "profiles").symlink_to(target)
        bad = check_profile_set(home, ())
        self.assertEqual(bad["issues"], ["profiles_symlink"])
        invalid = check_profile_set(home, ("default",))
        self.assertEqual(invalid, {"error": "allowed_profile_invalid", "ok": False})
        invalid2 = check_profile_set(home, ("Kunde!",))
        self.assertEqual(invalid2, {"error": "allowed_profile_invalid", "ok": False})

    def test_ps06_run_device_checks_optional_and_strict(self):
        from test_devicecheck import DeviceCheckTests

        runner = DeviceCheckTests()
        folder, package, built = runner.build_fixture_package()
        config_text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), config_text)
        (hermes_home / "profiles").mkdir(exist_ok=True)
        make_profile_dir(hermes_home / "profiles" / "kunde", config_text=config_text)
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        installed, pkg_hash, managed = runner.install_layout(folder, package, built)
        plugin = copy_plugin_tree()
        plist = package / "launchd" / "de.example.aaf-gate.plist"
        env = {"OTEL_SDK_DISABLED": "true"}
        base_kwargs = dict(
            package=package,
            hermes_home=hermes_home,
            plugin_dir=plugin,
            launchd_plist=plist,
            dump_file=dump,
            home=tempfile.mkdtemp(dir="/tmp"),
            system_root=tempfile.mkdtemp(dir="/tmp"),
            environ=env,
            installed_root=installed,
            package_hash=pkg_hash,
            managed_dir=managed,
        )
        without = run_device_checks(**base_kwargs)
        self.assertNotIn("profile_set", without["checks"])
        make_profile_dir(hermes_home / "profiles" / "zweit", config_text=config_text)
        strict = run_device_checks(**base_kwargs, allowed_profiles=("kunde",))
        self.assertFalse(strict["checks"]["profile_set"]["ok"])
        self.assertFalse(strict["ok"])

    def test_ps07_cli_profile_kunde_unexpected_zweit(self):
        from test_devicecheck import DeviceCheckTests

        runner = DeviceCheckTests()
        folder, package, built = runner.build_fixture_package()
        config_text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), config_text)
        (hermes_home / "profiles").mkdir(exist_ok=True)
        make_profile_dir(hermes_home / "profiles" / "kunde", config_text=config_text)
        make_profile_dir(hermes_home / "profiles" / "zweit", config_text=config_text)
        plugin = copy_plugin_tree()
        installed, pkg_hash, managed = runner.install_layout(folder, package, built)
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        plist = package / "launchd" / "de.example.aaf-gate.plist"
        env = {"PATH": os.environ.get("PATH", ""), "OTEL_SDK_DISABLED": "true"}
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "framework.cli",
                "verify-device",
                "--package",
                str(package),
                "--installed",
                str(installed),
                "--package-hash",
                pkg_hash,
                "--managed-dir",
                str(managed),
                "--hermes-home",
                str(hermes_home),
                "--plugin",
                str(plugin),
                "--launchd",
                str(plist),
                "--dump",
                str(dump),
                "--home",
                tempfile.mkdtemp(dir="/tmp"),
                "--system-root",
                tempfile.mkdtemp(dir="/tmp"),
                "--profile",
                "kunde",
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
            env=env,
        )
        body = json.loads(completed.stdout)
        self.assertEqual(
            body["checks"]["profile_set"]["issues"],
            ["profile_unexpected:zweit"],
        )


if __name__ == "__main__":
    unittest.main()
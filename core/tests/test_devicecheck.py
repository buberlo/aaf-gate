import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _managed_config, _profile_config, build_appliance_package
from framework.cli import init_gate_db
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import check_active_profile, check_profile_plugins, run_device_checks
from framework.pinaudit import disabled_from_config, enabled_from_config
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service, gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pin_provenance import write_pin_fixture
from test_appliance_plugin import PLUGIN_SOURCE, copy_plugin_tree
from test_dumpcheck import GOOD_DUMP, SHA

CORE = Path(__file__).resolve().parents[1]


def install_aaf_gate(plugins_dir):
    plugins_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(plugins_dir, 0o755)
    gate = plugins_dir / "aaf-gate"
    gate.mkdir()
    os.chmod(gate, 0o755)
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copyfile(PLUGIN_SOURCE / name, gate / name)
        os.chmod(gate / name, 0o644)


def make_profile_dir(profile_dir, foreign=False, config_text=None):
    profile_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o755)
    if config_text is not None:
        config = profile_dir / "config.yaml"
        config.write_text(config_text, encoding="utf-8")
        os.chmod(config, 0o644)
    install_aaf_gate(profile_dir / "plugins")
    if foreign:
        other = profile_dir / "plugins" / "other"
        other.mkdir()
        os.chmod(other, 0o755)
        (other / "plugin.yaml").write_text("name: other\n", encoding="utf-8")
        os.chmod(other / "plugin.yaml", 0o644)


def make_hermes_home(root, config_text, with_profiles=None):
    home = root / "hermes"
    home.mkdir()
    os.chmod(home, 0o755)
    config = home / "config.yaml"
    config.write_text(config_text, encoding="utf-8")
    os.chmod(config, 0o644)
    install_aaf_gate(home / "plugins")
    if with_profiles:
        profiles = home / "profiles"
        profiles.mkdir()
        os.chmod(profiles, 0o755)
        for name, foreign in with_profiles.items():
            make_profile_dir(
                profiles / name,
                foreign=foreign,
                config_text=config_text,
            )
    return home


class DeviceCheckTests(unittest.TestCase):
    def build_fixture_package(self, plugin_dir=None, include_digest=True):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        package = Path(folder) / "pkg"
        gs = gate_service_tmp(folder)
        kwargs = {
            "gate_service": gs,
            "hermes_source": source,
            "hermes_commit": SHA,
        }
        if include_digest:
            kwargs["plugin_dir"] = plugin_dir or PLUGIN_SOURCE
        built = build_appliance_package(plane, "konrad", "cfg-1", package, **kwargs)
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        return folder, package, built

    def install_layout(self, folder, package, built):
        installed = Path(folder) / "installed"
        if installed.exists():
            shutil.rmtree(installed)
        shutil.copytree(package, installed)
        managed = Path(folder) / "managed"
        managed.mkdir(exist_ok=True)
        shutil.copyfile(package / "managed" / "config.yaml", managed / "config.yaml")
        return installed, built["package_hash"], managed

    def device_run(
        self,
        folder,
        package,
        built,
        hermes_home,
        plugin,
        launchd,
        dump,
        environ,
        relay_home=None,
        system_root=None,
        **overrides,
    ):
        if {"installed_root", "package_hash", "managed_dir"} <= overrides.keys():
            installed = overrides["installed_root"]
            pkg_hash = overrides["package_hash"]
            managed = overrides["managed_dir"]
        else:
            installed, pkg_hash, managed = self.install_layout(folder, package, built)
        return run_device_checks(
            package,
            hermes_home,
            plugin,
            launchd,
            dump,
            relay_home or tempfile.mkdtemp(dir="/tmp"),
            system_root or tempfile.mkdtemp(dir="/tmp"),
            environ,
            installed_root=overrides.get("installed_root", installed),
            package_hash=overrides.get("package_hash", pkg_hash),
            managed_dir=overrides.get("managed_dir", managed),
        )

    def test_vd01_overall_with_launchd_issue(self):
        folder, package, built = self.build_fixture_package(plugin_dir=PLUGIN_SOURCE)
        plugin = copy_plugin_tree()
        gs = gate_service()
        plist = package / "launchd" / (gs["label"] + ".plist")
        shutil.copyfile(plist, Path(folder) / "installed.plist")
        installed = Path(folder) / "installed.plist"
        os.chmod(installed, 0o600)
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), _managed_config())
        result = self.device_run(
            folder,
            package,
            built,
            hermes_home,
            plugin,
            installed,
            dump,
            {"OTEL_SDK_DISABLED": "true"},
        )
        launchd = result["checks"]["launchd"]
        if os.getuid() == 0:
            self.assertTrue(launchd["ok"])
        else:
            self.assertFalse(result["ok"])
            self.assertFalse(launchd["ok"])
            self.assertEqual(launchd["issues"], ["owner_not_root"])
        for key, body in result["checks"].items():
            if key == "launchd":
                continue
            self.assertTrue(body["ok"], msg=key)
        self.assertTrue(result["checks"]["profile_plugins:."].get("ok"))

    def test_vd02_profile_hooks(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        hermes_home = make_hermes_home(
            Path(folder),
            _managed_config(),
            with_profiles={"a": False, "b": True},
        )
        _, package, built = self.build_fixture_package()
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        plist = Path(folder) / "p.plist"
        plist.write_text("x", encoding="utf-8")
        result = self.device_run(
            folder,
            package,
            built,
            hermes_home,
            copy_plugin_tree(),
            plist,
            dump,
            {"OTEL_SDK_DISABLED": "true"},
        )
        self.assertTrue(result["checks"]["hooks:profiles/a"]["ok"])
        self.assertFalse(result["checks"]["hooks:profiles/b"]["ok"])
        self.assertIn(
            "foreign_plugin:plugins/other",
            result["checks"]["hooks:profiles/b"]["issues"],
        )

    def test_vd03_dump_and_plugin_errors(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        _, package_with, built = self.build_fixture_package()
        dump_bad = Path(folder) / "bad-dump.txt"
        dump_bad.write_text(GOOD_DUMP.replace("[3b421578]", "[deadbeef]"), encoding="utf-8")
        result = self.device_run(
            folder,
            package_with,
            built,
            make_hermes_home(Path(folder), _managed_config()),
            copy_plugin_tree(),
            package_with / "launchd" / "de.example.aaf-gate.plist",
            dump_bad,
            {"OTEL_SDK_DISABLED": "true"},
        )
        self.assertEqual(result["checks"]["hermes_dump"]["issues"], ["commit_mismatch"])
        package_plain, package_path, built_plain = self.build_fixture_package(include_digest=False)
        result2 = self.device_run(
            package_plain,
            package_path,
            built_plain,
            make_hermes_home(Path(package_plain), _managed_config()),
            copy_plugin_tree(),
            package_path / "launchd" / "de.example.aaf-gate.plist",
            Path(package_plain) / "missing-dump.txt",
            {"OTEL_SDK_DISABLED": "true"},
        )
        self.assertEqual(result2["checks"]["hermes_dump"]["error"], "dump_unreadable")
        self.assertEqual(result2["checks"]["plugin"]["error"], "PLUGIN_DIGEST_UNSET")

    def test_vd04_relay_otlp(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        _, package, built = self.build_fixture_package()
        dump_path = Path(folder) / "d.txt"
        dump_path.write_text(GOOD_DUMP, encoding="utf-8")
        result = self.device_run(
            folder,
            package,
            built,
            make_hermes_home(Path(folder), _managed_config()),
            copy_plugin_tree(),
            package / "launchd" / "de.example.aaf-gate.plist",
            dump_path,
            {
                "OTEL_SDK_DISABLED": "true",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://e",
            },
        )
        self.assertIn(
            "otlp_endpoint:OTEL_EXPORTER_OTLP_ENDPOINT",
            result["checks"]["relay"]["issues"],
        )

    def test_vd05_cli(self):
        folder, package, built = self.build_fixture_package()
        plugin = copy_plugin_tree()
        hermes_home = make_hermes_home(Path(folder), _managed_config())
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        plist = package / "launchd" / "de.example.aaf-gate.plist"
        installed, pkg_hash, managed = self.install_layout(folder, package, built)
        relay_home = tempfile.mkdtemp(dir="/tmp")
        system_root = tempfile.mkdtemp(dir="/tmp")
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
                relay_home,
                "--system-root",
                system_root,
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        body = json.loads(completed.stdout)
        self.assertIn("checks", body)
        self.assertIn("package", body)
        self.assertFalse(body["ok"])
        missing_home = subprocess.run(
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
                "--hermes-home",
                str(hermes_home),
                "--plugin",
                str(plugin),
                "--launchd",
                str(plist),
                "--dump",
                str(dump),
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.assertEqual(missing_home.returncode, 2)
        self.assertEqual(
            json.loads(missing_home.stdout.splitlines()[0]),
            {"error": "home_missing", "ok": False},
        )

    def test_vd06_checklist(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        target = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", target)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("verify-device --package <path to this package>", manual)
        self.assertIn("package_hash, release_hash and hermes_commit", manual)

    def test_vd07_install_managed_and_package(self):
        folder, package, built = self.build_fixture_package()
        provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        result = self.device_run(
            folder,
            package,
            built,
            make_hermes_home(Path(folder), _managed_config()),
            copy_plugin_tree(),
            package / "launchd" / "de.example.aaf-gate.plist",
            dump,
            {"OTEL_SDK_DISABLED": "true"},
        )
        self.assertTrue(result["checks"]["install"]["ok"])
        self.assertTrue(result["checks"]["managed"]["ok"])
        self.assertEqual(result["package"]["package_hash"], built["package_hash"])
        self.assertEqual(result["package"]["release_hash"], provenance["release_hash"])
        self.assertEqual(result["package"]["release_no"], provenance["release_no"])
        self.assertEqual(result["package"]["hermes_commit"], provenance["hermes_commit"])

    def test_vd08_install_issues(self):
        folder, package, built = self.build_fixture_package()
        installed, pkg_hash, managed = self.install_layout(folder, package, built)
        soul = installed / "distribution" / "SOUL.md"
        soul.write_text(soul.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        bad_hash = "0" * 64
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), _managed_config())
        plugin = copy_plugin_tree()
        launchd = package / "launchd" / "de.example.aaf-gate.plist"
        env = {"OTEL_SDK_DISABLED": "true"}
        result = self.device_run(
            folder,
            package,
            built,
            hermes_home,
            plugin,
            launchd,
            dump,
            env,
            installed_root=installed,
            package_hash=pkg_hash,
            managed_dir=managed,
        )
        self.assertIn(
            {"kind": "changed", "path": "distribution/SOUL.md"},
            result["checks"]["install"]["issues"],
        )
        clean_installed, clean_hash, clean_managed = self.install_layout(folder, package, built)
        mismatch = self.device_run(
            folder,
            package,
            built,
            hermes_home,
            plugin,
            launchd,
            dump,
            env,
            installed_root=clean_installed,
            package_hash=bad_hash,
            managed_dir=clean_managed,
        )
        self.assertEqual(
            mismatch["checks"]["install"]["issues"],
            [{"kind": "package_hash_mismatch", "path": ""}],
        )

    def test_vd09_managed_states(self):
        folder, package, built = self.build_fixture_package()
        installed, pkg_hash, managed = self.install_layout(folder, package, built)
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        common = {
            "folder": folder,
            "package": package,
            "built": built,
            "hermes_home": make_hermes_home(Path(folder), _managed_config()),
            "plugin": copy_plugin_tree(),
            "launchd": package / "launchd" / "de.example.aaf-gate.plist",
            "dump": dump,
            "environ": {"OTEL_SDK_DISABLED": "true"},
        }
        missing_managed = Path(folder) / "managed-missing"
        missing_managed.mkdir()
        missing = run_device_checks(
            package,
            common["hermes_home"],
            common["plugin"],
            common["launchd"],
            dump,
            tempfile.mkdtemp(dir="/tmp"),
            tempfile.mkdtemp(dir="/tmp"),
            common["environ"],
            installed_root=installed,
            package_hash=pkg_hash,
            managed_dir=missing_managed,
        )
        self.assertEqual(missing["checks"]["managed"]["issues"], ["managed_config_missing"])
        changed_managed = Path(folder) / "managed-changed"
        changed_managed.mkdir()
        target = changed_managed / "config.yaml"
        shutil.copyfile(package / "managed" / "config.yaml", target)
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        changed = run_device_checks(
            package,
            common["hermes_home"],
            common["plugin"],
            common["launchd"],
            dump,
            tempfile.mkdtemp(dir="/tmp"),
            tempfile.mkdtemp(dir="/tmp"),
            common["environ"],
            installed_root=installed,
            package_hash=pkg_hash,
            managed_dir=changed_managed,
        )
        self.assertEqual(changed["checks"]["managed"]["issues"], ["managed_config_changed"])
        link_managed = Path(folder) / "managed-link"
        link_managed.mkdir()
        link_target = link_managed / "config.yaml"
        correct = package / "managed" / "config.yaml"
        link_target.symlink_to(correct)
        linked = run_device_checks(
            package,
            common["hermes_home"],
            common["plugin"],
            common["launchd"],
            dump,
            tempfile.mkdtemp(dir="/tmp"),
            tempfile.mkdtemp(dir="/tmp"),
            common["environ"],
            installed_root=installed,
            package_hash=pkg_hash,
            managed_dir=link_managed,
        )
        self.assertEqual(linked["checks"]["managed"]["issues"], ["managed_config_symlink"])

    def test_vd10_broken_provenance(self):
        folder, package, built = self.build_fixture_package()
        (package / "provenance.json").write_text("[]\n", encoding="utf-8")
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        result = self.device_run(
            folder,
            package,
            built,
            make_hermes_home(Path(folder), _managed_config()),
            copy_plugin_tree(),
            package / "launchd" / "de.example.aaf-gate.plist",
            dump,
            {"OTEL_SDK_DISABLED": "true"},
        )
        self.assertEqual(result["package"]["error"], "provenance_unreadable")
        self.assertFalse(result["ok"])

    def test_vd11_cli_install_managed_keys(self):
        folder, package, built = self.build_fixture_package()
        installed, pkg_hash, managed = self.install_layout(folder, package, built)
        plugin = copy_plugin_tree()
        hermes_home = make_hermes_home(Path(folder), _managed_config())
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
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        body = json.loads(completed.stdout)
        self.assertIn("checks", body)
        self.assertIn("ok", body)
        self.assertIn("package", body)
        self.assertIn("install", body["checks"])
        self.assertIn("managed", body["checks"])

    def test_vd12_profile_plugins_clean(self):
        folder, package, built = self.build_fixture_package()
        profile_text = _profile_config()
        hermes_home = make_hermes_home(Path(folder), profile_text)
        profiles = hermes_home / "profiles" / "client"
        make_profile_dir(profiles, config_text=profile_text)
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        result = self.device_run(
            folder,
            package,
            built,
            hermes_home,
            copy_plugin_tree(),
            package / "launchd" / "de.example.aaf-gate.plist",
            dump,
            {"OTEL_SDK_DISABLED": "true"},
        )
        self.assertTrue(result["checks"]["profile_plugins:."].get("ok"))
        self.assertTrue(result["checks"]["profile_plugins:profiles/client"].get("ok"))

    def test_vd13_profile_plugins_issues(self):
        folder, package, built = self.build_fixture_package()
        base_config = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), base_config)
        profiles = hermes_home / "profiles" / "client"
        make_profile_dir(profiles, foreign=False)
        required = disabled_from_config(base_config)
        missing = check_profile_plugins(profiles, required)
        self.assertEqual(missing["issues"], ["config_missing"])
        bad_enabled = base_config.replace(
            "    - aaf-gate",
            "    - aaf-gate\n    - raft-platform",
            1,
        )
        bad_root = Path(tempfile.mkdtemp(dir="/tmp"))
        home_bad = make_hermes_home(bad_root, bad_enabled)
        bad = check_profile_plugins(home_bad, required)
        self.assertIn("plugins_enabled_not_gate_only", bad["issues"])
        lines = [line for line in base_config.splitlines() if line.strip() != "- raft-platform"]
        without_raft = "\n".join(lines) + "\n"
        nodis_root = Path(tempfile.mkdtemp(dir="/tmp"))
        home_missing_dis = make_hermes_home(nodis_root, without_raft)
        miss_dis = check_profile_plugins(home_missing_dis, required)
        self.assertIn("plugins_disabled_missing:raft-platform", miss_dis["issues"])

    def test_vd14_config_symlink(self):
        folder, package, built = self.build_fixture_package()
        config_text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), config_text)
        target = hermes_home / "config.real.yaml"
        target.write_text(config_text, encoding="utf-8")
        link = hermes_home / "config.yaml"
        link.unlink()
        link.symlink_to(target)
        required = disabled_from_config(config_text)
        issues = check_profile_plugins(hermes_home, required)
        self.assertEqual(issues["issues"], ["config_symlink"])

    def test_vd15_enabled_disabled_from_package(self):
        folder, package, built = self.build_fixture_package()
        text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        self.assertEqual(enabled_from_config(text), ["aaf-gate"])
        self.assertEqual(
            disabled_from_config(text),
            ["disk-cleanup", "raft-platform", "security-guidance"],
        )

    def test_vd16_assumptions_and_manual(self):
        folder, package, built = self.build_fixture_package()
        manual = (package / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("verify-device checks that per profile", manual)
        assumptions = (package / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        marker = "Not documented and therefore not claimed as function of this package:"
        line = "Default profile is ~/.hermes; named profiles under ~/.hermes/profiles/."
        self.assertIn(line, assumptions)
        self.assertLess(assumptions.index(line), assumptions.index(marker))

    def test_vd17_active_profile_default(self):
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        home = make_hermes_home(folder, _managed_config())
        ok = check_active_profile(home, "default")
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["active"], "default")
        empty = home / "active_profile"
        empty.write_text("", encoding="utf-8")
        ok_empty = check_active_profile(home, "default")
        self.assertTrue(ok_empty["ok"])
        self.assertEqual(ok_empty["active"], "default")
        empty.write_text("\ufeffdefault\n", encoding="utf-8")
        bom = check_active_profile(home, "default")
        self.assertTrue(bom["ok"])
        self.assertEqual(bom["active"], "default")

    def test_vd18_active_profile_named(self):
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        config_text = _managed_config()
        home = make_hermes_home(folder, config_text)
        make_profile_dir(home / "profiles" / "client", config_text=config_text)
        profile_file = home / "active_profile"
        profile_file.write_text("client\n", encoding="utf-8")
        good = check_active_profile(home, "client")
        self.assertTrue(good["ok"])
        profile_file.unlink()
        missing = check_active_profile(home, "client")
        self.assertEqual(missing["issues"], ["active_profile_mismatch"])
        profile_file.write_text("weg\n", encoding="utf-8")
        dangling = check_active_profile(home, "client")
        self.assertIn("active_profile_dangling", dangling["issues"])
        self.assertIn("active_profile_mismatch", dangling["issues"])
        profile_file.write_text("Client!\n", encoding="utf-8")
        invalid = check_active_profile(home, "client")
        self.assertIn("active_profile_invalid", invalid["issues"])
        profile_file.unlink()
        target = folder / "client-active"
        target.write_text("client", encoding="utf-8")
        profile_file.symlink_to(target)
        linked = check_active_profile(home, "client")
        self.assertEqual(linked["issues"], ["active_profile_symlink"])
        self.assertIsNone(linked["active"])
        expect_missing = check_active_profile(home, "missing")
        self.assertIn("expected_profile_missing", expect_missing["issues"])
        bad_expected = check_active_profile(home, "A B")
        self.assertEqual(bad_expected, {"error": "expected_profile_invalid", "ok": False})

    def test_vd19_cli_active_profile(self):
        folder, package, built = self.build_fixture_package()
        config_text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), config_text)
        make_profile_dir(hermes_home / "profiles" / "client", config_text=config_text)
        (hermes_home / "active_profile").write_text("client\n", encoding="utf-8")
        plugin = copy_plugin_tree()
        installed, pkg_hash, managed = self.install_layout(folder, package, built)
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        plist = package / "launchd" / "de.example.aaf-gate.plist"
        relay_home = tempfile.mkdtemp(dir="/tmp")
        system_root = tempfile.mkdtemp(dir="/tmp")
        env = {"PATH": os.environ.get("PATH", ""), "OTEL_SDK_DISABLED": "true"}
        base_cmd = [
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
            relay_home,
            "--system-root",
            system_root,
        ]
        client = subprocess.run(base_cmd + ["--profile", "client"], cwd=CORE, capture_output=True, text=True, env=env)
        client_body = json.loads(client.stdout)
        self.assertTrue(client_body["checks"]["active_profile"]["ok"])
        default = subprocess.run(base_cmd + ["--profile", "default"], cwd=CORE, capture_output=True, text=True, env=env)
        default_body = json.loads(default.stdout)
        self.assertIn(
            "active_profile_mismatch",
            default_body["checks"]["active_profile"]["issues"],
        )

    def test_vd20_docs_active_profile(self):
        folder, package, _built = self.build_fixture_package()
        manual = (package / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("`verify-device --profile <name>` checks that.", manual)
        assumptions = (package / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        profile_line = "Default profile is ~/.hermes; named profiles under ~/.hermes/profiles/."
        sticky_line = "Plain `hermes` without -p follows ~/.hermes/active_profile"
        marker = "Not documented and therefore not claimed as function of this package:"
        self.assertIn(sticky_line, assumptions)
        self.assertGreater(assumptions.index(sticky_line), assumptions.index(profile_line))
        self.assertLess(assumptions.index(sticky_line), assumptions.index(marker))


if __name__ == "__main__":
    unittest.main()
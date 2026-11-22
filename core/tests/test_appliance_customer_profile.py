import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.devicecheck import package_customer_profile
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service
from test_appliance_package import initial_values, template_body
from test_appliance_pin_provenance import write_pin_fixture
from test_appliance_plugin import PLUGIN_SOURCE, copy_plugin_tree
from test_devicecheck import CORE, make_hermes_home, make_profile_dir
from test_dumpcheck import GOOD_DUMP, SHA

OLD_POINT5 = (
    "5. ~/.hermes/active_profile fehlt (Standardprofil) oder nennt das Kundenprofil dieses Pakets. "
    "`verify-device --profile <name>` prüft das."
)


class ApplianceCustomerProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            self.store,
            (("konrad", "synth-tenant", "human", ("author",)),),
        )
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")

    def test_cp01_without_customer_profile(self):
        target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gate_service())
        provenance = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
        self.assertIsNone(provenance.get("customer_profile"))
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn(OLD_POINT5, manual)
        self.assertNotIn("Unter ~/.hermes/profiles/ liegt genau das Verzeichnis kunde", manual)

    def test_cp02_with_customer_profile_kunde(self):
        target = Path(self.folder) / "kunde"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            target,
            gate_service=gate_service(),
            customer_profile="kunde",
        )
        provenance = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(provenance["customer_profile"], "kunde")
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn(
            "5. ~/.hermes/active_profile fehlt (Standardprofil) oder nennt das Kundenprofil kunde. "
            "Unter ~/.hermes/profiles/ liegt genau das Verzeichnis kunde. "
            "verify-device prüft beides mit dem Profilnamen aus provenance.json.",
            manual,
        )

    def test_cp08_profile_not_derived_from_tenant(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gate_service())
        provenance = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
        self.assertIsNone(provenance.get("customer_profile"))
        self.assertEqual(provenance.get("tenant_id"), "synth-tenant")
        assumptions = (target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn("wird nicht aus tenant_id abgeleitet", assumptions)

    def test_cp03_invalid_customer_profile(self):
        target = Path(self.folder) / "bad"
        for value in ("default", "Kunde!", ""):
            with self.assertRaises(ConfigError) as ctx:
                build_appliance_package(
                    self.plane,
                    "konrad",
                    "cfg-1",
                    target,
                    customer_profile=value,
                )
            self.assertEqual(ctx.exception.code, "CUSTOMER_PROFILE_INVALID")
            self.assertFalse(target.exists())

    def test_cp04_package_customer_profile_reader(self):
        package = Path(self.folder) / "pkg"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            package,
            customer_profile="kunde",
        )
        self.assertEqual(package_customer_profile(package), (True, "kunde"))
        plain = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        self.assertEqual(package_customer_profile(plain), (True, None))
        missing = Path(self.folder) / "nope"
        missing.mkdir()
        self.assertEqual(package_customer_profile(missing), (False, None))
        broken = Path(self.folder) / "broken"
        broken.mkdir()
        (broken / "provenance.json").write_text('{"customer_profile": 5}\n', encoding="utf-8")
        self.assertEqual(package_customer_profile(broken), (False, None))
        legacy = Path(self.folder) / "legacy"
        legacy.mkdir()
        text = (package / "provenance.json").read_text(encoding="utf-8")
        payload = json.loads(text)
        del payload["customer_profile"]
        (legacy / "provenance.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(package_customer_profile(legacy), (True, None))

    def test_cp05_cli_verify_device_kunde_from_provenance(self):
        from test_devicecheck import DeviceCheckTests

        runner = DeviceCheckTests()
        folder, _package, _built = runner.build_fixture_package()
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        pkg = Path(folder) / "bound"
        built2 = build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            pkg,
            gate_service=gate_service(),
            hermes_source=source,
            hermes_commit=SHA,
            plugin_dir=PLUGIN_SOURCE,
            customer_profile="kunde",
        )
        config_text = (pkg / "managed" / "config.yaml").read_text(encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), config_text)
        (hermes_home / "profiles").mkdir(exist_ok=True)
        make_profile_dir(hermes_home / "profiles" / "kunde", config_text=config_text)
        (hermes_home / "active_profile").write_text("kunde\n", encoding="utf-8")
        installed, pkg_hash, managed = runner.install_layout(folder, pkg, built2)
        dump = Path(folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        plist = pkg / "launchd" / "de.example.aaf-gate.plist"
        plugin = copy_plugin_tree()
        env = {"PATH": os.environ.get("PATH", ""), "OTEL_SDK_DISABLED": "true"}
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "framework.cli",
                "verify-device",
                "--package",
                str(pkg),
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
        body = json.loads(completed.stdout)
        self.assertEqual(body["checks"]["active_profile"]["expected"], "kunde")
        self.assertEqual(body["checks"]["profile_set"]["allowed"], ["kunde"])

    def test_cp06_cli_profile_mismatch(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        pkg = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", pkg, customer_profile="kunde")
        env = {"PATH": os.environ.get("PATH", "")}
        base = [
            sys.executable,
            "-m",
            "framework.cli",
            "verify-device",
            "--package",
            str(pkg),
            "--installed",
            str(pkg),
            "--package-hash",
            "0" * 64,
            "--hermes-home",
            str(folder),
            "--plugin",
            str(folder),
            "--launchd",
            str(folder),
            "--dump",
            str(folder),
            "--home",
            folder,
        ]
        wrong_profile = subprocess.run(
            base + ["--profile", "andere"],
            cwd=CORE,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(wrong_profile.returncode, 2)
        self.assertEqual(
            json.loads(wrong_profile.stdout),
            {"error": "profile_package_mismatch", "expected": "kunde", "ok": False},
        )
        wrong_allowed = subprocess.run(
            base + ["--allowed-profile", "kunde", "--allowed-profile", "zweit"],
            cwd=CORE,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(wrong_allowed.returncode, 2)
        self.assertEqual(
            json.loads(wrong_allowed.stdout),
            {"error": "profile_package_mismatch", "expected": "kunde", "ok": False},
        )

    def test_cp07_cli_default_without_customer_profile(self):
        from test_devicecheck import DeviceCheckTests

        runner = DeviceCheckTests()
        folder, package, built = runner.build_fixture_package()
        installed, pkg_hash, managed = runner.install_layout(folder, package, built)
        config_text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        hermes_home = make_hermes_home(Path(folder), config_text)
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
                str(copy_plugin_tree()),
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
        body = json.loads(completed.stdout)
        self.assertEqual(body["checks"]["active_profile"]["expected"], "default")


if __name__ == "__main__":
    unittest.main()
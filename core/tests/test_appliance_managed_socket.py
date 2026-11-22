import io
import json
import os
import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _profile_config, build_appliance_package
from framework.cli import init_gate_db
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import run_device_checks
from framework.pinaudit import disabled_from_config, enabled_from_config
from framework.store import Store

from seed_config import seed_config

from test_appliance_launchd import gate_service, gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pin_provenance import write_pin_fixture
from test_appliance_plugin import PLUGIN_SOURCE, copy_plugin_tree
from test_devicecheck import install_aaf_gate, make_hermes_home
from test_dumpcheck import GOOD_DUMP, SHA

CORE = Path(__file__).resolve().parents[1]

MANAGED_SUFFIX = (
    "  entries:\n"
    "    aaf-gate:\n"
    "      settings:\n"
    '        socket_path: "/var/aaf/run/g.sock"\n'
    "        label_ids: []\n"
)

ASSUMPTIONS_SOCKET = (
    "Mit Gate-Dienst setzt dieses Paket plugins.entries.aaf-gate.settings.socket_path im Managed Scope."
)

ASSUMPTIONS_LABEL_IDS = (
    "Mit Gate-Dienst setzt dieses Paket außerdem plugins.entries.aaf-gate.settings.label_ids im Managed Scope, ohne Paketparameter als leere Liste, damit die Benutzerkonfiguration keine Label-IDs nachtragen kann."
)

CHECKLIST_SOCKET = (
    "7. /etc/hermes/config.yaml enthält plugins.entries.aaf-gate.settings.socket_path "
    "mit demselben Pfad wie --socket in der Gate-Plist."
)


class ApplianceManagedSocketTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            self.store,
            (("konrad", "synth-tenant", "human", ("author",)),),
        )
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")

    def test_ms01_without_gate_service(self):
        target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        managed = (target / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("entries:", managed)
        self.assertNotIn("socket_path", managed)

    def test_ms02_with_gate_service_managed_text(self):
        gs = gate_service()
        target = Path(self.folder) / "gated"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        managed = (target / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertTrue(managed.endswith(MANAGED_SUFFIX))
        distribution = (target / "distribution/config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("socket_path", distribution)

    def test_ms03_plugin_lists_from_managed(self):
        gs = gate_service()
        target = Path(self.folder) / "lists"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        managed = (target / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertEqual(enabled_from_config(managed), ["aaf-gate"])
        self.assertEqual(
            disabled_from_config(managed),
            ["disk-cleanup", "raft-platform", "security-guidance"],
        )

    def test_ms04_plist_matches_managed_socket(self):
        gs = gate_service()
        target = Path(self.folder) / "plist"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        managed = (target / "managed/config.yaml").read_text(encoding="utf-8")
        plist_path = target / "launchd" / (gs["label"] + ".plist")
        plist = plistlib.loads(plist_path.read_bytes())
        args = plist["ProgramArguments"]
        socket_idx = args.index("--socket")
        plist_socket = args[socket_idx + 1]
        self.assertEqual(plist_socket, "/var/aaf/run/g.sock")
        self.assertIn('socket_path: "/var/aaf/run/g.sock"', managed)

    def test_ms05_docs_socket_path(self):
        plain = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        gated = Path(self.folder) / "gated"
        build_appliance_package(self.plane, "konrad", "cfg-1", gated, gate_service=gate_service())
        marker = "Nicht belegt und deshalb nicht als Funktion dieses Pakets behauptet:"
        assumptions = (gated / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn(ASSUMPTIONS_SOCKET, assumptions)
        self.assertIn(ASSUMPTIONS_LABEL_IDS, assumptions)
        self.assertLess(assumptions.index(ASSUMPTIONS_SOCKET), assumptions.index(marker))
        manual_gate = (gated / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn(CHECKLIST_SOCKET, manual_gate)
        manual_plain = (plain / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertNotIn(CHECKLIST_SOCKET, manual_plain)

    def test_ms06_device_profile_plugins_with_managed_copy(self):
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        package = Path(self.folder) / "pkg"
        gs = gate_service_tmp(self.folder)
        built = build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            package,
            gate_service=gs,
            hermes_source=source,
            hermes_commit=SHA,
            plugin_dir=PLUGIN_SOURCE,
        )
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        folder = Path(self.folder) / "device"
        folder.mkdir()
        hermes_home = make_hermes_home(folder, _profile_config())
        installed = folder / "installed"
        shutil.copytree(package, installed)
        managed_dir = folder / "managed"
        managed_dir.mkdir()
        shutil.copyfile(package / "managed" / "config.yaml", managed_dir / "config.yaml")
        dump = folder / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        plist = package / "launchd" / "de.example.aaf-gate.plist"
        plugin = copy_plugin_tree()
        result = run_device_checks(
            package,
            hermes_home,
            plugin,
            plist,
            dump,
            tempfile.mkdtemp(dir="/tmp"),
            tempfile.mkdtemp(dir="/tmp"),
            {"PATH": os.environ.get("PATH", ""), "OTEL_SDK_DISABLED": "true"},
            installed_root=installed,
            package_hash=built["package_hash"],
            managed_dir=managed_dir,
        )
        self.assertTrue(result["checks"]["profile_plugins:."].get("ok"))


if __name__ == "__main__":
    unittest.main()
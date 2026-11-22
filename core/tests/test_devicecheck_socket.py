import io
import os
import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _managed_config, _profile_config, build_appliance_package
from framework.cli import init_gate_db
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import (
    check_socket_path,
    managed_socket_path,
    plist_socket_path,
    run_device_checks,
)
from framework.launchd import gate_plist
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service, gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pin_provenance import write_pin_fixture
from test_appliance_plugin import PLUGIN_SOURCE, copy_plugin_tree
from test_devicecheck import make_hermes_home
from test_dumpcheck import GOOD_DUMP, SHA


class DeviceCheckSocketTests(unittest.TestCase):
    def test_sp01_managed_socket_path_basic(self):
        path = "/var/aaf/run/g.sock"
        self.assertEqual(managed_socket_path(_managed_config(path)), path)
        self.assertIsNone(managed_socket_path(_managed_config()))
        self.assertIsNone(managed_socket_path(""))

    def test_sp02_managed_socket_path_wrong_plugin_or_json(self):
        wrong_plugin = (
            _profile_config()
            + "  entries:\n"
            + "    other:\n"
            + "      settings:\n"
            + '        socket_path: "/x"\n'
        )
        self.assertIsNone(managed_socket_path(wrong_plugin))
        broken = _managed_config("/var/aaf/run/g.sock").replace(
            '"/var/aaf/run/g.sock"',
            "bareword",
        )
        self.assertIsNone(managed_socket_path(broken))

    def test_sp03_plist_socket_path(self):
        gs = gate_service()
        payload = gate_plist(**gs)
        self.assertEqual(plist_socket_path(payload), gs["socket_path"])
        self.assertIsNone(plist_socket_path(b"kaputt"))
        plist = {
            "Label": "x",
            "ProgramArguments": ["/usr/bin/python3", "-m", "x"],
        }
        self.assertIsNone(plist_socket_path(plistlib.dumps(plist)))

    def test_sp04_check_socket_path_ok(self):
        gs = gate_service()
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        plist_path = folder / "gate.plist"
        plist_path.write_bytes(gate_plist(**gs))
        (folder / "config.yaml").write_text(_managed_config(gs["socket_path"]), encoding="utf-8")
        result = check_socket_path(plist_path, folder)
        self.assertTrue(result["ok"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["plist"], gs["socket_path"])
        self.assertEqual(result["managed"], gs["socket_path"])

    def test_sp05_check_socket_path_missing_and_mismatch(self):
        gs = gate_service()
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        plist_path = folder / "gate.plist"
        plist_path.write_bytes(gate_plist(**gs))
        no_socket = folder / "plain"
        no_socket.mkdir()
        (no_socket / "config.yaml").write_text(_managed_config(), encoding="utf-8")
        missing = check_socket_path(plist_path, no_socket)
        self.assertEqual(missing["issues"], ["managed_socket_missing"])
        mismatch_dir = folder / "mismatch"
        mismatch_dir.mkdir()
        (mismatch_dir / "config.yaml").write_text(
            _managed_config("/other/path.sock"),
            encoding="utf-8",
        )
        mismatch = check_socket_path(plist_path, mismatch_dir)
        self.assertIn("socket_path_mismatch", mismatch["issues"])
        self.assertEqual(mismatch["plist"], gs["socket_path"])
        self.assertEqual(mismatch["managed"], "/other/path.sock")

    def test_sp06_unreadable_and_run_device_checks(self):
        gs = gate_service()
        folder = Path(tempfile.mkdtemp(dir="/tmp"))
        plist_path = folder / "gate.plist"
        plist_path.write_bytes(gate_plist(**gs))
        config_dir = folder / "managed"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(_managed_config(gs["socket_path"]), encoding="utf-8")
        missing_plist = check_socket_path(folder / "missing.plist", config_dir)
        self.assertEqual(missing_plist["issues"], ["plist_unreadable"])
        link_dir = folder / "linked"
        link_dir.mkdir()
        link_dir.joinpath("config.yaml").symlink_to(config_dir / "config.yaml")
        linked = check_socket_path(plist_path, link_dir)
        self.assertEqual(linked["issues"], ["managed_unreadable"])

        store = Store(str(folder / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        gs = gate_service_tmp(folder)
        package = folder / "pkg"
        built = build_appliance_package(
            plane,
            "konrad",
            "cfg-1",
            package,
            gate_service=gs,
            hermes_source=source,
            hermes_commit=SHA,
            plugin_dir=PLUGIN_SOURCE,
        )
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        managed_text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        device = folder / "device"
        device.mkdir()
        hermes_home = make_hermes_home(device, managed_text)
        installed = device / "installed"
        shutil.copytree(package, installed)
        managed_dir = device / "managed-live"
        managed_dir.mkdir()
        shutil.copyfile(package / "managed" / "config.yaml", managed_dir / "config.yaml")
        dump = device / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
        plist = package / "launchd" / (gs["label"] + ".plist")
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
        self.assertTrue(result["checks"]["socket_path"]["ok"])
        self.assertIn("gate_tenant", result["checks"])
        self.assertTrue(result["checks"]["gate_tenant"]["ok"])


if __name__ == "__main__":
    unittest.main()
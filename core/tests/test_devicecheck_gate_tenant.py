import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import check_gate_tenant, plist_gate_args, run_device_checks
from framework.launchd import gate_plist
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service, gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pin_provenance import write_pin_fixture
from test_appliance_plugin import PLUGIN_SOURCE, copy_plugin_tree
from test_devicecheck import make_hermes_home
from test_dumpcheck import GOOD_DUMP, SHA


class DeviceCheckGateTenantTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            self.store,
            (
                ("konrad", "synth-tenant", "human", ("author",)),
                ("cust", "synth-tenant", "human", ("customer",)),
            ),
        )
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")

    def _build_package(self, folder=None):
        folder = folder or self.folder
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        package = Path(folder) / "pkg"
        gs = gate_service_tmp(folder)
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
        return gs, package, built

    def test_gt01_plist_gate_args(self):
        gs = gate_service()
        data = gate_plist(**gs)
        parsed = plist_gate_args(data)
        self.assertEqual(parsed, {"db": gs["db"], "principal": gs["principal"]})
        self.assertIsNone(plist_gate_args(b"kaputt"))

    def test_gt02_matching_tenant(self):
        gs, package, _built = self._build_package()
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        plist = package / "launchd" / (gs["label"] + ".plist")
        result = check_gate_tenant(plist, package)
        self.assertTrue(result["ok"])
        self.assertEqual(result["gate_tenant"], result["package_tenant"])
        self.assertEqual(result["package_tenant"], "synth-tenant")

    def test_gt03_tenant_mismatch(self):
        gs, package, _built = self._build_package()
        init_gate_db(gs["db"], gs["principal"], "other-tenant", out=io.StringIO())
        plist = package / "launchd" / (gs["label"] + ".plist")
        result = check_gate_tenant(plist, package)
        self.assertFalse(result["ok"])
        self.assertEqual(result["issues"], ["gate_tenant_mismatch"])

    def test_gt04_principal_missing_and_disabled(self):
        gs, package, _built = self._build_package()
        init_gate_db(gs["db"], "other-principal", "synth-tenant", out=io.StringIO())
        plist = package / "launchd" / (gs["label"] + ".plist")
        missing = check_gate_tenant(plist, package)
        self.assertEqual(missing["issues"], ["gate_principal_missing"])

        if os.path.lexists(gs["db"]):
            os.unlink(gs["db"])
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        conn = sqlite3.connect(gs["db"])
        try:
            conn.execute(
                "UPDATE principals SET disabled=1 WHERE principal_id=?",
                (gs["principal"],),
            )
            conn.commit()
        finally:
            conn.close()
        disabled = check_gate_tenant(plist, package)
        self.assertEqual(disabled["issues"], ["gate_principal_disabled"])

    def test_gt05_db_missing_and_package_tenant_missing(self):
        gs, package, _built = self._build_package()
        plist = package / "launchd" / (gs["label"] + ".plist")
        missing_db = check_gate_tenant(plist, package)
        self.assertEqual(missing_db["issues"], ["gate_db_unreadable"])

        no_tenant = Path(self.folder) / "notenant"
        shutil.copytree(package, no_tenant)
        prov = json.loads((no_tenant / "provenance.json").read_text(encoding="utf-8"))
        del prov["tenant_id"]
        (no_tenant / "provenance.json").write_text(json.dumps(prov), encoding="utf-8")
        self.assertEqual(
            check_gate_tenant(plist, no_tenant)["issues"],
            ["package_tenant_missing"],
        )

        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        before = os.stat(gs["db"])
        before_bytes = Path(gs["db"]).read_bytes()
        check_gate_tenant(plist, package)
        after = os.stat(gs["db"])
        after_bytes = Path(gs["db"]).read_bytes()
        self.assertEqual(before_bytes, after_bytes)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    def test_gt06_gate_init_package_tenant(self):
        gs, package, _built = self._build_package()
        db = Path(self.folder) / "cli-gate.sqlite"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                [
                    "gate-init",
                    "--db",
                    str(db),
                    "--principal",
                    gs["principal"],
                    "--tenant",
                    "synth-tenant",
                    "--package",
                    str(package),
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue().splitlines()[0])["ok"])
        bad_db = Path(self.folder) / "cli-bad.sqlite"
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            code2 = main(
                [
                    "gate-init",
                    "--db",
                    str(bad_db),
                    "--principal",
                    gs["principal"],
                    "--tenant",
                    "wrong-tenant",
                    "--package",
                    str(package),
                ]
            )
        self.assertEqual(code2, 2)
        self.assertEqual(
            json.loads(out2.getvalue().splitlines()[0])["error"],
            "tenant_package_mismatch",
        )
        self.assertFalse(bad_db.exists())

    def test_gt07_checklist_and_run_device_checks(self):
        gs, package, built = self._build_package()
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        manual = (package / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn(
            " mit --tenant synth-tenant --package <Paketverzeichnis>",
            manual,
        )
        managed_text = (package / "managed" / "config.yaml").read_text(encoding="utf-8")
        folder = Path(self.folder) / "device"
        folder.mkdir()
        hermes_home = make_hermes_home(folder, managed_text)
        installed = folder / "installed"
        shutil.copytree(package, installed)
        managed_dir = folder / "managed"
        managed_dir.mkdir()
        shutil.copyfile(package / "managed" / "config.yaml", managed_dir / "config.yaml")
        dump = folder / "dump.txt"
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
        self.assertIn("gate_tenant", result["checks"])
        self.assertTrue(result["checks"]["gate_tenant"]["ok"])


if __name__ == "__main__":
    unittest.main()
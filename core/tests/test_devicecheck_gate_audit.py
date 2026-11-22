import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.audit import GENESIS, verify
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import (
    check_gate_audit,
    parse_audit_anchor,
    run_device_checks,
)
from framework.gatekeeper import GateKeeper
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pin_provenance import write_pin_fixture
from test_appliance_plugin import PLUGIN_SOURCE, copy_plugin_tree
from test_devicecheck import CORE, make_hermes_home
from test_dumpcheck import GOOD_DUMP, SHA
from test_gate import sample_policy, sample_request

VALID_HASH = "sha256:" + "a" * 64


class DeviceCheckGateAuditTests(unittest.TestCase):
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

    def _build_package(self):
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
        return gs, package, built

    def _keeper(self, gs):
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        store = Store(gs["db"])
        return GateKeeper(store, FixedClock("2026-11-14T15:00:00Z")), store

    def test_gu01_parse_audit_anchor(self):
        self.assertEqual(parse_audit_anchor("3:" + VALID_HASH), (3, VALID_HASH))
        self.assertIsNone(parse_audit_anchor("0:" + VALID_HASH))
        self.assertIsNone(parse_audit_anchor("03:" + VALID_HASH))
        self.assertIsNone(parse_audit_anchor("x:" + VALID_HASH))
        self.assertIsNone(parse_audit_anchor("3:sha256:XYZ"))
        self.assertIsNone(parse_audit_anchor("3"))
        self.assertIsNone(parse_audit_anchor(""))

    def test_gu02_empty_chain(self):
        gs, package, _built = self._build_package()
        plist = package / "launchd" / (gs["label"] + ".plist")
        self._keeper(gs)
        result = check_gate_audit(plist, package)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["head"], GENESIS)

    def test_gu03_anchor_match(self):
        gs, package, _built = self._build_package()
        plist = package / "launchd" / (gs["label"] + ".plist")
        keeper, store = self._keeper(gs)
        for _ in range(3):
            keeper.check("hermes-a", sample_request(), sample_policy())
        conn = store.connect()
        event_hash = conn.execute(
            "SELECT event_hash FROM audit_events WHERE tenant_id=? AND sequence=2",
            ("synth-tenant",),
        ).fetchone()[0]
        conn.close()
        anchor = (2, event_hash)
        result = check_gate_audit(plist, package, anchor)
        self.assertTrue(result["ok"])
        self.assertEqual(result["anchor"], f"2:{event_hash}")
        self.assertEqual(result["count"], 3)

    def test_gu04_rollback_anchor_missing(self):
        gs, package, _built = self._build_package()
        plist = package / "launchd" / (gs["label"] + ".plist")
        keeper, _store = self._keeper(gs)
        keeper.check("hermes-a", sample_request(), sample_policy())
        keeper.check("hermes-a", sample_request(), sample_policy())
        backup = Path(self.folder) / "backup.sqlite"
        shutil.copyfile(gs["db"], backup)
        keeper.check("hermes-a", sample_request(), sample_policy())
        conn = sqlite3.connect(gs["db"])
        anchor_hash = conn.execute(
            "SELECT event_hash FROM audit_events WHERE tenant_id=? AND sequence=3",
            ("synth-tenant",),
        ).fetchone()[0]
        conn.close()
        shutil.copyfile(backup, gs["db"])
        store = Store(gs["db"])
        conn = store.connect()
        self.assertTrue(verify(conn, "synth-tenant")["ok"])
        conn.close()
        result = check_gate_audit(plist, package, (3, anchor_hash))
        self.assertEqual(result["issues"], ["audit_anchor_missing"])

    def test_gu05_tamper_chain_and_anchor(self):
        gs, package, _built = self._build_package()
        plist = package / "launchd" / (gs["label"] + ".plist")
        keeper, store = self._keeper(gs)
        for _ in range(3):
            keeper.check("hermes-a", sample_request(), sample_policy())
        conn = store.connect()
        anchor_hash = conn.execute(
            "SELECT event_hash FROM audit_events WHERE tenant_id=? AND sequence=2",
            ("synth-tenant",),
        ).fetchone()[0]
        conn.close()
        conn = sqlite3.connect(gs["db"])
        conn.execute("DROP TRIGGER audit_no_update")
        conn.execute("UPDATE audit_events SET outcome='hacked' WHERE sequence=2")
        conn.commit()
        conn.close()
        broken = check_gate_audit(plist, package)
        self.assertEqual(broken["issues"], ["audit_chain_broken"])
        anchored = check_gate_audit(plist, package, (2, anchor_hash))
        self.assertNotIn("audit_anchor_mismatch", anchored["issues"])
        conn = sqlite3.connect(gs["db"])
        conn.execute("UPDATE audit_events SET event_hash=? WHERE sequence=2", (VALID_HASH,))
        conn.commit()
        conn.close()
        anchor_only = check_gate_audit(plist, package, (2, anchor_hash))
        self.assertIn("audit_anchor_mismatch", anchor_only["issues"])

    def test_gu06_tenant_isolation_and_readonly(self):
        gs, package, _built = self._build_package()
        plist = package / "launchd" / (gs["label"] + ".plist")
        keeper, store = self._keeper(gs)
        store.add_principal("hermes-b", "other-tenant", "agent")
        keeper.check("hermes-a", sample_request(), sample_policy())
        keeper.check("hermes-b", sample_request(), sample_policy())
        keeper.check("hermes-b", sample_request(), sample_policy())
        result = check_gate_audit(plist, package)
        self.assertEqual(result["count"], 1)
        before_bytes = Path(gs["db"]).read_bytes()
        before_mtime = os.stat(gs["db"]).st_mtime_ns
        check_gate_audit(plist, package)
        self.assertEqual(Path(gs["db"]).read_bytes(), before_bytes)
        self.assertEqual(os.stat(gs["db"]).st_mtime_ns, before_mtime)

    def test_gu07_cli_audit_anchor(self):
        gs, package, built = self._build_package()
        keeper, store = self._keeper(gs)
        for _ in range(2):
            keeper.check("hermes-a", sample_request(), sample_policy())
        conn = store.connect()
        anchor_hash = conn.execute(
            "SELECT event_hash FROM audit_events WHERE tenant_id=? AND sequence=2",
            ("synth-tenant",),
        ).fetchone()[0]
        conn.close()
        anchor_text = f"2:{anchor_hash}"
        bad = io.StringIO()
        with contextlib.redirect_stdout(bad):
            code = main(
                [
                    "verify-device",
                    "--package",
                    str(package),
                    "--installed",
                    str(package),
                    "--package-hash",
                    built["package_hash"],
                    "--managed-dir",
                    str(package / "managed"),
                    "--hermes-home",
                    str(package),
                    "--plugin",
                    str(copy_plugin_tree()),
                    "--launchd",
                    str(package / "launchd" / (gs["label"] + ".plist")),
                    "--dump",
                    str(Path(self.folder) / "dump.txt"),
                    "--audit-anchor",
                    "kaputt",
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(bad.getvalue().splitlines()[0])["error"],
            "audit_anchor_invalid",
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
        dump = Path(self.folder) / "dump.txt"
        dump.write_text(GOOD_DUMP, encoding="utf-8")
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
                built["package_hash"],
                "--managed-dir",
                str(managed_dir),
                "--hermes-home",
                str(hermes_home),
                "--plugin",
                str(copy_plugin_tree()),
                "--launchd",
                str(package / "launchd" / (gs["label"] + ".plist")),
                "--dump",
                str(dump),
                "--home",
                tempfile.mkdtemp(dir="/tmp"),
                "--system-root",
                tempfile.mkdtemp(dir="/tmp"),
                "--audit-anchor",
                anchor_text,
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
            env=env,
        )
        body = json.loads(completed.stdout.splitlines()[0])
        self.assertEqual(body["checks"]["gate_audit"]["anchor"], anchor_text)


if __name__ == "__main__":
    unittest.main()
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.installcheck import verify_installation
from framework.launchd import gate_plist
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body


def gate_service():
    return {
        "label": "de.example.aaf-gate",
        "python": "/usr/bin/python3",
        "workdir": "/opt/aaf/core",
        "db": "/var/aaf/gate.sqlite",
        "socket_path": "/var/aaf/run/g.sock",
        "principal": "hermes-a",
        "user": "aafgate",
        "group": "aafsock",
        "mode": "0660",
    }


def gate_service_tmp(folder):
    gs = dict(gate_service())
    gs["db"] = str(Path(folder) / "gate.sqlite")
    return gs


class ApplianceLaunchdTests(unittest.TestCase):
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
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")

    def _provenance(self, package_dir):
        return json.loads((package_dir / "provenance.json").read_text(encoding="utf-8"))

    def test_al01_without_gate_service(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        self.assertFalse((target / "launchd").exists())
        prov = self._provenance(target)
        self.assertIsNone(prov["gate_launchd_label"])
        self.assertIsNone(prov["gate_plist_sha256"])

    def test_al02_with_gate_service(self):
        gs = gate_service()
        target = Path(self.folder) / "pkg-launchd"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        plist_path = target / "launchd" / "de.example.aaf-gate.plist"
        self.assertTrue(plist_path.is_file())
        raw = gate_plist(**gs)
        self.assertEqual(plist_path.read_text(encoding="utf-8").encode("utf-8"), raw)
        prov = self._provenance(target)
        self.assertEqual(prov["gate_plist_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(prov["gate_launchd_label"], gs["label"])

    def test_al03_root_user_invalid(self):
        gs = dict(gate_service())
        gs["user"] = "root"
        target = Path(self.folder) / "must-not-exist"
        with self.assertRaises(ConfigError) as ctx:
            build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        self.assertEqual(ctx.exception.code, "GATE_SERVICE_INVALID")
        self.assertFalse(target.exists())

    def test_al04_unknown_and_missing_keys(self):
        cases = [
            {**gate_service(), "env": "x"},
            {k: v for k, v in gate_service().items() if k != "user"},
        ]
        for case in cases:
            with self.subTest(case=case):
                target = Path(self.folder) / ("bad-" + str(len(case)))
                with self.assertRaises(ConfigError) as ctx:
                    build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=case)
                self.assertEqual(ctx.exception.code, "GATE_SERVICE_INVALID")
                self.assertFalse(target.exists())

    def test_al05_verify_changed_plist(self):
        gs = gate_service()
        package = Path(self.folder) / "pkg-src"
        built = build_appliance_package(self.plane, "konrad", "cfg-1", package, gate_service=gs)
        target = Path(self.folder) / "installed"
        shutil.copytree(package, target)
        plist = target / "launchd" / "de.example.aaf-gate.plist"
        text = plist.read_text(encoding="utf-8")
        plist.write_text(text.replace("KeepAlive", "KeepAliveX", 1), encoding="utf-8")
        issues = verify_installation(package, target, built["package_hash"])
        changed = [row for row in issues if row["path"] == "launchd/de.example.aaf-gate.plist"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["kind"], "changed")

    def test_al06_package_hash_stability(self):
        gs = gate_service()
        first = Path(self.folder) / "pkg-a"
        second = Path(self.folder) / "pkg-b"
        built_a = build_appliance_package(self.plane, "konrad", "cfg-1", first, gate_service=gs)
        built_b = build_appliance_package(self.plane, "konrad", "cfg-1", second, gate_service=gs)
        self.assertEqual(built_a["package_hash"], built_b["package_hash"])
        plain = Path(self.folder) / "pkg-plain"
        built_plain = build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        self.assertNotEqual(built_plain["package_hash"], built_a["package_hash"])


if __name__ == "__main__":
    unittest.main()
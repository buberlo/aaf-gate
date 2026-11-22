import plistlib
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _checklist, build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.launchd import gate_plist
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service
from test_appliance_package import initial_values, template_body


class ApplianceGateChecksTests(unittest.TestCase):
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

    def test_ac01_without_gate_service(self):
        target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertEqual(manual, _checklist())
        self.assertNotIn("## Gate-Dienst", manual)

    def test_ac02_with_gate_service_section(self):
        gs = gate_service()
        target = Path(self.folder) / "with-gate"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertTrue(manual.startswith(_checklist()))
        self.assertIn("## Gate service (startup chain)", manual)
        self.assertIn("/Library/LaunchDaemons/de.example.aaf-gate.plist", manual)
        self.assertIn("verify-launchd", manual)
        self.assertIn("the service default output", manual)
        self.assertIn(
            "5. man launchd.plist on this device describes the keys used "
            "KeepAlive, Label, ProgramArguments, UserName, WorkingDirectory.",
            manual,
        )
        self.assertNotIn("SYN:", manual)

    def test_ac03_log_path_and_hash_stability(self):
        gs = dict(gate_service())
        gs["log_path"] = "/var/log/aaf-gate.log"
        target = Path(self.folder) / "with-log"
        built = build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("/var/log/aaf-gate.log", manual)
        keys = ", ".join(sorted(plistlib.loads(gate_plist(**gs)).keys()))
        self.assertIn(
            "5. man launchd.plist on this device describes the keys used " + keys + ".",
            manual,
        )
        self.assertIn("StandardErrorPath", keys)
        self.assertIn("StandardOutPath", keys)
        again = Path(self.folder) / "with-log-2"
        built2 = build_appliance_package(self.plane, "konrad", "cfg-1", again, gate_service=gs)
        self.assertEqual(built["package_hash"], built2["package_hash"])


if __name__ == "__main__":
    unittest.main()
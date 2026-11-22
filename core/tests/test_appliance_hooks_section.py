import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service
from test_appliance_package import initial_values, template_body


class ApplianceHooksSectionTests(unittest.TestCase):
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

    def test_fh01_plain_checklist(self):
        target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("## Foreign hooks", manual)
        self.assertIn("verify-hooks ~/.hermes", manual)
        self.assertIn("~/.hermes/profiles/", manual)
        self.assertIn("plugins.enabled contains only aaf-gate", manual)
        bypass = manual.index("## Approvals without bypass")
        foreign = manual.index("## Foreign hooks")
        self.assertLess(bypass, foreign)

    def test_fh02_gated_section_order(self):
        target = Path(self.folder) / "gated"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gate_service())
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        bypass = manual.index("## Approvals without bypass")
        foreign = manual.index("## Foreign hooks")
        relay = manual.index("## NeMo relay and telemetry")
        gate = manual.index("## Gate service (startup chain)")
        pilot = manual.index("## Pilot 1:")
        self.assertLess(bypass, foreign)
        self.assertLess(foreign, relay)
        self.assertLess(relay, gate)
        self.assertLess(gate, pilot)

    def test_fh03_assumptions(self):
        target = Path(self.folder) / "assumptions"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        text = (target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn("HERMES_SAFE_MODE does not skip them", text)
        self.assertIn("modify", text)
        marker = "Not documented and therefore not claimed as function of this package:"
        verify_line = "That verify-hooks recognizes every YAML spelling"
        self.assertIn(verify_line, text)
        self.assertGreater(text.index(verify_line), text.index(marker))


if __name__ == "__main__":
    unittest.main()
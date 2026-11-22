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
from test_appliance_plugins_disabled import plugin_list_names


class ApplianceRelayTests(unittest.TestCase):
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

    def test_rl01_distribution_telemetry(self):
        target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        distribution = (target / "distribution/config.yaml").read_text(encoding="utf-8")
        managed = (target / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertIn("telemetry:", distribution)
        telemetry_idx = distribution.index("telemetry:")
        self.assertIn("shared_metrics:", distribution[telemetry_idx:])
        self.assertIn("enabled: false", distribution[telemetry_idx:])
        self.assertNotIn("telemetry", managed)
        enabled = plugin_list_names(distribution, "enabled")
        disabled = plugin_list_names(distribution, "disabled")
        self.assertEqual(enabled, ["aaf-gate"])
        self.assertEqual(disabled, ["disk-cleanup", "raft-platform", "security-guidance"])

    def test_rl02_checklist_section(self):
        target = Path(self.folder) / "gated"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gate_service())
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("## NeMo relay and telemetry", manual)
        self.assertIn("/etc/nemo-relay/plugins.toml", manual)
        self.assertIn("HERMES_NEMO_RELAY_PLUGINS_TOML", manual)
        self.assertIn("OTEL_SDK_DISABLED is true", manual)
        foreign = manual.index("## Foreign hooks")
        relay = manual.index("## NeMo relay and telemetry")
        gate = manual.index("## Gate service (startup chain)")
        self.assertLess(foreign, relay)
        self.assertLess(relay, gate)

    def test_rl03_assumptions(self):
        target = Path(self.folder) / "assumptions"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        text = (target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        marker = "Not documented and therefore not claimed as function of this package:"
        self.assertIn("plugins.enabled and plugins.disabled do not apply", text)
        self.assertIn("Middleware errors are fail-open", text)
        self.assertLess(text.index("plugins.enabled and plugins.disabled do not apply"), text.index(marker))
        self.assertLess(text.index("Middleware errors are fail-open"), text.index(marker))
        relay_audit = "That verify-hooks or pin-audit detect relay plugins."
        self.assertIn(relay_audit, text)
        self.assertGreater(text.index(relay_audit), text.index(marker))


if __name__ == "__main__":
    unittest.main()
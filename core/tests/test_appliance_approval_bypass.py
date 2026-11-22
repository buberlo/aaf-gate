import tempfile
import unittest
from pathlib import Path

from framework.appliance import DOCUMENTED_MANAGED_KEYS, build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service
from test_appliance_package import initial_values, template_body


class ApplianceApprovalBypassTests(unittest.TestCase):
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

    def test_ab01_single_query_mode_in_config(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        for rel in ("managed/config.yaml", "distribution/config.yaml"):
            text = (target / rel).read_text(encoding="utf-8")
            self.assertIn("single_query_mode: deny", text)
            lines = [line.strip() for line in text.splitlines() if line.strip().startswith(("mode:", "cron_mode:", "single_query_mode:", "unattended_mode:"))]
            self.assertEqual(
                lines,
                [
                    "mode: manual",
                    "cron_mode: deny",
                    "single_query_mode: deny",
                    "unattended_mode: deny",
                ],
            )
        self.assertEqual(len(DOCUMENTED_MANAGED_KEYS), 4)
        self.assertNotIn("approvals.single_query_mode", DOCUMENTED_MANAGED_KEYS)

    def test_ab02_checklist_section_order(self):
        plain = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        plain_manual = (plain / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("## Freigaben ohne Umgehung", plain_manual)
        self.assertIn("HERMES_YOLO_MODE", plain_manual)
        self.assertIn("plugin_rule:aaf:", plain_manual)
        with_gate = Path(self.folder) / "with-gate"
        build_appliance_package(self.plane, "konrad", "cfg-1", with_gate, gate_service=gate_service())
        gated = (with_gate / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("## Freigaben ohne Umgehung", gated)
        bypass = gated.index("## Freigaben ohne Umgehung")
        gate = gated.index("## Gate-Dienst (Startkette)")
        self.assertLess(bypass, gate)

    def test_ab03_assumptions_text(self):
        target = Path(self.folder) / "assumptions"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        text = (target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn("plugin_rule:<rule_key>", text)
        self.assertIn("HERMES_YOLO_MODE=1", text)
        marker = "Nicht belegt und deshalb nicht als Funktion dieses Pakets behauptet:"
        scoped = text.index("Dass der Managed Scope approvals.single_query_mode sperrt")
        self.assertGreater(scoped, text.index(marker))


if __name__ == "__main__":
    unittest.main()
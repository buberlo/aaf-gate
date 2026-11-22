import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework import hermes_mcp
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service
from test_appliance_package import initial_values, template_body


class AppliancePilot1ChecksTests(unittest.TestCase):
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

    def test_ap01_without_gate_service(self):
        target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertNotIn("## Pilot 1", manual)

    def test_ap02_pilot_section_content(self):
        gs = gate_service()
        target = Path(self.folder) / "with-gate"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        gate_index = manual.index("## Gate-Dienst (Startkette)")
        pilot_index = manual.index("## Pilot 1")
        self.assertGreater(pilot_index, gate_index)
        tool_lines = [
            f"- {hermes_mcp.registered_name('gmail', tool)}: {hermes_mcp.PILOT1_CLASSES[tool]}"
            for tool in sorted(hermes_mcp.GMAIL_MCP_TOOLS)
        ]
        for line in tool_lines:
            self.assertIn(line, manual)
        self.assertEqual(len(re.findall(r"^- mcp_gmail_[a-z_]+: ", manual, re.MULTILINE)), 10)
        self.assertIn("Ein Werkzeug zum Senden ist nicht dabei.", manual)
        self.assertIn("action_class_unknown", manual)
        self.assertNotIn("SYN:", manual)

    def test_ap03_matches_policy(self):
        gs = dict(gate_service())
        gs["server"] = "gmail"
        target = Path(self.folder) / "policy"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        policy = hermes_mcp.pilot1_policy("gmail")
        names_from_manual = set(re.findall(r"^- (mcp_gmail_[a-z_]+): ", manual, re.MULTILINE))
        policy_keys = {key[: -len(":call")] for key in policy["tool_classes"]}
        self.assertEqual(names_from_manual, policy_keys)
        for tool in sorted(hermes_mcp.GMAIL_MCP_TOOLS):
            name = hermes_mcp.registered_name("gmail", tool)
            expected = hermes_mcp.PILOT1_CLASSES[tool]
            row = f"- {name}: {expected}"
            self.assertIn(row, manual)
            self.assertEqual(policy["tool_classes"][name + ":call"], expected)

    def test_ap04_send_warning_when_patched(self):
        gs = gate_service()
        target = Path(self.folder) / "send-warn"
        with patch.dict(hermes_mcp.PILOT1_CLASSES, {"create_draft": "send"}, clear=False):
            build_appliance_package(self.plane, "konrad", "cfg-1", target, gate_service=gs)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("Achtung: Die Liste enthält ein Werkzeug zum Senden.", manual)
        self.assertNotIn("Ein Werkzeug zum Senden ist nicht dabei.", manual)


if __name__ == "__main__":
    unittest.main()
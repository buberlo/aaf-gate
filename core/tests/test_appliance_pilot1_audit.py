import contextlib
import io
import json
import re
import shlex
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _pilot1_checklist_section, build_appliance_package
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework import hermes_mcp
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service_tmp
from test_appliance_package import initial_values, template_body


def _legacy_pilot1_prefix(server):
    lines = [
        "## Pilot 1: Gmail via gate (Server " + server + ")",
        "",
        "Output lines to keep are entered in this package's ACCEPTANCE-RECORD.md.",
        "",
        "Hermes tool list shows exactly these Gmail tools, each with the class the gate expects:",
    ]
    for tool in sorted(hermes_mcp.GMAIL_MCP_TOOLS):
        name = hermes_mcp.registered_name(server, tool)
        klass = hermes_mcp.PILOT1_CLASSES[tool]
        lines.append("- " + name + ": " + klass)
    has_send = any(
        hermes_mcp.PILOT1_CLASSES[t] == "send" or "send" in t for t in hermes_mcp.GMAIL_MCP_TOOLS
    )
    if has_send:
        lines.append("Warning: The list includes a send tool.")
    else:
        lines.append("No send tool is included.")
    lines.extend(
        [
            "1. A call to a tool not on this list is denied by the gate with action_class_unknown. The audit line names this reason.",
            "2. A read and a draft each produce an audit line with the gate decision.",
            "3. In unattended operation a tool of class internal_write is denied.",
            "",
        ]
    )
    return "\n".join(lines)


def _pilot_section(manual):
    start = manual.index("## Pilot 1")
    return manual[start:]


class AppliancePilot1AuditTests(unittest.TestCase):
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
        self.gs = gate_service_tmp(self.folder)
        self.tenant = "synth-tenant"
        target = Path(self.folder) / "pkg"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            target,
            gate_service=self.gs,
        )
        self.manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.pilot = _pilot_section(self.manual)

    def test_pa01_points_present(self):
        self.assertIn("gate-audit --db " + self.gs["db"], self.pilot)
        self.assertIn("--tenant " + self.tenant, self.pilot)
        three = self.pilot.index(
            "3. In unattended operation a tool of class internal_write is denied."
        )
        four = self.pilot.index("4. `python3 -m framework.cli gate-audit")
        five = self.pilot.index("5. At end of pilot phase: `python3 -m framework.cli gate-audit")
        self.assertLess(three, four)
        self.assertLess(four, five)

    def test_pa02_prefix_unchanged(self):
        expected = _legacy_pilot1_prefix(self.gs.get("server", "gmail"))
        self.assertTrue(self.pilot.startswith(expected))
        current = _pilot1_checklist_section(self.gs.get("server", "gmail"), self.gs["db"], self.tenant)
        self.assertTrue(current.startswith(expected))

    def test_pa03_point4_command_runs(self):
        match = re.search(r"^4\. `([^`]+)`", self.pilot, re.MULTILINE)
        self.assertIsNotNone(match)
        command = match.group(1)
        prefix = "python3 -m framework.cli "
        self.assertTrue(command.startswith(prefix))
        argv = shlex.split(command[len(prefix) :])
        init_gate_db(self.gs["db"], self.gs["principal"], self.tenant, out=io.StringIO())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertTrue(body["ok"])

    def test_pa04_point5_summary_runs(self):
        match = re.search(r"^5\. At end of pilot phase: `([^`]+)`", self.pilot, re.MULTILINE)
        self.assertIsNotNone(match)
        command = match.group(1)
        command = command.replace("<start UTC>", "2026-01-01T00:00:00Z")
        command = command.replace("<end UTC>", "2027-01-01T00:00:00Z")
        prefix = "python3 -m framework.cli "
        self.assertTrue(command.startswith(prefix))
        argv = shlex.split(command[len(prefix) :])
        init_gate_db(self.gs["db"], self.gs["principal"], self.tenant, out=io.StringIO())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
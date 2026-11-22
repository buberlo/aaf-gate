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
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pilot1_audit import _pilot_section


class AppliancePilot1StopTests(unittest.TestCase):
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
        plain_target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain_target)
        self.manual_plain = (plain_target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")

    def test_ps01_stop_probe_steps_present(self):
        anchor = self.pilot.index("Grenze: Der Anker belegt nur")
        probe = self.pilot.index("### Stopp-Probe", anchor)
        self.assertGreater(probe, anchor)
        tail = self.pilot[probe:]
        for step in range(1, 10):
            self.assertEqual(len(re.findall(rf"^S{step}\.", tail, re.MULTILINE)), 1)

    def test_ps02_prefix_before_stop_unchanged(self):
        section = _pilot1_checklist_section(
            self.gs.get("server", "gmail"), self.gs["db"], self.tenant
        )
        prefix, _rest = section.split("### Stopp-Probe", 1)
        self.assertTrue(prefix.endswith(
            "Grenze: Der Anker belegt nur, dass das Gate-Audit bis zu seiner Sequenz nicht zurückgesetzt oder umgeschrieben wurde. Er belegt nicht, dass jeder Vorgang protokolliert wurde. Ereignisse, die vor dem Schreiben unterdrückt wurden, erkennt er nicht.\n\n"
        ))
        self.assertIn("4. `python3 -m framework.cli gate-audit --db ", prefix)
        self.assertIn("6. Bei jeder Abnahme:", prefix)
        stop_at = self.pilot.index("### Stopp-Probe")
        self.assertGreater(stop_at, self.pilot.index("6. Bei jeder Abnahme:"))

    def test_ps03_stop_commands_executable(self):
        block = self.pilot.split("### Stopp-Probe", 1)[1]
        matches = re.findall(r"^S(\d+)\. `([^`]+)`", block, re.MULTILINE)
        self.assertEqual(len(matches), 8)
        init_gate_db(self.gs["db"], self.gs["principal"], self.tenant, out=io.StringIO())
        prefix = "python3 -m framework.cli "
        expectations = {
            "1": (0, lambda b: b.get("ok") is True),
            "2": (0, lambda b: b.get("ok") is True),
            "3": (0, lambda b: b.get("stopped") is True),
            "4": (0, lambda b: b.get("stopped") is True),
            "6": (1, lambda b: b.get("reason") == "role_forbidden"),
            "7": (0, lambda b: b.get("ok") is True and b.get("stopped") is False),
            "8": (0, lambda b: b.get("stopped") is False),
            "9": (
                0,
                lambda b: b.get("ok") is True
                and b["summary"]["stops"]["stop"] == 1
                and b["summary"]["stops"]["resume"] == 1
                and b["summary"]["stops"]["rejected"] == 1
                and b["summary"]["stops"]["stopped"] is False,
            ),
        }
        for step, command in matches:
            command = command.replace("<Kunden-Principal>", "cust-stop")
            command = command.replace("<Betreiber-Principal>", "konrad-stop")
            self.assertTrue(command.startswith(prefix))
            argv = shlex.split(command[len(prefix) :])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(argv)
            body = json.loads(buf.getvalue().splitlines()[0])
            exp_code, check = expectations[step]
            self.assertEqual(code, exp_code, msg="step S" + step)
            self.assertTrue(check(body), msg="step S" + step)

    def test_ps04_no_stop_without_gate_service(self):
        self.assertNotIn("### Stopp-Probe", self.manual_plain)

    def test_ps05_docs(self):
        root = Path(__file__).resolve().parents[2]
        doc = (root / "docs/v0.5/168-stop-switch.md").read_text(encoding="utf-8")
        for token in ("gate-stop", "gate-resume", "gate-status", "role_forbidden"):
            self.assertIn(token, doc)
        self.assertIn("Not tested on device.", doc)
        mandant = (root / "docs/v0.4/04-TENANT-UPDATE-STOP.md").read_text(encoding="utf-8")
        self.assertIn("../v0.5/168-stop-switch.md", mandant)
        self.assertIn(
            "This switch is not implemented in this increment.",
            mandant,
        )
        readme = (root / "docs/v0.5/README.md").read_text(encoding="utf-8")
        self.assertIn("168-stop-switch.md", readme)


if __name__ == "__main__":
    unittest.main()
import contextlib
import io
import json
import re
import shlex
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import check_gate_audit, parse_audit_anchor
from framework.gatekeeper import GateKeeper
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pilot1_audit import _legacy_pilot1_prefix
from test_appliance_pin_provenance import write_pin_fixture
from test_appliance_plugin import PLUGIN_SOURCE
from test_gate import sample_policy, sample_request


class GateAuditAnchorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(self.db, "hermes-a", "demo-a", out=io.StringIO())
        self.store = Store(self.db)
        self.keeper = GateKeeper(self.store, FixedClock("2026-11-14T15:00:00Z"))

    def _anchor_run(self, tenant="demo-a"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", self.db, "--tenant", tenant, "--anchor"])
        body = json.loads(buf.getvalue().splitlines()[0])
        return code, body

    def _three_events(self):
        for _ in range(3):
            self.keeper.check("hermes-a", sample_request(), sample_policy())

    def _build_package(self):
        gs = gate_service_tmp(self.folder)
        gs["db"] = str(Path(self.folder) / "pilot-gate.sqlite")
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        package = Path(self.folder) / "pkg"
        build_appliance_package(
            plane,
            "konrad",
            "cfg-1",
            package,
            gate_service=gs,
            hermes_source=source,
            hermes_commit="a" * 40,
            plugin_dir=PLUGIN_SOURCE,
        )
        return gs, package

    def test_an01_anchor_output(self):
        self._three_events()
        code, body = self._anchor_run()
        self.assertEqual(code, 0)
        self.assertEqual(set(body.keys()), {"anchor", "ok", "tenant", "verify"})
        verify = body["verify"]
        expected = f"{verify['count']}:{verify['head']}"
        self.assertEqual(body["anchor"], expected)
        self.assertEqual(parse_audit_anchor(body["anchor"]), (verify["count"], verify["head"]))

    def test_an02_roundtrip_and_stale_anchor(self):
        gs, package = self._build_package()
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        keeper = GateKeeper(Store(gs["db"]), FixedClock("2026-11-14T15:00:00Z"))
        for _ in range(3):
            keeper.check("hermes-a", sample_request(), sample_policy())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                ["gate-audit", "--db", gs["db"], "--tenant", "synth-tenant", "--anchor"]
            )
        self.assertEqual(code, 0)
        anchor = parse_audit_anchor(json.loads(buf.getvalue().splitlines()[0])["anchor"])
        plist = package / "launchd" / (gs["label"] + ".plist")
        good = check_gate_audit(plist, package, anchor)
        self.assertTrue(good["ok"])
        self.assertNotIn("audit_anchor_missing", good["issues"])
        self.assertNotIn("audit_anchor_mismatch", good["issues"])
        keeper.check("hermes-a", sample_request(), sample_policy())
        stale = check_gate_audit(plist, package, anchor)
        self.assertNotIn("audit_anchor_missing", stale["issues"])
        self.assertNotIn("audit_anchor_mismatch", stale["issues"])

    def test_an03_empty_tenant(self):
        code, body = self._anchor_run()
        self.assertEqual(code, 0)
        self.assertIsNone(body["anchor"])
        self.assertTrue(body["ok"])

    def test_an04_broken_chain(self):
        self._three_events()
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TRIGGER audit_no_update")
        conn.execute("UPDATE audit_events SET outcome='hacked' WHERE sequence=2")
        conn.commit()
        conn.close()
        code, body = self._anchor_run()
        self.assertEqual(code, 1)
        self.assertFalse(body["ok"])
        self.assertIsNone(body["anchor"])

    def test_an05_conflicts_and_relative_db(self):
        cases = [
            ["--anchor", "--summary"],
            ["--anchor", "--summary", "--since", "2026-01-01T00:00:00Z"],
            ["--since", "2026-01-01T00:00:00Z"],
        ]
        for extra in cases:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main(["gate-audit", "--db", self.db, "--tenant", "demo-a", *extra])
            if extra == ["--since", "2026-01-01T00:00:00Z"]:
                self.assertEqual(code, 2)
                self.assertEqual(
                    json.loads(buf.getvalue().splitlines()[0])["error"],
                    "time_requires_summary",
                )
            else:
                self.assertEqual(code, 2)
                self.assertEqual(
                    json.loads(buf.getvalue().splitlines()[0])["error"],
                    "anchor_conflict",
                )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", "relative.sqlite", "--tenant", "demo-a", "--anchor"])
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(buf.getvalue().splitlines()[0])["error"],
            "db_path_not_absolute",
        )

    def test_an06_checklist_and_command(self):
        gs, package = self._build_package()
        manual = (package / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        pilot_start = manual.index("## Pilot 1")
        pilot = manual[pilot_start:]
        prefix = _legacy_pilot1_prefix(gs.get("server", "gmail"))
        self.assertTrue(pilot.startswith(prefix))
        expected_cmd = (
            "python3 -m framework.cli gate-audit --db "
            + gs["db"]
            + " --tenant synth-tenant --anchor"
        )
        self.assertIn(expected_cmd, pilot)
        self.assertIn("--audit-anchor <aufbewahrter Wert>", pilot)
        self.assertIn("Grenze: Der Anker belegt nur", pilot)
        match = re.search(r"^6\. Bei jeder Abnahme: `([^`]+)`", pilot, re.MULTILINE)
        self.assertIsNotNone(match)
        command = match.group(1)
        argv = shlex.split(command[len("python3 -m framework.cli ") :])
        init_gate_db(gs["db"], gs["principal"], "synth-tenant", out=io.StringIO())
        keeper = GateKeeper(Store(gs["db"]), FixedClock("2026-11-14T15:00:00Z"))
        keeper.check(gs["principal"], sample_request(), sample_policy())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        self.assertTrue(body["ok"])
        self.assertIsNotNone(body["anchor"])

    def test_an07_assumptions_line(self):
        gs, package = self._build_package()
        text = (package / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        line = (
            "- Dass das Gate-Audit vollständig ist. Ein Audit-Anker belegt nur, dass die Kette bis zu seiner Sequenz nicht zurückgesetzt oder umgeschrieben wurde. Ereignisse, die vor dem Schreiben unterdrückt wurden, erkennt er nicht."
        )
        self.assertIn(line, text)
        marker = "Nicht belegt und deshalb nicht als Funktion dieses Pakets behauptet:"
        block = text.split(marker, 1)[1].split("\n\n", 1)[0]
        self.assertTrue(block.strip().endswith(line))


if __name__ == "__main__":
    unittest.main()
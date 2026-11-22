import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _pilot1_checklist_section, build_appliance_package
from framework.baseline import compute
from framework.cli import init_gate_db, main
from framework.clock import Clock
from framework.configplane import ConfigPlane
from framework.gatekeeper import GateKeeper
from framework import hermes_adapter, hermes_mcp
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service_tmp
from test_appliance_package import initial_values, template_body
from test_appliance_pilot1_audit import _legacy_pilot1_prefix
from test_gate_audit_summary import SequenceClock
from test_hermes_adapter import build_observation
from test_pilot_baseline import _timesheet
POINT5_LEGACY_TAIL = (
    "Die Ausgabezeile wird zur Abnahme aufbewahrt. Sie enthält nur Zählwerte und keine Zeitersparnis."
)
POINT5_NEW_TAIL = (
    " In summary.observations sind gate_mismatch, gate_unseen und gate_ignored 0. Sonst ist dieser Punkt nicht bestanden, "
    "und die Ereignisse werden mit dem Kommando aus Punkt 4 gelesen."
)


class GateAuditObservationsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(self.db, "hermes-a", "demo-a", out=io.StringIO())
        self.store = Store(self.db)
        self.policy = hermes_mcp.pilot1_policy()
        self.read_tool = hermes_mcp.registered_name("gmail", "search_threads")

    def _run(self, *extra, tenant="demo-a"):
        base = ["gate-audit", "--db", self.db, "--tenant", tenant]
        base.extend(extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(base)
        body = json.loads(buf.getvalue().splitlines()[0])
        return code, body

    def _allow_decide(self, keeper, n=1):
        req = hermes_adapter.build_request(self.read_tool, {"query": "q"})
        for _ in range(n):
            keeper.check("hermes-a", req, self.policy)

    def test_ob01_mixed_counts(self):
        keeper = GateKeeper(self.store, Clock())
        self._allow_decide(keeper, 2)
        keeper.observe(
            "hermes-a",
            build_observation(
                "gmail",
                "gate_mismatch",
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
            ),
        )
        keeper.observe(
            "hermes-a",
            build_observation("gmail", "gate_unseen", None, "sha256:" + "d" * 64),
        )
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["total"], 2)
        self.assertEqual(
            body["summary"]["observations"],
            {"gate_mismatch": 1, "gate_unseen": 1, "gate_ignored": 0, "other": 0},
        )
        self.assertEqual(body["summary"]["by_class"]["read"]["allow"], 2)

    def test_ob02_empty_observations(self):
        keeper = GateKeeper(self.store, Clock())
        self._allow_decide(keeper, 1)
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertEqual(
            body["summary"]["observations"],
            {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0},
        )

    def test_ob03_time_window(self):
        stamps = [
            "2026-11-14T08:00:00Z",
            "2026-11-14T09:00:00Z",
            "2026-11-14T10:00:00Z",
            "2026-11-14T11:00:00Z",
        ]
        keeper = GateKeeper(self.store, SequenceClock(stamps))
        self._allow_decide(keeper, 1)
        keeper.observe(
            "hermes-a",
            build_observation(
                "gmail",
                "gate_mismatch",
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
            ),
        )
        keeper.observe(
            "hermes-a",
            build_observation("gmail", "gate_unseen", None, "sha256:" + "d" * 64),
        )
        code, body = self._run(
            "--summary",
            "--since",
            "2026-11-14T09:30:00Z",
            "--until",
            "2026-11-14T11:30:00Z",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            body["summary"]["observations"],
            {"gate_mismatch": 0, "gate_unseen": 1, "gate_ignored": 0, "other": 0},
        )

    def test_ob04_principal_kind_forbidden_other(self):
        self.store.add_principal("ada", "demo-a", "human")
        keeper = GateKeeper(self.store, Clock())
        obs = build_observation("gmail", "gate_unseen", None, "sha256:" + "2" * 64)
        keeper.observe("ada", obs)
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertEqual(
            body["summary"]["observations"],
            {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 1},
        )

    def test_ob05_checklist_and_baseline(self):
        gs = gate_service_tmp(self.folder)
        gs["db"] = str(Path(self.folder) / "pilot-gate.sqlite")
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, Clock())
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        package = Path(self.folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", package, gate_service=gs)
        manual = (package / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        pilot = manual[manual.index("## Pilot 1") :]
        prefix = _legacy_pilot1_prefix(gs.get("server", "gmail"))
        self.assertTrue(pilot.startswith(prefix))
        point5_match = re.search(r"^5\. Am Ende der Pilotphase:.*", pilot, re.MULTILINE)
        self.assertIsNotNone(point5_match)
        point5 = point5_match.group(0)
        self.assertIn(POINT5_LEGACY_TAIL, point5)
        self.assertTrue(point5.endswith(POINT5_NEW_TAIL))
        point6_match = re.search(
            r"^6\. Bei jeder Abnahme: `python3 -m framework.cli gate-audit --db "
            + re.escape(gs["db"])
            + r" --tenant synth-tenant --anchor`",
            pilot,
            re.MULTILINE,
        )
        self.assertIsNotNone(point6_match)
        grenze = "Grenze: Der Anker belegt nur, dass das Gate-Audit bis zu seiner Sequenz"
        self.assertIn(grenze, pilot)
        legacy_pilot = _pilot1_checklist_section(
            gs.get("server", "gmail"), gs["db"], "synth-tenant"
        ).replace(POINT5_NEW_TAIL, "")
        current_without_new = pilot.replace(POINT5_NEW_TAIL, "")
        for marker in (
            "4. `python3 -m framework.cli gate-audit --db ",
            "6. Bei jeder Abnahme:",
            grenze,
        ):
            legacy_idx = legacy_pilot.index(marker)
            current_idx = current_without_new.index(marker)
            legacy_line = legacy_pilot[legacy_idx:].split("\n", 1)[0]
            current_line = current_without_new[current_idx:].split("\n", 1)[0]
            self.assertEqual(legacy_line, current_line)
        keeper = GateKeeper(self.store, Clock())
        self._allow_decide(keeper, 2)
        keeper.observe(
            "hermes-a",
            build_observation(
                "gmail",
                "gate_mismatch",
                "sha256:" + "a" * 64,
                "sha256:" + "b" * 64,
            ),
        )
        keeper.observe(
            "hermes-a",
            build_observation("gmail", "gate_unseen", None, "sha256:" + "d" * 64),
        )
        _, body = self._run("--summary")
        timesheet = _timesheet(
            {"draft": [10] * 5, "internal_write": [], "read": [8] * 5}
        )
        result = compute(
            body,
            timesheet,
            5,
        )
        self.assertIn("classes", result)

    def test_ob06_other_tenant_excluded(self):
        init_gate_db(
            str(Path(self.folder) / "other.sqlite"),
            "hermes-b",
            "other-tenant",
            out=io.StringIO(),
        )
        other_store = Store(str(Path(self.folder) / "other.sqlite"))
        other_keeper = GateKeeper(other_store, Clock())
        other_keeper.observe(
            "hermes-b",
            build_observation(
                "gmail",
                "gate_mismatch",
                "sha256:" + "e" * 64,
                "sha256:" + "f" * 64,
            ),
        )
        keeper = GateKeeper(self.store, Clock())
        self._allow_decide(keeper, 1)
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertEqual(
            body["summary"]["observations"],
            {"gate_mismatch": 0, "gate_unseen": 0, "gate_ignored": 0, "other": 0},
        )


if __name__ == "__main__":
    unittest.main()
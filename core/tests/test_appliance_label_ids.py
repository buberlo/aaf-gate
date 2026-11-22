import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _managed_config, build_appliance_package, dry_run_lines
from framework.configplane import ConfigError
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import managed_socket_path
from framework import hermes_adapter
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service
from test_appliance_package import initial_values, template_body

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "adapters/hermes/aaf-gate/__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("aaf_gate_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApplianceLabelIdsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(self.store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(self.store, (("konrad", "synth-tenant", "human", ("author",)),))
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        self.gs = gate_service()

    def test_li01_managed_with_ids(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            target,
            gate_service=self.gs,
            label_ids=["Label_2", "Label_1"],
        )
        managed = (target / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertTrue(
            managed.endswith(
                '        socket_path: "/var/aaf/run/g.sock"\n'
                '        label_ids: ["Label_1", "Label_2"]\n'
            )
        )

    def test_li02_managed_default_empty(self):
        gated = Path(self.folder) / "gated"
        build_appliance_package(self.plane, "konrad", "cfg-1", gated, gate_service=self.gs)
        managed = (gated / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertTrue(managed.endswith('        label_ids: []\n'))
        plain = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        self.assertEqual(
            (plain / "managed/config.yaml").read_text(encoding="utf-8"),
            _managed_config(),
        )

    def test_li03_without_gate_rejected(self):
        target = Path(self.folder) / "nope"
        with self.assertRaises(ConfigError) as caught:
            build_appliance_package(
                self.plane,
                "konrad",
                "cfg-1",
                target,
                label_ids=["Label_1"],
            )
        self.assertEqual(caught.exception.code, "LABEL_IDS_WITHOUT_GATE")
        self.assertFalse(target.exists())

    def test_li04_invalid_rejected(self):
        cases = [
            [],
            ["INBOX"],
            ["TRASH"],
            ["CATEGORY_SOCIAL"],
            ["Label_1", "Label_1"],
            [""],
            [1],
            "Label_1",
            ["x" * 129],
        ]
        for index, value in enumerate(cases):
            with self.subTest(value=value):
                target = Path(self.folder) / f"bad-{index}"
                with self.assertRaises(ConfigError) as caught:
                    build_appliance_package(
                        self.plane,
                        "konrad",
                        "cfg-1",
                        target,
                        gate_service=self.gs,
                        label_ids=value,
                    )
                self.assertEqual(caught.exception.code, "LABEL_IDS_INVALID")
                self.assertFalse(target.exists())

    def test_li05_provenance_and_dry_run(self):
        with_ids = Path(self.folder) / "ids"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            with_ids,
            gate_service=self.gs,
            label_ids=["Label_2", "Label_1"],
        )
        prov_ids = json.loads((with_ids / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(prov_ids["label_ids"], ["Label_1", "Label_2"])
        empty = Path(self.folder) / "empty"
        build_appliance_package(self.plane, "konrad", "cfg-1", empty, gate_service=self.gs)
        prov_empty = json.loads((empty / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(prov_empty["label_ids"], [])
        plain = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        prov_plain = json.loads((plain / "provenance.json").read_text(encoding="utf-8"))
        self.assertIsNone(prov_plain["label_ids"])
        completed = subprocess.run(
            [sys.executable, str(with_ids / "provision_dry_run.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("label_ids Label_1,Label_2", completed.stdout)
        lines_empty = dry_run_lines(empty)
        self.assertIn("label_ids -", lines_empty)
        lines_plain = dry_run_lines(plain)
        self.assertIn("label_ids -", lines_plain)

    def test_li06_plugin_roundtrip(self):
        plugin = load_plugin()
        with_ids = Path(self.folder) / "ids"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            with_ids,
            gate_service=self.gs,
            label_ids=["Label_2", "Label_1"],
        )
        managed = (with_ids / "managed/config.yaml").read_text(encoding="utf-8")
        line = [row for row in managed.splitlines() if row.strip().startswith("label_ids:")][0]
        payload = line.split(":", 1)[1].strip()
        parsed = json.loads(payload)
        self.assertEqual(
            hermes_adapter.label_allowlist(parsed),
            frozenset({"Label_1", "Label_2"}),
        )
        self.assertEqual(plugin.label_allowlist(parsed), frozenset({"Label_1", "Label_2"}))
        empty = Path(self.folder) / "empty"
        build_appliance_package(self.plane, "konrad", "cfg-1", empty, gate_service=self.gs)
        managed_empty = (empty / "managed/config.yaml").read_text(encoding="utf-8")
        line_empty = [row for row in managed_empty.splitlines() if row.strip().startswith("label_ids:")][0]
        parsed_empty = json.loads(line_empty.split(":", 1)[1].strip())
        self.assertIsNone(hermes_adapter.label_allowlist(parsed_empty))
        self.assertIsNone(plugin.label_allowlist(parsed_empty))

    def test_li07_checklist_and_assumptions(self):
        with_ids = Path(self.folder) / "ids"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            with_ids,
            gate_service=self.gs,
            label_ids=["Label_2", "Label_1"],
        )
        manual = (with_ids / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("mit genau den IDs Label_1, Label_2", manual)
        self.assertIn("settings.label_ids '[]'` wird als verwalteter Schlüssel verweigert", manual)
        empty = Path(self.folder) / "empty"
        build_appliance_package(self.plane, "konrad", "cfg-1", empty, gate_service=self.gs)
        manual_empty = (empty / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("settings.label_ids: []", manual_empty)
        self.assertIn("settings.label_ids '[]'` wird als verwalteter Schlüssel verweigert", manual_empty)
        plain = Path(self.folder) / "plain2"
        build_appliance_package(self.plane, "konrad", "cfg-1", plain)
        manual_plain = (plain / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertNotIn("settings.label_ids", manual_plain)
        assumptions = (with_ids / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        self.assertIn(
            "plugins.entries.aaf-gate.settings.label_ids im Managed Scope, ohne Paketparameter als leere Liste",
            assumptions,
        )
        marker = "Nicht belegt und deshalb nicht als Funktion dieses Pakets behauptet:"
        merge_line = (
            "Ob Hermes eine Liste im Managed Scope als ein Blatt behandelt oder mit einer Liste der Benutzerkonfiguration zusammenführt"
        )
        self.assertIn(merge_line, assumptions)
        self.assertLess(assumptions.index(marker), assumptions.index(merge_line))

    def test_li08_socket_parser_unaffected(self):
        with_ids = Path(self.folder) / "ids"
        build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            with_ids,
            gate_service=self.gs,
            label_ids=["Label_1"],
        )
        text = (with_ids / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertEqual(managed_socket_path(text), "/var/aaf/run/g.sock")
        empty = Path(self.folder) / "empty"
        build_appliance_package(self.plane, "konrad", "cfg-1", empty, gate_service=self.gs)
        text_empty = (empty / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertEqual(managed_socket_path(text_empty), "/var/aaf/run/g.sock")


if __name__ == "__main__":
    unittest.main()
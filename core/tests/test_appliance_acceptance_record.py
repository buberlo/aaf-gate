import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from framework.appliance import ACCEPTANCE_STEP_ORDER, build_appliance_package
from framework.version import VERSION
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service_tmp
from test_appliance_package import initial_values, template_body


def _build(folder, gate=True, label_ids=None):
    store = Store(str(Path(folder) / "cfg.sqlite"))
    plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
    seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
    plane.register_template("konrad", template_body())
    plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
    target = Path(folder) / "pkg"
    kwargs = {}
    if gate:
        kwargs["gate_service"] = gate_service_tmp(folder)
        if label_ids is not None:
            kwargs["label_ids"] = label_ids
    build_appliance_package(plane, "konrad", "cfg-1", target, **kwargs)
    return target


class ApplianceAcceptanceRecordTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")

    def test_ar01_present_with_gate(self):
        package = _build(self.folder)
        path = package / "ACCEPTANCE-RECORD.md"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Pilot 1 acceptance record"))
        self.assertIn("Status: not executed", text)

    def test_ar02_absent_without_gate(self):
        plain_root = tempfile.mkdtemp(dir="/tmp")
        package = _build(plain_root, gate=False)
        self.assertFalse((package / "ACCEPTANCE-RECORD.md").exists())
        manual = (package / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertNotIn("ACCEPTANCE-RECORD.md", manual)

    def test_ar03_package_section_matches_provenance(self):
        package = _build(self.folder)
        provenance = json.loads((package / "provenance.json").read_text(encoding="utf-8"))
        record = (package / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        self.assertIn("release_hash: " + provenance["release_hash"], record)
        self.assertIn("tenant_id: " + provenance["tenant_id"], record)
        self.assertIn("label_ids: -", record)
        labeled = tempfile.mkdtemp(dir="/tmp")
        with_ids = _build(labeled, label_ids=["Inbox", "Wichtig"])
        prov2 = json.loads((with_ids / "provenance.json").read_text(encoding="utf-8"))
        rec2 = (with_ids / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        self.assertIn("label_ids: Inbox, Wichtig", rec2)
        self.assertEqual(prov2["label_ids"], ["Inbox", "Wichtig"])

    def test_ar04_step_order(self):
        package = _build(self.folder)
        record = (package / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        section = record.split("## Check steps", 1)[1].split("## Not covered", 1)[0]
        found = re.findall(r"^- ([^:]+):", section, re.MULTILINE)
        self.assertEqual(found, list(ACCEPTANCE_STEP_ORDER))
        self.assertEqual(len(found), len(set(found)))

    def test_ar05_manual_mentions_record(self):
        manual = (_build(self.folder) / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("ACCEPTANCE-RECORD.md", manual)

    def test_ar06_stable_and_no_build_host_paths(self):
        package_a = _build(self.folder)
        folder_b = tempfile.mkdtemp(dir="/tmp")
        package_b = _build(folder_b)
        text_a = (package_a / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        text_b = (package_b / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        self.assertEqual(text_a, text_b)
        self.assertNotIn(self.folder, text_a)
        self.assertNotIn("/tmp/", text_a)

    def test_ar07_docs_section(self):
        root = Path(__file__).resolve().parents[2]
        doc = (root / "docs/v0.4/06-APPLIANCE-TRACK-A.md").read_text(encoding="utf-8")
        start = doc.index("## Manual acceptance on Mac mini")
        rest = doc[start:]
        next_heading = rest.find("\n## ", len("## Manual acceptance on Mac mini"))
        section = rest if next_heading < 0 else rest[:next_heading]
        self.assertIn("ACCEPTANCE-RECORD.md", section)
        self.assertIn("verify-device", section)
        self.assertNotIn("CuaDriver", section)
        self.assertNotIn("Eine Mail geht erst", section)

    def test_ar08_step_order_backup(self):
        order = list(ACCEPTANCE_STEP_ORDER)
        self.assertLess(order.index("S8"), order.index("S9"))
        self.assertLess(order.index("S9"), order.index("gate-audit-anchor"))
        self.assertLess(order.index("gate-audit-anchor"), order.index("gate-backup"))
        self.assertLess(order.index("gate-backup"), order.index("gate-restore"))
        self.assertLess(order.index("gate-restore"), order.index("verify-device"))
        record = (_build(self.folder) / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        for token in ("- S9:", "- gate-backup:", "- gate-restore:"):
            self.assertIn(token, record)

    def test_ar09_not_covered_text(self):
        record = (_build(self.folder) / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        block = record.split("## Not covered", 1)[1].split("## Result", 1)[0]
        self.assertIn("Rebuild", block)
        self.assertNotIn("folgen mit V1-4", block)

    def test_ar10_checklist_backup_probe(self):
        gs = gate_service_tmp(self.folder)
        package = _build(self.folder)
        manual = (package / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertLess(manual.index("### Backup probe"), manual.index("### Device probes"))
        self.assertIn("B1.", manual)
        self.assertIn("B2.", manual)
        self.assertIn("B3.", manual)
        self.assertIn("gate-backup --db " + gs["db"], manual)
        self.assertIn("--expect-anchor " + "synth-tenant" + "=", manual)
        self.assertIn(".manifest.json", manual)
        plain_root = tempfile.mkdtemp(dir="/tmp")
        plain_manual = (_build(plain_root, gate=False) / "MANUAL-MAC-CHECKS.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("### Backup probe", plain_manual)

    def test_ar12_core_version_line(self):
        record = (_build(self.folder) / "ACCEPTANCE-RECORD.md").read_text(encoding="utf-8")
        block = record.split("## Package", 1)[1].split("## Device", 1)[0]
        lines = [line for line in block.strip().splitlines() if line]
        release_idx = next(i for i, line in enumerate(lines) if line.startswith("release_hash: "))
        self.assertEqual(lines[release_idx + 1], "core_version: " + VERSION)

    def test_ar13_core_version_in_package_hash(self):
        folder_a = tempfile.mkdtemp(dir="/tmp")
        folder_b = tempfile.mkdtemp(dir="/tmp")
        folder_c = tempfile.mkdtemp(dir="/tmp")
        gate_service = gate_service_tmp(self.folder)
        built_a = self._built_hash(folder_a, gate_service)
        with mock.patch("framework.appliance.VERSION", "0.0.0rc0"):
            built_b = self._built_hash(folder_b, gate_service)
        built_c = self._built_hash(folder_c, gate_service)
        self.assertNotEqual(built_a, built_b)
        self.assertEqual(built_a, built_c)

    def _built_hash(self, folder, gate_service):
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        target = Path(folder) / "pkg"
        return build_appliance_package(
            plane,
            "konrad",
            "cfg-1",
            target,
            gate_service=gate_service,
        )["package_hash"]

    def test_ar11_docs_reinstall_section(self):
        root = Path(__file__).resolve().parents[2]
        doc = (root / "docs/v0.4/06-APPLIANCE-TRACK-A.md").read_text(encoding="utf-8")
        start = doc.index("## Rebuild from last release")
        section = doc[start:]
        for token in (
            "gate-restore",
            "--expect-anchor",
            "db_exists",
            "--audit-anchor",
            "verify-install",
        ):
            self.assertIn(token, section)
        manual_start = doc.index("## Manual acceptance on Mac mini")
        manual_end = doc.index("## Rebuild from last release")
        manual = doc[manual_start:manual_end]
        self.assertNotIn("folgt.", manual)
        self.assertIn("B1–B3", manual)


if __name__ == "__main__":
    unittest.main()
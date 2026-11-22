import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.dumpcheck import verify_dump_commit
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body
from test_appliance_pin_provenance import write_pin_fixture

CORE = Path(__file__).resolve().parents[1]
SHA = "3b421578588243f920886e6fe79a6faa87ea026e"
GOOD_DUMP = (
    "--- hermes dump ---\n"
    "version:          0.23.1 (2026.11.1) [3b421578]\n"
    "os: x\n"
)


class DumpCheckTests(unittest.TestCase):
    def test_dc01_match(self):
        self.assertEqual(verify_dump_commit(GOOD_DUMP, SHA), [])

    def test_dc02_mismatch(self):
        self.assertEqual(verify_dump_commit(GOOD_DUMP, SHA), [])
        bad = GOOD_DUMP.replace("[3b421578]", "[deadbeef]")
        self.assertEqual(verify_dump_commit(bad, SHA), ["commit_mismatch"])

    def test_dc03_parse_errors(self):
        unknown = GOOD_DUMP.replace("[3b421578]", "[(unknown)]")
        self.assertEqual(verify_dump_commit(unknown, SHA), ["dump_commit_unknown"])
        self.assertEqual(
            verify_dump_commit("no version here\n", SHA),
            ["dump_version_missing"],
        )

    def test_dc04_provenance_commit(self):
        self.assertEqual(
            verify_dump_commit(GOOD_DUMP, None),
            ["provenance_commit_missing"],
        )
        self.assertEqual(
            verify_dump_commit(GOOD_DUMP, "abc"),
            ["provenance_commit_missing"],
        )

    def test_dc05_cli(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        package = Path(folder) / "pkg"
        build_appliance_package(
            plane,
            "konrad",
            "cfg-1",
            package,
            hermes_source=source,
            hermes_commit=SHA,
        )
        dump_path = Path(folder) / "dump-good.txt"
        dump_path.write_text(GOOD_DUMP, encoding="utf-8")
        ok = subprocess.run(
            [
                sys.executable,
                "-m",
                "framework.cli",
                "verify-hermes-dump",
                str(dump_path),
                "--package",
                str(package),
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ok.returncode, 0)
        self.assertTrue(json.loads(ok.stdout.splitlines()[0])["ok"])
        self.assertNotIn("os: x", ok.stdout)
        bad_dump = Path(folder) / "dump-bad.txt"
        bad_dump.write_text(GOOD_DUMP.replace("[3b421578]", "[deadbeef]"), encoding="utf-8")
        fail = subprocess.run(
            [
                sys.executable,
                "-m",
                "framework.cli",
                "verify-hermes-dump",
                str(bad_dump),
                "--package",
                str(package),
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(fail.returncode, 1)
        self.assertEqual(
            json.loads(fail.stdout.splitlines()[0])["issues"],
            ["commit_mismatch"],
        )
        missing_dump = subprocess.run(
            [
                sys.executable,
                "-m",
                "framework.cli",
                "verify-hermes-dump",
                str(Path(folder) / "missing.txt"),
                "--package",
                str(package),
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing_dump.returncode, 2)
        self.assertEqual(
            json.loads(missing_dump.stdout.splitlines()[0]),
            {"error": "dump_unreadable", "ok": False},
        )
        no_prov = subprocess.run(
            [
                sys.executable,
                "-m",
                "framework.cli",
                "verify-hermes-dump",
                str(dump_path),
                "--package",
                str(Path(folder) / "empty"),
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(no_prov.returncode, 2)
        self.assertEqual(
            json.loads(no_prov.stdout.splitlines()[0]),
            {"error": "provenance_unreadable", "ok": False},
        )

    def test_dc06_appliance_text(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        target = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", target)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("verify-hermes-dump /tmp/hermes-dump.txt", manual)
        assumptions = (target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        marker = "Not documented and therefore not claimed as function of this package:"
        line = "verify-hermes-dump only parses the version: line"
        self.assertIn(line, assumptions)
        self.assertGreater(assumptions.index(line), assumptions.index(marker))


if __name__ == "__main__":
    unittest.main()
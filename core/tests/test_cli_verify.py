import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_install_check import InstallCheckLab

CORE = Path(__file__).resolve().parents[1]


class CliVerifyTests(unittest.TestCase):
    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "framework.cli", *extra],
            cwd=CORE,
            capture_output=True,
            text=True,
        )

    def test_vi01_clean_install_ok(self):
        lab = InstallCheckLab()
        completed = self.run_cli(
            "verify-install",
            str(lab.package_dir),
            str(lab.installed),
            "--package-hash",
            lab.built["package_hash"],
        )
        self.assertEqual(completed.returncode, 0)
        body = json.loads(completed.stdout)
        self.assertEqual(body, {"issues": [], "ok": True})

    def test_vi02_changed_file_exit_one(self):
        lab = InstallCheckLab()
        path = lab.installed / "managed" / "config.yaml"
        path.write_text("approvals:\n  mode: smart\n", encoding="utf-8")
        completed = self.run_cli("verify-install", str(lab.package_dir), str(lab.installed))
        self.assertEqual(completed.returncode, 1)
        body = json.loads(completed.stdout)
        self.assertFalse(body["ok"])
        self.assertEqual(body["issues"], [{"kind": "changed", "path": "managed/config.yaml"}])

    def test_vi03_package_hash_mismatch(self):
        lab = InstallCheckLab()
        completed = self.run_cli(
            "verify-install",
            str(lab.package_dir),
            str(lab.installed),
            "--package-hash",
            "sha256:0",
        )
        self.assertEqual(completed.returncode, 1)
        body = json.loads(completed.stdout)
        self.assertFalse(body["ok"])
        self.assertEqual(body["issues"], [{"kind": "package_hash_mismatch", "path": ""}])

    def test_vi04_missing_package_dir_exit_two(self):
        lab = InstallCheckLab()
        missing = Path(lab.folder) / "missing-package"
        completed = self.run_cli("verify-install", str(missing), str(lab.installed))
        self.assertEqual(completed.returncode, 2)
        body = json.loads(completed.stdout)
        self.assertEqual(body, {"error": "PACKAGE_INVALID", "ok": False})

    def test_vi05_missing_arguments_exit_two(self):
        completed = self.run_cli("verify-install")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
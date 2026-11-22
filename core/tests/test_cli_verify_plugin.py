import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.plugincheck import plugin_digest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO_ROOT / "adapters/hermes/aaf-gate"


def copy_plugin_tree():
    dest = Path(tempfile.mkdtemp(dir="/tmp")) / "aaf-gate"
    dest.mkdir()
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copyfile(PLUGIN_SOURCE / name, dest / name)
    return dest


class CliVerifyPluginTests(unittest.TestCase):
    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "framework.cli", *extra],
            cwd=CORE,
            capture_output=True,
            text=True,
        )

    def test_vp01_digest_matches_module(self):
        completed = self.run_cli("plugin-digest", str(PLUGIN_SOURCE))
        self.assertEqual(completed.returncode, 0)
        body = json.loads(completed.stdout)
        self.assertTrue(body["ok"])
        self.assertEqual(body["digest"], plugin_digest(PLUGIN_SOURCE))

    def test_vp02_clean_copy_ok(self):
        copy = copy_plugin_tree()
        digest = plugin_digest(PLUGIN_SOURCE)
        completed = self.run_cli("verify-plugin", str(copy), "--digest", digest)
        self.assertEqual(completed.returncode, 0)
        body = json.loads(completed.stdout)
        self.assertEqual(body, {"issues": [], "ok": True})

    def test_vp03_modified_exit_one(self):
        copy = copy_plugin_tree()
        digest = plugin_digest(PLUGIN_SOURCE)
        init = copy / "__init__.py"
        init.write_text(init.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        completed = self.run_cli("verify-plugin", str(copy), "--digest", digest)
        self.assertEqual(completed.returncode, 1)
        body = json.loads(completed.stdout)
        self.assertFalse(body["ok"])
        self.assertEqual(body["issues"], [{"kind": "digest_mismatch", "path": ""}])

    def test_vp04_missing_dir_exit_one(self):
        missing = Path(tempfile.mkdtemp(dir="/tmp")) / "missing-aaf"
        digest = plugin_digest(PLUGIN_SOURCE)
        completed = self.run_cli("verify-plugin", str(missing), "--digest", digest)
        self.assertEqual(completed.returncode, 1)
        body = json.loads(completed.stdout)
        self.assertEqual(body["issues"], [{"kind": "missing", "path": ""}])

    def test_vp05_digest_invalid_exit_two(self):
        empty = Path(tempfile.mkdtemp(dir="/tmp")) / "empty"
        empty.mkdir()
        completed = self.run_cli("plugin-digest", str(empty))
        self.assertEqual(completed.returncode, 2)
        body = json.loads(completed.stdout)
        self.assertEqual(body, {"error": "PLUGIN_INVALID", "ok": False})

    def test_vp06_digest_required(self):
        copy = copy_plugin_tree()
        completed = self.run_cli("verify-plugin", str(copy))
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn('"ok": true', completed.stdout)


if __name__ == "__main__":
    unittest.main()
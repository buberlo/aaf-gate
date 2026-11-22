import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.plugincheck import plugin_digest
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPO_ROOT / "adapters/hermes/aaf-gate"


def copy_plugin_tree():
    dest = Path(tempfile.mkdtemp(dir="/tmp")) / "aaf-gate"
    dest.mkdir()
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copyfile(PLUGIN_SOURCE / name, dest / name)
    return dest


class AppliancePluginTests(unittest.TestCase):
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

    def run_cli(self, *extra):
        return subprocess.run(
            [sys.executable, "-m", "framework.cli", *extra],
            cwd=CORE,
            capture_output=True,
            text=True,
        )

    def _provenance(self, package_dir):
        return json.loads((package_dir / "provenance.json").read_text(encoding="utf-8"))

    def test_ap01_default_digest_none(self):
        target = Path(self.folder) / "pkg"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        self.assertIsNone(self._provenance(target).get("hermes_plugin_digest"))

    def test_ap02_digest_recorded(self):
        target = Path(self.folder) / "pkg-digest"
        build_appliance_package(self.plane, "konrad", "cfg-1", target, plugin_dir=PLUGIN_SOURCE)
        self.assertEqual(
            self._provenance(target)["hermes_plugin_digest"],
            plugin_digest(PLUGIN_SOURCE),
        )

    def test_ap03_invalid_plugin_dir_no_package(self):
        empty = Path(tempfile.mkdtemp(dir="/tmp")) / "empty-plugin"
        empty.mkdir()
        target = Path(self.folder) / "must-not-exist"
        with self.assertRaises(ConfigError) as ctx:
            build_appliance_package(self.plane, "konrad", "cfg-1", target, plugin_dir=empty)
        self.assertEqual(ctx.exception.code, "PLUGIN_INVALID")
        self.assertFalse(target.exists())

    def test_ap04_cli_package_clean(self):
        package = Path(self.folder) / "pkg-cli"
        build_appliance_package(self.plane, "konrad", "cfg-1", package, plugin_dir=PLUGIN_SOURCE)
        copy = copy_plugin_tree()
        completed = self.run_cli("verify-plugin", str(copy), "--package", str(package))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout), {"issues": [], "ok": True})
        init = copy / "__init__.py"
        init.write_text(init.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        broken = self.run_cli("verify-plugin", str(copy), "--package", str(package))
        self.assertEqual(broken.returncode, 1)
        body = json.loads(broken.stdout)
        self.assertEqual(body["issues"], [{"kind": "digest_mismatch", "path": ""}])

    def test_ap05_cli_package_without_digest(self):
        package = Path(self.folder) / "pkg-nodigest"
        build_appliance_package(self.plane, "konrad", "cfg-1", package)
        copy = copy_plugin_tree()
        completed = self.run_cli("verify-plugin", str(copy), "--package", str(package))
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            json.loads(completed.stdout),
            {"error": "PLUGIN_DIGEST_UNSET", "ok": False},
        )
        bare = Path(self.folder) / "bare-package"
        bare.mkdir()
        (bare / "rollback.json").write_text("{}", encoding="utf-8")
        completed2 = self.run_cli("verify-plugin", str(copy), "--package", str(bare))
        self.assertEqual(completed2.returncode, 2)
        self.assertEqual(
            json.loads(completed2.stdout),
            {"error": "PLUGIN_DIGEST_UNSET", "ok": False},
        )

    def test_ap06_cli_digest_and_package_exclusive(self):
        package = Path(self.folder) / "pkg-both"
        build_appliance_package(self.plane, "konrad", "cfg-1", package, plugin_dir=PLUGIN_SOURCE)
        copy = copy_plugin_tree()
        digest = plugin_digest(PLUGIN_SOURCE)
        completed = self.run_cli(
            "verify-plugin",
            str(copy),
            "--digest",
            digest,
            "--package",
            str(package),
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn('"ok": true', completed.stdout)


if __name__ == "__main__":
    unittest.main()
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from framework.hookcheck import verify_hermes_home


class HookCheckPluginsTests(unittest.TestCase):
    def home(self):
        root = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(root.cleanup)
        path = Path(root.name) / "hermes"
        os.makedirs(path)
        os.chmod(path, 0o755)
        return path

    def write_plugin_yaml(self, plugin_dir, text):
        plugin_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(plugin_dir, 0o755)
        yaml_path = plugin_dir / "plugin.yaml"
        yaml_path.write_text(text, encoding="utf-8")
        os.chmod(yaml_path, 0o644)

    def test_pd01_foreign_plugin(self):
        home = self.home()
        plugins = home / "plugins"
        os.makedirs(plugins)
        os.chmod(plugins, 0o755)
        other = plugins / "other"
        self.write_plugin_yaml(other, "name: other\n")
        gate = plugins / "aaf-gate"
        self.write_plugin_yaml(gate, "name: aaf-gate\nversion: 0.5.0\n")
        self.assertEqual(verify_hermes_home(home), ["foreign_plugin:plugins/other"])

    def test_pd02_clean_gate_and_pycache(self):
        home = self.home()
        plugins = home / "plugins"
        os.makedirs(plugins)
        os.chmod(plugins, 0o755)
        gate = plugins / "aaf-gate"
        self.write_plugin_yaml(gate, "name: aaf-gate\nversion: 0.5.0\n")
        cache = plugins / "__pycache__"
        cache.mkdir()
        os.chmod(cache, 0o755)
        self.assertEqual(verify_hermes_home(home), [])

    def test_pd03_invalid_gate_name_or_missing_yaml(self):
        home = self.home()
        plugins = home / "plugins"
        os.makedirs(plugins)
        os.chmod(plugins, 0o755)
        gate = plugins / "aaf-gate"
        self.write_plugin_yaml(gate, 'name: "other"\n')
        self.assertEqual(verify_hermes_home(home), ["gate_plugin_invalid"])
        for child in gate.iterdir():
            child.unlink()
        gate.rmdir()
        gate.mkdir()
        os.chmod(gate, 0o755)
        self.assertEqual(verify_hermes_home(home), ["gate_plugin_invalid"])

    def test_pd04_plugins_listdir_permission_error(self):
        home = self.home()
        plugins = home / "plugins"
        os.makedirs(plugins)
        os.chmod(plugins, 0o755)
        gate = plugins / "aaf-gate"
        self.write_plugin_yaml(gate, "name: aaf-gate\n")
        real_listdir = os.listdir

        def listdir(path):
            if os.fspath(path) == os.fspath(plugins):
                raise PermissionError("denied")
            return real_listdir(path)

        with mock.patch("framework.hookcheck.os.listdir", listdir):
            issues = verify_hermes_home(home)
        self.assertIn("unreadable:plugins", issues)

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_pd05_gate_plugin_symlink(self):
        home = self.home()
        plugins = home / "plugins"
        os.makedirs(plugins)
        os.chmod(plugins, 0o755)
        real = home / "real-aaf-gate"
        self.write_plugin_yaml(real, "name: aaf-gate\n")
        link = plugins / "aaf-gate"
        link.symlink_to(real)
        self.assertEqual(verify_hermes_home(home), ["symlink:plugins/aaf-gate"])


if __name__ == "__main__":
    unittest.main()
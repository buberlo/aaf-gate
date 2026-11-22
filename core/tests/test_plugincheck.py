import os
import shutil
import tempfile
import unittest
from pathlib import Path

from framework.canon import hash_value
from framework.configplane import ConfigError
from framework.plugincheck import plugin_digest, verify_plugin

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_SOURCE = REPO_ROOT / "adapters/hermes/aaf-gate"


def copy_plugin_tree():
    dest = Path(tempfile.mkdtemp(dir="/tmp")) / "aaf-gate"
    dest.mkdir()
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copyfile(PLUGIN_SOURCE / name, dest / name)
    return dest


class PluginCheckTests(unittest.TestCase):
    def setUp(self):
        self.copy = copy_plugin_tree()
        self.expected = plugin_digest(PLUGIN_SOURCE)

    def test_pc01_copy_is_clean(self):
        self.assertEqual(verify_plugin(self.copy, self.expected), [])
        self.assertEqual(plugin_digest(self.copy), self.expected)
        prefix = hash_value({})[: hash_value({}).index(":") + 1]
        self.assertTrue(self.expected.startswith(prefix))

    def test_pc02_modified_file(self):
        init = self.copy / "__init__.py"
        init.write_text(init.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(
            verify_plugin(self.copy, self.expected),
            [{"kind": "digest_mismatch", "path": ""}],
        )

    def test_pc03_extra_file(self):
        (self.copy / "evil.py").write_text("x", encoding="utf-8")
        self.assertEqual(
            verify_plugin(self.copy, self.expected),
            [{"kind": "extra", "path": "evil.py"}],
        )
        shutil.rmtree(self.copy)
        self.copy = copy_plugin_tree()
        (self.copy / "x").mkdir()
        self.assertEqual(
            verify_plugin(self.copy, self.expected),
            [{"kind": "extra", "path": "x"}],
        )

    def test_pc04_missing_file_and_dir(self):
        (self.copy / "plugin.yaml").unlink()
        self.assertEqual(
            verify_plugin(self.copy, self.expected),
            [{"kind": "missing", "path": "plugin.yaml"}],
        )
        missing = Path(tempfile.mkdtemp(dir="/tmp")) / "nope"
        self.assertEqual(
            verify_plugin(missing, self.expected),
            [{"kind": "missing", "path": ""}],
        )

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_pc05_symlinks(self):
        target = PLUGIN_SOURCE / "plugin.yaml"
        link = self.copy / "plugin.yaml"
        link.unlink()
        link.symlink_to(target)
        out = verify_plugin(self.copy, self.expected)
        self.assertIn({"kind": "symlink", "path": "plugin.yaml"}, out)
        self.assertNotIn({"kind": "digest_mismatch", "path": ""}, out)
        outer = Path(tempfile.mkdtemp(dir="/tmp")) / "link-dir"
        outer.symlink_to(self.copy, target_is_directory=True)
        self.assertEqual(
            verify_plugin(outer, self.expected),
            [{"kind": "symlink", "path": ""}],
        )

    def test_pc06_pycache_rules(self):
        cache = self.copy / "__pycache__"
        cache.mkdir()
        (cache / "a.cpython-313.pyc").write_bytes(b"\x00")
        self.assertEqual(verify_plugin(self.copy, self.expected), [])
        (cache / "note.txt").write_text("x", encoding="utf-8")
        self.assertEqual(
            verify_plugin(self.copy, self.expected),
            [{"kind": "extra", "path": "__pycache__/note.txt"}],
        )

    def test_pc07_digest_invalid_inputs(self):
        missing = Path(tempfile.mkdtemp(dir="/tmp")) / "nope"
        with self.assertRaises(ConfigError) as ctx:
            plugin_digest(missing)
        self.assertEqual(ctx.exception.code, "PLUGIN_INVALID")
        partial = Path(tempfile.mkdtemp(dir="/tmp")) / "aaf-gate"
        partial.mkdir()
        shutil.copyfile(PLUGIN_SOURCE / "__init__.py", partial / "__init__.py")
        with self.assertRaises(ConfigError) as ctx:
            plugin_digest(partial)
        self.assertEqual(ctx.exception.code, "PLUGIN_INVALID")
        if os.name == "posix":
            link = Path(tempfile.mkdtemp(dir="/tmp")) / "link"
            link.symlink_to(self.copy, target_is_directory=True)
            with self.assertRaises(ConfigError) as ctx:
                plugin_digest(link)
            self.assertEqual(ctx.exception.code, "PLUGIN_INVALID")

    def test_pc08_order(self):
        (self.copy / "plugin.yaml").unlink()
        (self.copy / "b.txt").write_text("b", encoding="utf-8")
        (self.copy / "a.txt").write_text("a", encoding="utf-8")
        self.assertEqual(
            verify_plugin(self.copy, self.expected),
            [
                {"kind": "extra", "path": "a.txt"},
                {"kind": "extra", "path": "b.txt"},
                {"kind": "missing", "path": "plugin.yaml"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
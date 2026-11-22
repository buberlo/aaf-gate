import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.hookcheck import verify_hermes_home

CORE = Path(__file__).resolve().parents[1]


class HookCheckTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(dir="/tmp"))
        os.chmod(self.root, 0o755)

    def profile(self):
        home = self.root / "hermes"
        home.mkdir()
        os.chmod(home, 0o755)
        return home

    def write_config(self, home, text):
        path = home / "config.yaml"
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o644)

    def test_hk01_empty_profile(self):
        home = self.profile()
        self.write_config(home, "model: gpt\n")
        self.assertEqual(verify_hermes_home(home), [])
        bare = Path(tempfile.mkdtemp(dir="/tmp"))
        os.chmod(bare, 0o755)
        self.assertEqual(verify_hermes_home(bare), [])

    def test_hk02_gateway_hooks(self):
        home = self.profile()
        hooks = home / "hooks"
        hooks.mkdir()
        os.chmod(hooks, 0o755)
        complete = hooks / "a"
        complete.mkdir()
        os.chmod(complete, 0o755)
        for name in ("HOOK.yaml", "handler.py"):
            p = complete / name
            p.write_text("x\n", encoding="utf-8")
            os.chmod(p, 0o644)
        partial = hooks / "b"
        partial.mkdir()
        os.chmod(partial, 0o755)
        hook_yaml = partial / "HOOK.yaml"
        hook_yaml.write_text("x\n", encoding="utf-8")
        os.chmod(hook_yaml, 0o644)
        self.assertEqual(verify_hermes_home(home), ["gateway_hook:hooks/a"])

    def test_hk03_shell_hooks_config(self):
        home = self.profile()
        self.write_config(home, "hooks:\n  pre_tool_call: x\n")
        self.assertEqual(verify_hermes_home(home), ["shell_hooks:config.yaml"])
        self.write_config(home, "hooks: {pre_tool_call: x}\n")
        self.assertEqual(verify_hermes_home(home), ["shell_hooks:config.yaml"])
        self.write_config(home, "hooks: {}\n")
        self.assertEqual(verify_hermes_home(home), [])
        self.write_config(home, "hooks:\n# note\nmodel: x\n")
        self.assertEqual(verify_hermes_home(home), [])

    def test_hk04_writable_by_others(self):
        home = self.profile()
        hooks = home / "hooks"
        hooks.mkdir()
        os.chmod(hooks, 0o777)
        self.write_config(home, "model: x\n")
        path = home / "config.yaml"
        os.chmod(path, 0o666)
        issues = verify_hermes_home(home)
        self.assertIn("writable_by_others:hooks", issues)
        self.assertIn("writable_by_others:config.yaml", issues)

    @unittest.skipUnless(os.name == "posix", "posix symlinks")
    def test_hk05_symlinks_and_missing(self):
        home = self.profile()
        hooks = home / "hooks"
        target = home / "hooks-target"
        target.mkdir()
        os.chmod(target, 0o755)
        hooks.symlink_to(target)
        self.assertEqual(verify_hermes_home(home), ["symlink:hooks"])
        outer_target = self.root / "real-home"
        outer_target.mkdir()
        os.chmod(outer_target, 0o755)
        link_home = self.root / "link-home"
        link_home.symlink_to(outer_target)
        self.assertEqual(verify_hermes_home(link_home), ["home_symlink"])
        missing = self.root / "missing"
        self.assertEqual(verify_hermes_home(missing), ["home_missing"])

    def test_hk06_cli(self):
        home = self.profile()
        self.write_config(home, "model: x\n")
        clean = subprocess.run(
            [sys.executable, "-m", "framework.cli", "verify-hooks", str(home)],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(clean.returncode, 0)
        body = json.loads(clean.stdout.splitlines()[0])
        self.assertEqual(body, {"issues": [], "ok": True})
        self.write_config(home, "hooks: {pre_tool_call: x}\n")
        bad = subprocess.run(
            [sys.executable, "-m", "framework.cli", "verify-hooks", str(home)],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad.returncode, 1)
        body = json.loads(bad.stdout.splitlines()[0])
        self.assertIn("issues", body)
        self.assertIn("ok", body)
        self.assertFalse(body["ok"])
        self.assertEqual(body["issues"], ["shell_hooks:config.yaml"])


if __name__ == "__main__":
    unittest.main()
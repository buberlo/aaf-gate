import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.cli import main
from framework.configplane import ConfigPlane
from framework.pinaudit import audit_plugins, disabled_from_config
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body

CORE = Path(__file__).resolve().parents[1]


def write_fixture(root: Path) -> Path:
    plugins = root / "plugins"
    plugins.mkdir(parents=True)
    tooly = plugins / "tooly"
    tooly.mkdir()
    (tooly / "plugin.yaml").write_text("name: tooly\n", encoding="utf-8")
    (tooly / "__init__.py").write_text('def f():\n    pass\nctx.register_hook("pre_tool_call", f)\n', encoding="utf-8")
    plat = plugins / "plat" / "x"
    plat.mkdir(parents=True)
    (plat / "plugin.yaml").write_text("name: x-platform\nkind: platform\n", encoding="utf-8")
    (plat / "adapter.py").write_text(
        "def f():\n    pass\nfor h, cb in ((\"a\", f),):\n    ctx.register_hook(h, cb)\n",
        encoding="utf-8",
    )
    meet = plugins / "meet"
    meet.mkdir()
    (meet / "plugin.yaml").write_text("name: meet\nkind: standalone\n", encoding="utf-8")
    (meet / "__init__.py").write_text('def f():\n    pass\nctx.register_hook("on_session_end", f)\n', encoding="utf-8")
    mem = plugins / "mem"
    mem.mkdir()
    (mem / "__init__.py").write_text("def register_hook(self, name, cb):\n    pass\n", encoding="utf-8")
    return root


class PinAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/tmp"))
        self.source = write_fixture(self.tmp)

    def plugin_by_name(self, result, name):
        for row in result["plugins"]:
            if row["name"] == name:
                return row
        return None

    def test_pa01_disabled_ok(self):
        result = audit_plugins(self.source, {"tooly", "x-platform"})
        self.assertEqual(result["issues"], [])
        tooly = self.plugin_by_name(result, "tooly")
        self.assertEqual(tooly["hooks"], ["pre_tool_call"])
        plat = self.plugin_by_name(result, "x-platform")
        self.assertEqual(plat["hooks"], ["dynamic"])
        meet = self.plugin_by_name(result, "meet")
        self.assertEqual(meet["hooks"], ["on_session_end"])
        names = {row["name"] for row in result["plugins"]}
        self.assertNotIn("mem", names)

    def test_pa02_missing_disabled(self):
        result = audit_plugins(self.source, set())
        self.assertEqual(
            result["issues"],
            ["hook_plugin_not_disabled:tooly", "hook_plugin_not_disabled:x-platform"],
        )

    def test_pa03_unparseable(self):
        broken = self.source / "plugins" / "broken"
        broken.mkdir()
        (broken / "bad.py").write_text("def (\n", encoding="utf-8")
        result = audit_plugins(self.source, {"tooly", "x-platform"})
        self.assertIn("unparseable:broken/bad.py", result["issues"])

    def test_pa04_orphan_hook(self):
        (self.source / "plugins" / "loose.py").write_text(
            'def f():\n    pass\nctx.register_hook("pre_tool_call", f)\n',
            encoding="utf-8",
        )
        result = audit_plugins(self.source, {"tooly", "x-platform"})
        self.assertIn("orphan_hook:loose.py", result["issues"])

    def test_pa05_appliance_disabled_list(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        target = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", target)
        text = (target / "managed/config.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            disabled_from_config(text),
            ["disk-cleanup", "raft-platform", "security-guidance"],
        )

    def test_pa06_cli(self):
        config = self.tmp / "config.yaml"
        config.write_text(
            "plugins:\n  disabled:\n    - tooly\n    - x-platform\n",
            encoding="utf-8",
        )
        code = main(["pin-audit", str(self.source), str(config)])
        self.assertEqual(code, 0)
        missing = Path(tempfile.mkdtemp(dir="/tmp"))
        bad_cfg = missing / "c.yaml"
        bad_cfg.write_text("plugins:\n  disabled:\n []\n", encoding="utf-8")
        code2 = main(["pin-audit", str(missing), str(bad_cfg)])
        self.assertEqual(code2, 2)
        completed = subprocess.run(
            [sys.executable, "-m", "framework.cli", "pin-audit", str(self.source), str(config)],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0)
        body = json.loads(completed.stdout.splitlines()[0])
        self.assertTrue(body["ok"])

    def test_pa07_middleware_disabled(self):
        root = Path(tempfile.mkdtemp(dir="/tmp"))
        plugins = root / "plugins"
        plugins.mkdir()
        mw = plugins / "mw"
        mw.mkdir()
        (mw / "plugin.yaml").write_text("name: mw\nkind: standalone\n", encoding="utf-8")
        (mw / "__init__.py").write_text(
            'def f():\n    pass\nctx.register_middleware("tool_execution", f)\n',
            encoding="utf-8",
        )
        missing = audit_plugins(root, set())
        self.assertIn("hook_plugin_not_disabled:mw", missing["issues"])
        row = self.plugin_by_name(missing, "mw")
        self.assertEqual(row["hooks"], ["middleware:tool_execution"])
        clean = audit_plugins(root, {"mw"})
        self.assertNotIn("hook_plugin_not_disabled:mw", clean["issues"])

    def test_pa08_orphan_and_dynamic_middleware(self):
        root = Path(tempfile.mkdtemp(dir="/tmp"))
        plugins = root / "plugins"
        plugins.mkdir()
        (plugins / "loose_mw.py").write_text(
            "def f():\n    pass\nfor k in (\"tool_execution\",):\n    ctx.register_middleware(k, f)\n",
            encoding="utf-8",
        )
        orphan = audit_plugins(root, set())
        self.assertIn("orphan_hook:loose_mw.py", orphan["issues"])
        root2 = Path(tempfile.mkdtemp(dir="/tmp"))
        plugins2 = root2 / "plugins"
        plugins2.mkdir()
        dyn = plugins2 / "dynmw"
        dyn.mkdir()
        (dyn / "plugin.yaml").write_text("name: dynmw\nkind: standalone\n", encoding="utf-8")
        (dyn / "__init__.py").write_text(
            "kind_var = \"tool_execution\"\ndef f():\n    pass\nctx.register_middleware(kind_var, f)\n",
            encoding="utf-8",
        )
        dynamic = audit_plugins(root2, {"dynmw"})
        row = self.plugin_by_name(dynamic, "dynmw")
        self.assertEqual(row["hooks"], ["middleware:dynamic"])


if __name__ == "__main__":
    unittest.main()
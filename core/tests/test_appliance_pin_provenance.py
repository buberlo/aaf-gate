import json
import tempfile
import unittest
from pathlib import Path

from framework.appliance import _managed_config, build_appliance_package
from framework.canon import hash_value
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.pinaudit import audit_plugins, disabled_from_config
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body


def write_pin_fixture(root: Path, include_evil: bool = False) -> Path:
    plugins = root / "plugins"
    plugins.mkdir(parents=True)
    for name, hook_line in (
        ("disk-cleanup", 'ctx.register_hook("pre_tool_call", f)\n'),
        ("memo", 'ctx.register_hook("on_session_end", f)\n'),
    ):
        plugin = plugins / name
        plugin.mkdir()
        (plugin / "plugin.yaml").write_text("name: " + name + "\n", encoding="utf-8")
        (plugin / "__init__.py").write_text("def f():\n    pass\n" + hook_line, encoding="utf-8")
    if include_evil:
        evil = plugins / "evil"
        evil.mkdir()
        (evil / "plugin.yaml").write_text("name: evil\n", encoding="utf-8")
        (evil / "__init__.py").write_text(
            'def f():\n    pass\nctx.register_middleware("tool_execution", f)\n',
            encoding="utf-8",
        )
    return root


class AppliancePinProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        self.plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        self.plane.register_template("konrad", template_body())
        self.plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        self.commit = "a" * 40

    def test_pp01_without_hermes_source(self):
        target = Path(self.folder) / "plain"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        provenance = json.loads((target / "provenance.json").read_text(encoding="utf-8"))
        self.assertIsNone(provenance.get("hermes_commit"))
        self.assertIsNone(provenance.get("pin_audit_hash"))

    def test_pp02_pin_hash_and_stable_package(self):
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        commit = self.commit
        disabled = set(disabled_from_config(_managed_config()))
        expected_hash = hash_value(audit_plugins(source, disabled))
        first = Path(self.folder) / "pkg-a"
        second = Path(self.folder) / "pkg-b"
        built_a = build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            first,
            hermes_source=source,
            hermes_commit=commit,
        )
        built_b = build_appliance_package(
            self.plane,
            "konrad",
            "cfg-1",
            second,
            hermes_source=source,
            hermes_commit=commit,
        )
        prov_a = json.loads((first / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(prov_a["hermes_commit"], commit)
        self.assertEqual(prov_a["pin_audit_hash"], expected_hash)
        self.assertEqual(built_a["package_hash"], built_b["package_hash"])

    def test_pp03_pin_audit_failed(self):
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")), include_evil=True)
        target = Path(self.folder) / "bad"
        with self.assertRaises(ConfigError) as ctx:
            build_appliance_package(
                self.plane,
                "konrad",
                "cfg-1",
                target,
                hermes_source=source,
                hermes_commit=self.commit,
            )
        self.assertEqual(ctx.exception.code, "PIN_AUDIT_FAILED")
        self.assertFalse(target.exists())

    def test_pp04_validation_errors(self):
        source = write_pin_fixture(Path(tempfile.mkdtemp(dir="/tmp")))
        bad_commit = Path(self.folder) / "invalid-commit"
        with self.assertRaises(ConfigError) as ctx:
            build_appliance_package(
                self.plane,
                "konrad",
                "cfg-1",
                bad_commit,
                hermes_source=source,
                hermes_commit="abc",
            )
        self.assertEqual(ctx.exception.code, "HERMES_COMMIT_INVALID")
        self.assertFalse(bad_commit.exists())
        missing_source = Path(self.folder) / "missing-source"
        with self.assertRaises(ConfigError) as ctx:
            build_appliance_package(
                self.plane,
                "konrad",
                "cfg-1",
                missing_source,
                hermes_commit=self.commit,
            )
        self.assertEqual(ctx.exception.code, "HERMES_SOURCE_MISSING")
        self.assertFalse(missing_source.exists())
        empty = Path(tempfile.mkdtemp(dir="/tmp")) / "empty"
        empty.mkdir()
        no_plugins = Path(self.folder) / "no-plugins"
        with self.assertRaises(ConfigError) as ctx:
            build_appliance_package(
                self.plane,
                "konrad",
                "cfg-1",
                no_plugins,
                hermes_source=empty,
                hermes_commit=self.commit,
            )
        self.assertEqual(ctx.exception.code, "plugins_missing")
        self.assertFalse(no_plugins.exists())

    def test_pp05_assumptions(self):
        target = Path(self.folder) / "assumptions"
        build_appliance_package(self.plane, "konrad", "cfg-1", target)
        text = (target / "ASSUMPTIONS.md").read_text(encoding="utf-8")
        marker = "Not documented and therefore not claimed as function of this package:"
        line = "hermes_commit is a package-build input"
        self.assertIn(line, text)
        self.assertGreater(text.index(line), text.index(marker))


if __name__ == "__main__":
    unittest.main()
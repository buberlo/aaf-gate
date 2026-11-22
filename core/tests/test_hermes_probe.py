import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import (
    PLUGINS_DISABLED,
    PLUGINS_ENABLED,
    _profile_config,
    build_appliance_package,
)
from framework.cli import main
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.devicecheck import managed_socket_path
from framework.hermesprobe import PROBE_SCHEMA, PROBE_SCRIPT, evaluate_probe
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service_tmp
from test_appliance_package import initial_values, template_body

PROFILE_CONFIG_LEGACY = (
    "approvals:\n"
    "  mode: manual\n"
    "  cron_mode: deny\n"
    "  single_query_mode: deny\n"
    "  unattended_mode: deny\n"
    "computer_use:\n"
    "  permission_mode: standard\n"
    "plugins:\n"
    "  enabled:\n"
    "    - aaf-gate\n"
    "  disabled:\n"
    "    - disk-cleanup\n"
    "    - raft-platform\n"
    "    - security-guidance\n"
)

EXPECTED_PROBE_KEYS = frozenset(
    {
        "label_ids",
        "label_ids_type",
        "managed_loaded",
        "ok",
        "plugins_disabled",
        "plugins_disabled_type",
        "plugins_enabled",
        "plugins_enabled_type",
        "schema",
        "socket_path",
        "socket_path_type",
    }
)


def _build_package(folder, gate=True):
    store = Store(str(Path(folder) / "cfg.sqlite"))
    plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
    seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
    plane.register_template("konrad", template_body())
    plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
    target = Path(folder) / "pkg"
    kwargs = {}
    if gate:
        kwargs["gate_service"] = gate_service_tmp(folder)
    build_appliance_package(plane, "konrad", "cfg-1", target, **kwargs)
    return target


def _good_probe_text(package_dir, **overrides):
    root = Path(package_dir)
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    managed = (root / "managed" / "config.yaml").read_text(encoding="utf-8")
    socket = managed_socket_path(managed)
    body = {
        "label_ids": provenance.get("label_ids") or [],
        "label_ids_type": "list",
        "managed_loaded": True,
        "ok": True,
        "plugins_disabled": list(PLUGINS_DISABLED),
        "plugins_disabled_type": "list",
        "plugins_enabled": list(PLUGINS_ENABLED),
        "plugins_enabled_type": "list",
        "schema": PROBE_SCHEMA,
        "socket_path": socket,
        "socket_path_type": "str",
    }
    body.update(overrides)
    return json.dumps(body, sort_keys=True) + "\n"


def _fake_hermes_root(folder, cfg_body, managed_body):
    root = Path(folder) / "hermes_cli"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "config.py").write_text(cfg_body, encoding="utf-8")
    (root / "managed_scope.py").write_text(managed_body, encoding="utf-8")
    return folder


class HermesProbeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")

    def test_hpr01_package_probe_file(self):
        gated = _build_package(self.folder, gate=True)
        plain_parent = tempfile.mkdtemp(dir="/tmp")
        plain = _build_package(plain_parent, gate=False)
        path = gated / "probes" / "hermes_probe.py"
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), PROBE_SCRIPT)
        self.assertFalse((plain / "probes" / "hermes_probe.py").exists())

    def test_hpr02_script_data_minimization(self):
        cfg = (
            "def load_config_readonly():\n"
            "    return {\n"
            '        "api_key": "SECRET-VALUE",\n'
            '        "plugins": {\n'
            '            "enabled": ["aaf-gate"],\n'
            '            "disabled": ["disk-cleanup", "raft-platform", "security-guidance"],\n'
            '            "entries": {"aaf-gate": {"settings": {"socket_path": "/var/s", "label_ids": []}}},\n'
            "        },\n"
            "    }\n"
        )
        managed = "def load_managed_config():\n    return {'x': 1}\n"
        fake_root = _fake_hermes_root(self.folder, cfg, managed)
        script = Path(self.folder) / "run_probe.py"
        script.write_text(PROBE_SCRIPT, encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": fake_root}
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("SECRET-VALUE", proc.stdout)
        body = json.loads(proc.stdout.strip())
        self.assertEqual(set(body.keys()), EXPECTED_PROBE_KEYS)
        self.assertTrue(body["ok"])
        self.assertTrue(body["managed_loaded"])

    def test_hpr03_import_failed(self):
        script = Path(self.folder) / "probe.py"
        script.write_text(PROBE_SCRIPT, encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": self.folder}
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        body = json.loads(proc.stdout.strip())
        self.assertEqual(body["error"], "hermes_import_failed")

    def test_hpr04_label_ids_string(self):
        cfg = (
            "def load_config_readonly():\n"
            "    return {'plugins': {'entries': {'aaf-gate': {'settings': {'label_ids': 'x'}}}}}\n"
        )
        managed = "def load_managed_config():\n    return {}\n"
        fake_root = _fake_hermes_root(self.folder, cfg, managed)
        script = Path(self.folder) / "probe.py"
        script.write_text(PROBE_SCRIPT, encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": fake_root}
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        body = json.loads(proc.stdout.strip())
        self.assertEqual(body["label_ids_type"], "str")
        self.assertIsNone(body["label_ids"])

    def test_hpr05_evaluate_and_cli_ok(self):
        package = _build_package(self.folder)
        text = _good_probe_text(package)
        code, body = evaluate_probe(package, text)
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        self.assertEqual(body["issues"], [])
        probe_file = Path(self.folder) / "probe.json"
        probe_file.write_text(text, encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_code = main(
                [
                    "verify-hermes-probe",
                    str(probe_file),
                    "--package",
                    str(package),
                ]
            )
        self.assertEqual(cli_code, 0)
        self.assertTrue(json.loads(buf.getvalue().strip())["ok"])

    def test_hpr06_label_ids_not_list(self):
        package = _build_package(self.folder)
        text = _good_probe_text(
            package,
            label_ids=None,
            label_ids_type="str",
        )
        code, body = evaluate_probe(package, text)
        self.assertEqual(code, 1)
        self.assertIn("label_ids_not_list", body["issues"])

    def test_hpr07_plugin_list_mismatches(self):
        package = _build_package(self.folder)
        enabled_extra = _good_probe_text(
            package,
            plugins_enabled=["aaf-gate", "extra"],
        )
        code, body = evaluate_probe(package, enabled_extra)
        self.assertIn("plugins_enabled_mismatch", body["issues"])
        disabled_missing = _good_probe_text(
            package,
            plugins_disabled=["disk-cleanup", "raft-platform"],
        )
        code, body = evaluate_probe(package, disabled_missing)
        self.assertIn("plugins_disabled_mismatch", body["issues"])
        label_extra = _good_probe_text(package, label_ids=["extra-label"])
        code, body = evaluate_probe(package, label_extra)
        self.assertIn("label_ids_mismatch", body["issues"])

    def test_hpr08_other_issues(self):
        package = _build_package(self.folder)
        socket_bad = _good_probe_text(package, socket_path="/other.sock")
        code, body = evaluate_probe(package, socket_bad)
        self.assertIn("socket_path_mismatch", body["issues"])
        not_loaded = _good_probe_text(package, managed_loaded=False)
        code, body = evaluate_probe(package, not_loaded)
        self.assertIn("managed_not_loaded", body["issues"])
        failed = _good_probe_text(package, ok=False)
        code, body = evaluate_probe(package, failed)
        self.assertIn("probe_failed", body["issues"])

    def test_hpr09_unreadable_inputs(self):
        package = _build_package(self.folder)
        code, body = evaluate_probe(package, "not-json")
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "probe_unreadable")
        plain_parent = tempfile.mkdtemp(dir="/tmp")
        plain = _build_package(plain_parent, gate=False)
        text = _good_probe_text(package)
        code, body = evaluate_probe(plain, text)
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "package_without_gate")

    def test_hpr10_checklist_section(self):
        manual = (_build_package(self.folder) / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("### Geräteproben", manual)
        self.assertIn("verify-hermes-probe", manual)
        self.assertIn("aaf-probe-extra", manual)
        plain_parent = tempfile.mkdtemp(dir="/tmp")
        plain_manual = (_build_package(plain_parent, gate=False) / "MANUAL-MAC-CHECKS.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("### Geräteproben", plain_manual)

    def test_hpr11_profile_config_unchanged(self):
        self.assertEqual(_profile_config(), PROFILE_CONFIG_LEGACY)


if __name__ == "__main__":
    unittest.main()
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.clock import FixedClock
from framework.configplane import ConfigPlane
from framework.relaycheck import verify_relay
from framework.store import Store

from seed_config import seed_config
from test_appliance_package import initial_values, template_body

CORE = Path(__file__).resolve().parents[1]


class RelayCheckTests(unittest.TestCase):
    def test_vr01_clean(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as home, tempfile.TemporaryDirectory(dir="/tmp") as system_root:
            environ = {"OTEL_SDK_DISABLED": "true"}
            self.assertEqual(verify_relay(environ, home, system_root), [])

    def test_vr02_env_issues(self):
        environ = {
            "HERMES_NEMO_RELAY_PLUGINS_TOML": "/x",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://e",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://t",
            "OTEL_EXPORTER_OTLP_HEADERS": "a=b",
        }
        issues = verify_relay(environ, "/tmp/home", "/tmp/sys")
        self.assertEqual(
            issues,
            [
                "otel_sdk_not_disabled",
                "otlp_endpoint:OTEL_EXPORTER_OTLP_ENDPOINT",
                "otlp_endpoint:OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                "relay_toml_env",
            ],
        )
        self.assertNotIn("otlp_endpoint:OTEL_EXPORTER_OTLP_HEADERS", issues)
        on = dict(environ)
        on["OTEL_SDK_DISABLED"] = "TRUE"
        self.assertNotIn("otel_sdk_not_disabled", verify_relay(on, "/tmp", "/tmp"))
        for value in ("", "1"):
            off = dict(environ)
            off["OTEL_SDK_DISABLED"] = value
            self.assertIn("otel_sdk_not_disabled", verify_relay(off, "/tmp", "/tmp"))
        spaced = dict(environ)
        spaced["OTEL_EXPORTER_OTLP_ENDPOINT"] = "  "
        spaced_issues = verify_relay(spaced, "/tmp", "/tmp")
        self.assertNotIn("otlp_endpoint:OTEL_EXPORTER_OTLP_ENDPOINT", spaced_issues)

    def test_vr03_plugin_files(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as home, tempfile.TemporaryDirectory(dir="/tmp") as system_root:
            xdg = Path(home) / "xdg"
            xdg.mkdir()
            paths = [
                Path(home) / ".config" / "nemo-relay" / "plugins.toml",
                xdg / "nemo-relay" / "plugins.toml",
                Path(system_root) / "etc" / "nemo-relay" / "plugins.toml",
            ]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            environ = {"OTEL_SDK_DISABLED": "true", "XDG_CONFIG_HOME": str(xdg)}
            issues = verify_relay(environ, home, system_root)
            expected = sorted("relay_plugins_file:" + str(path) for path in paths)
            self.assertEqual(issues, expected)

    def test_vr04_cli(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as home, tempfile.TemporaryDirectory(dir="/tmp") as system_root:
            env = {"PATH": os.environ.get("PATH", ""), "OTEL_SDK_DISABLED": "true"}
            ok = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "framework.cli",
                    "verify-relay",
                    "--home",
                    home,
                    "--system-root",
                    system_root,
                ],
                cwd=CORE,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(ok.returncode, 0)
            self.assertTrue(json.loads(ok.stdout.splitlines()[0])["ok"])
            bad_env = dict(env)
            bad_env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://e"
            fail = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "framework.cli",
                    "verify-relay",
                    "--home",
                    home,
                    "--system-root",
                    system_root,
                ],
                cwd=CORE,
                capture_output=True,
                text=True,
                env=bad_env,
            )
            self.assertEqual(fail.returncode, 1)
            body = json.loads(fail.stdout.splitlines()[0])
            self.assertIn("otlp_endpoint:OTEL_EXPORTER_OTLP_ENDPOINT", body["issues"])
            missing = subprocess.run(
                [sys.executable, "-m", "framework.cli", "verify-relay"],
                cwd=CORE,
                capture_output=True,
                text=True,
                env={"PATH": os.environ.get("PATH", "")},
            )
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(
                json.loads(missing.stdout.splitlines()[0]),
                {"error": "home_missing", "ok": False},
            )

    def test_vr05_appliance_checklist(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        store = Store(str(Path(folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(store, (("konrad", "synth-tenant", "human", ("author",)),))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        target = Path(folder) / "pkg"
        build_appliance_package(plane, "konrad", "cfg-1", target)
        manual = (target / "MANUAL-MAC-CHECKS.md").read_text(encoding="utf-8")
        self.assertIn("verify-relay` reports ok true as customer and agent account", manual)


if __name__ == "__main__":
    unittest.main()
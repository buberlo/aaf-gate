import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.cli import main
from framework import hermes_mcp
from framework.launchd import gate_plist

CORE = Path(__file__).resolve().parents[1]


def valid_args():
    folder = tempfile.mkdtemp(dir="/tmp")
    return {
        "label": "com.example.aaf.gate",
        "python": "/usr/bin/python3",
        "workdir": folder + "/core",
        "db": folder + "/gate.sqlite",
        "socket_path": folder + "/gate.sock",
        "principal": "hermes-a",
        "user": "aafgate",
    }


class LaunchdPlistTests(unittest.TestCase):
    def test_lp01_valid_plist(self):
        base = valid_args()
        data = plistlib.loads(
            gate_plist(
                **base,
                group="aafgate",
                unattended=True,
            )
        )
        self.assertEqual(
            set(data.keys()),
            {"KeepAlive", "Label", "ProgramArguments", "UserName", "WorkingDirectory"},
        )
        self.assertEqual(data["Label"], base["label"])
        self.assertIs(data["KeepAlive"], True)
        self.assertEqual(
            data["ProgramArguments"],
            [
                base["python"],
                "-m",
                "framework.cli",
                "gate-serve",
                "--db",
                base["db"],
                "--socket",
                base["socket_path"],
                "--principal",
                base["principal"],
                "--server",
                "gmail",
                "--mode",
                "0600",
                "--group",
                "aafgate",
                "--unattended",
            ],
        )

    def test_lp02_deterministic(self):
        base = valid_args()
        first = gate_plist(**base)
        second = gate_plist(**base)
        self.assertEqual(first, second)

    def test_lp03_log_path(self):
        base = valid_args()
        log_path = base["socket_path"] + ".log"
        data = plistlib.loads(gate_plist(**base, log_path=log_path))
        self.assertEqual(data["StandardOutPath"], log_path)
        self.assertEqual(data["StandardErrorPath"], log_path)
        self.assertEqual(data["ProgramArguments"][-2:], ["--mode", "0600"])

    def test_lp04_path_not_absolute(self):
        base = valid_args()
        cases = {
            "python": "/usr/bin/python3",
            "workdir": base["workdir"],
            "db": base["db"],
            "socket_path": base["socket_path"],
            "log_path": base["socket_path"] + ".log",
        }
        relative = {
            "python": "python3",
            "workdir": "core",
            "db": "gate.sqlite",
            "socket_path": "g.sock",
            "log_path": "gate.log",
        }
        for key, value in relative.items():
            with self.subTest(key=key):
                kwargs = dict(base)
                if key == "log_path":
                    kwargs["log_path"] = value
                else:
                    kwargs[key] = value
                with self.assertRaises(ValueError) as ctx:
                    gate_plist(**kwargs)
                self.assertEqual(str(ctx.exception), "path_not_absolute")

    def test_lp05_user_and_principal(self):
        base = valid_args()
        with self.assertRaises(ValueError) as ctx:
            gate_plist(**{**base, "user": "root"})
        self.assertEqual(str(ctx.exception), "user_root_forbidden")
        with self.assertRaises(ValueError) as ctx:
            gate_plist(**{**base, "user": "Bad User"})
        self.assertEqual(str(ctx.exception), "user_invalid")
        with self.assertRaises(ValueError) as ctx:
            gate_plist(**{**base, "principal": ""})
        self.assertEqual(str(ctx.exception), "principal_invalid")

    def test_lp06_label_group_argument(self):
        base = valid_args()
        for label in ("nodot", "a..b"):
            with self.subTest(label=label):
                with self.assertRaises(ValueError) as ctx:
                    gate_plist(**{**base, "label": label})
                self.assertEqual(str(ctx.exception), "label_invalid")
        with self.assertRaises(ValueError) as ctx:
            gate_plist(**{**base, "group": "a b"})
        self.assertEqual(str(ctx.exception), "group_invalid")
        with self.assertRaises(ValueError) as ctx:
            gate_plist(**{**base, "principal": "bad\nid"})
        self.assertEqual(str(ctx.exception), "argument_invalid")

    def test_lp07_mode_and_server(self):
        base = valid_args()
        with self.assertRaises(ValueError) as ctx:
            gate_plist(**{**base, "mode": "0777"})
        self.assertEqual(str(ctx.exception), "invalid_socket_mode")
        try:
            hermes_mcp.pilot1_policy("terminal")
            expected = "unexpected"
        except ValueError as exc:
            expected = str(exc)
        with self.assertRaises(ValueError) as ctx:
            gate_plist(**{**base, "server": "terminal"})
        self.assertEqual(str(ctx.exception), expected)

    def test_lp08_cli(self):
        base = valid_args()
        folder = Path(base["workdir"]).parent

        def run_cli(*extra):
            return subprocess.run(
                [sys.executable, "-m", "framework.cli", *extra],
                cwd=CORE,
                capture_output=True,
            )

        ok = run_cli(
            "gate-plist",
            "--label",
            base["label"],
            "--python",
            base["python"],
            "--workdir",
            base["workdir"],
            "--db",
            base["db"],
            "--socket",
            base["socket_path"],
            "--principal",
            base["principal"],
            "--user",
            base["user"],
        )
        self.assertEqual(ok.returncode, 0)
        data = plistlib.loads(ok.stdout)
        self.assertNotIn("EnvironmentVariables", data)
        bad = run_cli(
            "gate-plist",
            "--label",
            base["label"],
            "--python",
            base["python"],
            "--workdir",
            base["workdir"],
            "--db",
            "rel.sqlite",
            "--socket",
            base["socket_path"],
            "--principal",
            base["principal"],
            "--user",
            base["user"],
        )
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(
            json.loads(bad.stdout.decode("utf-8").splitlines()[0]),
            {"error": "path_not_absolute", "ok": False},
        )
        self.assertFalse((folder / "rel.sqlite").exists())
        code = main(
            [
                "gate-plist",
                "--label",
                base["label"],
                "--python",
                base["python"],
                "--workdir",
                base["workdir"],
                "--db",
                "rel.sqlite",
                "--socket",
                base["socket_path"],
                "--principal",
                base["principal"],
                "--user",
                base["user"],
            ]
        )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from framework.appliance import build_appliance_package
from framework.cli import main
from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.launchd import gate_plist
from framework.launchdcheck import (
    INSTALL_MAP_SOURCE,
    package_launchd_expectation,
    verify_launchd_file,
)
from framework.store import Store

from seed_config import seed_config
from test_appliance_launchd import gate_service
from test_appliance_package import initial_values, template_body

CORE = Path(__file__).resolve().parents[1]


def sample_plist_bytes():
    return gate_plist(
        label="de.example.aaf-gate",
        python="/usr/bin/python3",
        workdir="/opt/aaf/core",
        db="/var/aaf/gate.sqlite",
        socket_path="/var/aaf/run/g.sock",
        principal="hermes-a",
        user="aafgate",
    )


def write_plist(folder, mode=0o600):
    path = Path(folder) / "job.plist"
    data = sample_plist_bytes()
    path.write_bytes(data)
    os.chmod(path, mode)
    digest = hashlib.sha256(data).hexdigest()
    return path, digest


class LaunchdCheckTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.uid = os.getuid()

    def test_lc01_mode_0600_clean(self):
        path, digest = write_plist(self.folder, 0o600)
        self.assertEqual(verify_launchd_file(path, digest, expected_uid=self.uid), [])

    def test_lc02_mode_0400_and_open(self):
        path, digest = write_plist(self.folder, 0o400)
        self.assertEqual(verify_launchd_file(path, digest, expected_uid=self.uid), [])
        for mode in (0o644, 0o660):
            with self.subTest(mode=mode):
                p, d = write_plist(tempfile.mkdtemp(dir="/tmp"), mode)
                self.assertEqual(verify_launchd_file(p, d, expected_uid=self.uid), ["mode_too_open"])

    def test_lc03_sha256_mismatch(self):
        path, digest = write_plist(self.folder, 0o600)
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(verify_launchd_file(path, digest, expected_uid=self.uid), ["sha256_mismatch"])

    def test_lc04_owner_and_combined(self):
        path, digest = write_plist(self.folder, 0o600)
        other = self.uid + 1
        self.assertEqual(verify_launchd_file(path, digest, expected_uid=other), ["owner_not_root"])
        loose, loose_digest = write_plist(tempfile.mkdtemp(dir="/tmp"), 0o644)
        self.assertEqual(
            verify_launchd_file(loose, loose_digest, expected_uid=other),
            ["mode_too_open", "owner_not_root"],
        )

    def test_lc05_missing_and_symlink(self):
        missing = Path(self.folder) / "missing.plist"
        self.assertEqual(verify_launchd_file(missing, "0" * 64), ["missing"])
        if os.name == "posix":
            target, digest = write_plist(self.folder, 0o600)
            link = Path(self.folder) / "link.plist"
            link.symlink_to(target)
            self.assertEqual(verify_launchd_file(link, digest, expected_uid=self.uid), ["symlink"])

    def test_lc06_install_map_and_expectation(self):
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            store,
            (
                ("konrad", "synth-tenant", "human", ("author",)),
                ("cust", "synth-tenant", "human", ("customer",)),
            ),
        )
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        with_gs = Path(self.folder) / "with-gs"
        gs = gate_service()
        build_appliance_package(plane, "konrad", "cfg-1", with_gs, gate_service=gs)
        body = json.loads((with_gs / "install-map.json").read_text(encoding="utf-8"))
        self.assertEqual(
            body,
            {
                "entries": [
                    {
                        "mode": "0600",
                        "owner": "root",
                        "package_path": "launchd/de.example.aaf-gate.plist",
                        "source": INSTALL_MAP_SOURCE,
                        "target": "/Library/LaunchDaemons/de.example.aaf-gate.plist",
                    }
                ],
                "unmapped": "other package files have no documented device target yet",
            },
        )
        prov = json.loads((with_gs / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(package_launchd_expectation(with_gs), (gs["label"], prov["gate_plist_sha256"]))
        plain = Path(self.folder) / "plain"
        build_appliance_package(plane, "konrad", "cfg-1", plain)
        self.assertFalse((plain / "install-map.json").exists())
        with self.assertRaises(ConfigError) as ctx:
            package_launchd_expectation(plain)
        self.assertEqual(ctx.exception.code, "GATE_SERVICE_ABSENT")

    def test_lc07_cli(self):
        store = Store(str(Path(self.folder) / "cfg2.sqlite"))
        plane = ConfigPlane(store, FixedClock("2026-11-12T16:00:00Z"))
        seed_config(
            store,
            (("konrad", "synth-tenant", "human", ("author",)),),
        )
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "kfz-terminannahme", "1.1.0", initial_values(), "rel-1")
        package = Path(self.folder) / "pkg"
        gs = gate_service()
        build_appliance_package(plane, "konrad", "cfg-1", package, gate_service=gs)
        plist = package / "launchd" / "de.example.aaf-gate.plist"
        os.chmod(plist, 0o600)
        completed = subprocess.run(
            [sys.executable, "-m", "framework.cli", "verify-launchd", str(plist), "--package", str(package)],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        body = json.loads(completed.stdout.splitlines()[0])
        if os.getuid() == 0:
            self.assertEqual(completed.returncode, 0)
            self.assertTrue(body["ok"])
        else:
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(body["issues"], ["owner_not_root"])
        plain = Path(self.folder) / "pkg-plain"
        build_appliance_package(plane, "konrad", "cfg-1", plain)
        bad = subprocess.run(
            [
                sys.executable,
                "-m",
                "framework.cli",
                "verify-launchd",
                str(plist),
                "--package",
                str(plain),
            ],
            cwd=CORE,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(
            json.loads(bad.stdout.splitlines()[0]),
            {"error": "GATE_SERVICE_ABSENT", "ok": False},
        )


if __name__ == "__main__":
    unittest.main()
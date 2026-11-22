import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from framework.cli import init_gate_db, main


class CliBackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        os.chmod(self.folder, 0o700)
        self.db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(self.db, "agent-1", "demo-a", out=io.StringIO())
        self._run(
            "gate-principal-add",
            "--db",
            self.db,
            "--principal",
            "cust-1",
            "--tenant",
            "demo-a",
            "--role",
            "customer",
        )
        self._run("gate-stop", "--db", self.db, "--principal", "cust-1")

    def _run(self, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(list(args))
        line = buf.getvalue().splitlines()[0]
        body = json.loads(line) if line else {}
        return code, body

    def _anchor(self, db):
        code, body = self._run("gate-audit", "--db", db, "--tenant", "demo-a", "--anchor")
        self.assertEqual(code, 0)
        return body["anchor"]

    def test_b01_backup_ok(self):
        out = str(Path(self.folder) / "copy.sqlite")
        code, body = self._run("gate-backup", "--db", self.db, "--out", out)
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        manifest = out + ".manifest.json"
        self.assertTrue(os.path.isfile(out))
        self.assertTrue(os.path.isfile(manifest))
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(manifest).st_mode), 0o600)
        source_anchor = self._anchor(self.db)
        self.assertEqual(body["tenants"]["demo-a"]["anchor"], source_anchor)
        with open(out, "rb") as handle:
            data = handle.read()
        import hashlib

        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self.assertEqual(body["db_sha256"], digest)

    def test_b02_out_exists(self):
        out = str(Path(self.folder) / "copy.sqlite")
        Path(out).write_text("x", encoding="utf-8")
        before = Path(out).read_text(encoding="utf-8")
        code, body = self._run("gate-backup", "--db", self.db, "--out", out)
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "out_exists")
        self.assertEqual(Path(out).read_text(encoding="utf-8"), before)
        self.assertFalse(os.path.lexists(out + ".manifest.json"))

    def test_b03_out_relative(self):
        code, body = self._run("gate-backup", "--db", self.db, "--out", "copy.sqlite")
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "out_path_not_absolute")

    def test_b04_out_dir_insecure(self):
        loose = Path(self.folder) / "loose"
        loose.mkdir()
        os.chmod(loose, 0o777)
        out = str(loose / "copy.sqlite")
        code, body = self._run("gate-backup", "--db", self.db, "--out", out)
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "out_dir_insecure")
        self.assertFalse(os.path.lexists(out))

    def test_b05_chain_invalid(self):
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TRIGGER audit_no_update")
        conn.execute("UPDATE audit_events SET outcome='tampered' WHERE sequence=1")
        conn.commit()
        conn.close()
        out = str(Path(self.folder) / "bad.sqlite")
        code, body = self._run("gate-backup", "--db", self.db, "--out", out)
        self.assertEqual(code, 1)
        self.assertEqual(body["error"], "chain_invalid")
        self.assertEqual(body["tenant"], "demo-a")
        self.assertFalse(os.path.lexists(out))
        self.assertFalse(os.path.lexists(out + ".manifest.json"))

    def test_b06_roundtrip(self):
        out = str(Path(self.folder) / "copy.sqlite")
        code, body = self._run("gate-backup", "--db", self.db, "--out", out)
        self.assertEqual(code, 0)
        manifest = json.loads(Path(out + ".manifest.json").read_text(encoding="utf-8"))
        target = str(Path(self.folder) / "restored.sqlite")
        code, body = self._run("gate-restore", "--backup", out, "--db", target)
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
        self.assertEqual(body["tenants"]["demo-a"]["anchor"], manifest["tenants"]["demo-a"]["anchor"])
        self.assertTrue(body["tenants"]["demo-a"]["stopped"])
        status_code, status = self._run("gate-status", "--db", target, "--tenant", "demo-a")
        self.assertEqual(status_code, 0)
        self.assertTrue(status["stopped"])

    def test_b07_db_exists(self):
        out = str(Path(self.folder) / "copy.sqlite")
        self._run("gate-backup", "--db", self.db, "--out", out)
        target = str(Path(self.folder) / "gate.sqlite")
        before = Path(target).read_bytes()
        code, body = self._run("gate-restore", "--backup", out, "--db", target)
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "db_exists")
        self.assertEqual(Path(target).read_bytes(), before)

    def test_b08_backup_hash_mismatch(self):
        out = str(Path(self.folder) / "copy.sqlite")
        self._run("gate-backup", "--db", self.db, "--out", out)
        with open(out, "ab") as handle:
            handle.write(b"x")
        target = str(Path(self.folder) / "restored.sqlite")
        code, body = self._run("gate-restore", "--backup", out, "--db", target)
        self.assertEqual(code, 1)
        self.assertEqual(body["error"], "backup_hash_mismatch")
        self.assertFalse(os.path.lexists(target))

    def test_b09_manifest_missing(self):
        out = str(Path(self.folder) / "copy.sqlite")
        self._run("gate-backup", "--db", self.db, "--out", out)
        os.unlink(out + ".manifest.json")
        target = str(Path(self.folder) / "restored.sqlite")
        code, body = self._run("gate-restore", "--backup", out, "--db", target)
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "manifest_invalid")

    def test_b10_expect_anchor_ok(self):
        out = str(Path(self.folder) / "copy.sqlite")
        self._run("gate-backup", "--db", self.db, "--out", out)
        manifest = json.loads(Path(out + ".manifest.json").read_text(encoding="utf-8"))
        anchor = manifest["tenants"]["demo-a"]["anchor"]
        target = str(Path(self.folder) / "restored.sqlite")
        code, _body = self._run(
            "gate-restore",
            "--backup",
            out,
            "--db",
            target,
            "--expect-anchor",
            "demo-a=" + anchor,
        )
        self.assertEqual(code, 0)

    def test_b11_anchor_mismatch(self):
        out = str(Path(self.folder) / "copy.sqlite")
        self._run("gate-backup", "--db", self.db, "--out", out)
        self._run("gate-stop", "--db", self.db, "--principal", "cust-1")
        new_anchor = self._anchor(self.db)
        manifest = json.loads(Path(out + ".manifest.json").read_text(encoding="utf-8"))
        old_anchor = manifest["tenants"]["demo-a"]["anchor"]
        self.assertNotEqual(old_anchor, new_anchor)
        target = str(Path(self.folder) / "restored.sqlite")
        code, body = self._run(
            "gate-restore",
            "--backup",
            out,
            "--db",
            target,
            "--expect-anchor",
            "demo-a=" + new_anchor,
        )
        self.assertEqual(code, 1)
        self.assertEqual(body["error"], "anchor_mismatch")
        self.assertEqual(body["expected"], new_anchor)
        self.assertEqual(body["actual"], old_anchor)
        self.assertFalse(os.path.lexists(target))

    def test_b12_invalid_anchor(self):
        out = str(Path(self.folder) / "copy.sqlite")
        self._run("gate-backup", "--db", self.db, "--out", out)
        target = str(Path(self.folder) / "restored.sqlite")
        code, body = self._run(
            "gate-restore",
            "--backup",
            out,
            "--db",
            target,
            "--expect-anchor",
            "demo-a=abc",
        )
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "invalid_anchor")

    def test_b13_backup_mode_insecure(self):
        out = str(Path(self.folder) / "copy.sqlite")
        self._run("gate-backup", "--db", self.db, "--out", out)
        os.chmod(out, 0o644)
        target = str(Path(self.folder) / "restored.sqlite")
        code, body = self._run("gate-restore", "--backup", out, "--db", target)
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "backup_mode_insecure")


if __name__ == "__main__":
    unittest.main()
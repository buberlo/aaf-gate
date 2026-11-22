import contextlib
import io
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from framework.cli import init_gate_db, main, serve_gate
from framework.principals import resolve
from framework.store import Store


class GateInitTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = self.folder + "/gate.sqlite"

    def test_gi01_success(self):
        out = io.StringIO()
        code = init_gate_db(self.db, "hermes-a", "demo-a", out=out)
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(out.getvalue().splitlines()[0]),
            {
                "db": self.db,
                "kind": "agent",
                "ok": True,
                "principal": "hermes-a",
                "tenant": "demo-a",
            },
        )
        self.assertEqual(stat.S_IMODE(os.stat(self.db).st_mode), 0o600)
        conn = Store(self.db).connect()
        try:
            principal = resolve(conn, "hermes-a")
        finally:
            conn.close()
        self.assertEqual(principal.kind, "agent")
        self.assertEqual(principal.tenant_id, "demo-a")

    def test_gi02_umask_zero(self):
        prev = os.umask(0)
        try:
            out = io.StringIO()
            db = self.folder + "/umask.sqlite"
            code = init_gate_db(db, "hermes-a", "demo-a", out=out)
            self.assertEqual(code, 0)
            self.assertEqual(stat.S_IMODE(os.stat(db).st_mode), 0o600)
        finally:
            os.umask(prev)

    def test_gi03_db_exists(self):
        Path(self.db).write_bytes(b"x")
        out = io.StringIO()
        code = init_gate_db(self.db, "hermes-a", "demo-a", out=out)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue().splitlines()[0])["error"], "db_exists")
        self.assertEqual(Path(self.db).read_bytes(), b"x")
        if os.name == "posix":
            link = self.folder + "/link.sqlite"
            os.symlink(self.folder + "/missing.sqlite", link)
            out2 = io.StringIO()
            code2 = init_gate_db(link, "hermes-a", "demo-a", out=out2)
            self.assertEqual(code2, 2)
            self.assertEqual(json.loads(out2.getvalue().splitlines()[0])["error"], "db_exists")

    def test_gi04_paths(self):
        out = io.StringIO()
        relative = "gate.sqlite"
        code = init_gate_db(relative, "hermes-a", "demo-a", out=out)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue().splitlines()[0])["error"], "db_path_not_absolute")
        self.assertFalse(os.path.exists(relative))
        missing_parent = self.folder + "/nope/gate.sqlite"
        out2 = io.StringIO()
        code2 = init_gate_db(missing_parent, "hermes-a", "demo-a", out=out2)
        self.assertEqual(code2, 2)
        self.assertEqual(json.loads(out2.getvalue().splitlines()[0])["error"], "db_dir_missing")
        self.assertFalse(os.path.exists(missing_parent))

    def test_gi05_invalid_fields(self):
        cases = [
            ("Bad", "demo-a", "agent", "invalid_principal_id"),
            ("hermes-a", "", "agent", "invalid_tenant_id"),
            ("hermes-a", "demo-a", "human", "principal_kind_forbidden"),
        ]
        for principal, tenant, kind, error in cases:
            with self.subTest(error=error):
                db = self.folder + f"/{error}.sqlite"
                out = io.StringIO()
                code = init_gate_db(db, principal, tenant, kind=kind, out=out)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out.getvalue().splitlines()[0])["error"], error)
                self.assertFalse(os.path.exists(db))

    def test_gi06_chain_with_gate_serve(self):
        sock = self.folder + "/g.sock"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                [
                    "gate-init",
                    "--db",
                    self.db,
                    "--principal",
                    "hermes-a",
                    "--tenant",
                    "demo-a",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(buf.getvalue().splitlines()[0])["ok"])
        ready = threading.Event()
        stop = threading.Event()
        out = io.StringIO()
        result = {}

        def worker():
            result["code"] = serve_gate(
                self.db,
                sock,
                "hermes-a",
                stop=stop,
                ready=ready,
                out=out,
            )

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5))
        first = json.loads(out.getvalue().splitlines()[0])
        self.assertTrue(first["ok"])
        stop.set()
        thread.join(timeout=5)
        self.assertEqual(result.get("code"), 0)


if __name__ == "__main__":
    unittest.main()
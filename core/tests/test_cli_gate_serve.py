import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import threading
import unittest

from framework.audit import verify
from framework.cli import init_gate_db, main, serve_gate
from framework.gatesocket import socket_check
from framework import hermes_mcp
from framework.store import Store

from test_hermes_plugin import load_plugin

NAMES = [hermes_mcp.registered_name("gmail", t) for t in hermes_mcp.GMAIL_MCP_TOOLS]
LABEL_NAMES = [
    hermes_mcp.registered_name("gmail", t)
    for t in hermes_mcp.GMAIL_MCP_TOOLS
    if hermes_mcp.PILOT1_CLASSES[t] == "internal_write"
]
READ_DRAFT_NAMES = [n for n in NAMES if n not in LABEL_NAMES]


class GateServeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = self.folder + "/gate.sqlite"
        self.sock = self.folder + "/g.sock"
        store = Store(self.db)
        store.add_principal("hermes-a", "demo-a", "agent")
        store.add_principal("ada", "demo-a", "human")
        os.chmod(self.db, 0o600)
        self.stop = None
        self.ready = None
        self.thread = None
        self.out = None
        self.code = None
        self.plugin = load_plugin()

    def tearDown(self):
        if self.stop is not None:
            self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def _request(self, tool, args):
        return self.plugin.build_request(tool, args)

    def _start(self, **kwargs):
        self.out = io.StringIO()
        self.ready = threading.Event()
        self.stop = threading.Event()
        kwargs.setdefault("db", self.db)
        kwargs.setdefault("socket_path", self.sock)
        kwargs.setdefault("principal_id", "hermes-a")
        payload = dict(kwargs)
        payload["ready"] = self.ready
        payload["stop"] = self.stop
        payload["out"] = self.out

        def worker():
            self.code = serve_gate(**payload)

        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def test_cs01_serves_pilot1(self):
        self._start()
        self.assertTrue(self.ready.wait(5))
        self.assertEqual(stat.S_IMODE(os.stat(self.sock).st_mode), 0o600)
        check = socket_check(self.sock)
        read_tool = READ_DRAFT_NAMES[0]
        allow = check(self._request(read_tool, {"q": "x"}))
        self.assertEqual(allow["decision"], "allow")
        deny = check(self._request("terminal", {"command": "x"}))
        self.assertEqual(deny["decision"], "deny")
        self.stop.set()
        self.thread.join(timeout=5)
        self.assertEqual(self.code, 0)
        self.assertFalse(os.path.exists(self.sock))
        first = json.loads(self.out.getvalue().splitlines()[0])
        self.assertEqual(
            first,
            {
                "db_mode": "0600",
                "mode": "0600",
                "ok": True,
                "principal": "hermes-a",
                "server": "gmail",
                "socket": self.sock,
                "unattended": False,
            },
        )
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        self.assertTrue(verify(conn, "demo-a")["ok"])
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE tenant_id='demo-a' AND operation='gate_decide'"
        ).fetchone()[0]
        self.assertGreaterEqual(count, 2)
        conn.close()

    def test_cs02_mode_0660_group(self):
        gid = os.getgid()
        self._start(mode="0660", group=str(gid))
        self.assertTrue(self.ready.wait(5))
        st = os.stat(self.sock)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o660)
        self.assertEqual(st.st_gid, gid)
        out = socket_check(self.sock)(self._request(READ_DRAFT_NAMES[0], {"x": 1}))
        self.assertEqual(out["decision"], "allow")
        self.stop.set()
        self.thread.join(timeout=5)

    def test_cs03_store_missing(self):
        missing_db = self.folder + "/missing.sqlite"
        out = io.StringIO()
        code = serve_gate(missing_db, self.sock, "hermes-a", out=out)
        self.assertEqual(code, 2)
        body = json.loads(out.getvalue().splitlines()[0])
        self.assertEqual(body, {"error": "store_missing", "ok": False})
        self.assertFalse(os.path.exists(missing_db))
        self.assertFalse(os.path.exists(self.sock))

    def test_cs04_principal(self):
        out = io.StringIO()
        code = serve_gate(self.db, self.sock, "nobody", out=out)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue().splitlines()[0])["error"], "principal_unknown")
        self.assertFalse(os.path.exists(self.sock))
        out2 = io.StringIO()
        code2 = serve_gate(self.db, self.sock, "ada", out=out2)
        self.assertEqual(code2, 2)
        self.assertEqual(json.loads(out2.getvalue().splitlines()[0])["error"], "principal_kind_forbidden")
        self.assertFalse(os.path.exists(self.sock))

    def test_cs05_invalid_inputs(self):
        cases = [
            {"socket_path": "g.sock", "error": "socket_path_not_absolute"},
            {"mode": "0666", "error": "invalid_socket_mode"},
            {"mode": "0660", "group": "aaf-no-such-group-xyz", "error": "group_unknown"},
            {"server": "Gmail!", "error": "invalid_mcp_name"},
        ]
        for case in cases:
            with self.subTest(case=case):
                out = io.StringIO()
                kwargs = {
                    "db": self.db,
                    "socket_path": self.sock,
                    "principal_id": "hermes-a",
                    "out": out,
                }
                kwargs.update({k: v for k, v in case.items() if k != "error"})
                code = serve_gate(**kwargs)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out.getvalue().splitlines()[0])["error"], case["error"])
                self.assertFalse(os.path.exists(self.sock))

    def test_cs06_insecure_dir(self):
        sock_dir = tempfile.mkdtemp(dir="/tmp")
        sock = sock_dir + "/g.sock"
        os.chmod(sock_dir, 0o777)
        try:
            out = io.StringIO()
            code = serve_gate(self.db, sock, "hermes-a", out=out)
            self.assertEqual(code, 2)
            self.assertEqual(
                json.loads(out.getvalue().splitlines()[0])["error"],
                "socket_dir_insecure",
            )
            self.assertFalse(os.path.exists(sock))
        finally:
            os.chmod(sock_dir, 0o700)

    def _aux_db(self, name):
        path = self.folder + "/" + name
        store = Store(path)
        store.add_principal("hermes-a", "demo-a", "agent")
        return path

    def test_gs_db01_mode_0644(self):
        db = self._aux_db("open644.sqlite")
        os.chmod(db, 0o644)
        out = io.StringIO()
        code = serve_gate(db, self.sock, "hermes-a", out=out)
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(out.getvalue().splitlines()[0]),
            {"error": "store_mode_insecure", "ok": False},
        )
        self.assertFalse(os.path.exists(self.sock))
        self.assertEqual(stat.S_IMODE(os.stat(db).st_mode), 0o644)

    def test_gs_db02_group_or_other_bits(self):
        for mode in (0o640, 0o604):
            with self.subTest(mode=mode):
                db = self._aux_db(f"mode-{oct(mode)}.sqlite")
                os.chmod(db, mode)
                out = io.StringIO()
                code = serve_gate(db, self.folder + f"/s-{oct(mode)}.sock", "hermes-a", out=out)
                self.assertEqual(code, 2)
                self.assertEqual(
                    json.loads(out.getvalue().splitlines()[0])["error"],
                    "store_mode_insecure",
                )

    def test_gs_db03_symlink_db(self):
        link = self.folder + "/gate-link.sqlite"
        os.symlink(self.db, link)
        out = io.StringIO()
        code = serve_gate(link, self.sock, "hermes-a", out=out)
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(out.getvalue().splitlines()[0]),
            {"error": "store_symlink", "ok": False},
        )
        self.assertFalse(os.path.exists(self.sock))

    def test_gs_db04_init_then_serve(self):
        db = self.folder + "/fresh.sqlite"
        out = io.StringIO()
        self.assertEqual(init_gate_db(db, "hermes-a", "demo-a", out=out), 0)
        sock = self.folder + "/fresh.sock"
        ready = threading.Event()
        stop = threading.Event()
        serve_out = io.StringIO()
        result = {}

        def worker():
            result["code"] = serve_gate(
                db,
                sock,
                "hermes-a",
                stop=stop,
                ready=ready,
                out=serve_out,
            )

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5))
        self.assertTrue(json.loads(serve_out.getvalue().splitlines()[0])["ok"])
        stop.set()
        thread.join(timeout=5)
        self.assertEqual(result.get("code"), 0)

    def test_gs_db05_insecure_db_directory(self):
        sock_dir = tempfile.mkdtemp(dir="/tmp")
        for mode in (0o770, 0o757):
            with self.subTest(mode=mode):
                bad = tempfile.mkdtemp(dir="/tmp")
                os.chmod(bad, mode)
                db = bad + "/gate.sqlite"
                out_init = io.StringIO()
                self.assertEqual(init_gate_db(db, "hermes-a", "demo-a", out=out_init), 0)
                sock = sock_dir + f"/g-{mode}.sock"
                out = io.StringIO()
                code = serve_gate(db, sock, "hermes-a", out=out)
                self.assertEqual(code, 2)
                self.assertEqual(
                    json.loads(out.getvalue().splitlines()[0]),
                    {"error": "store_dir_insecure", "ok": False},
                )
                self.assertFalse(os.path.exists(sock))
                os.chmod(bad, 0o700)

    def test_gs_db06_restrictive_but_valid_dirs(self):
        for mode in (0o750, 0o755):
            with self.subTest(mode=mode):
                folder = tempfile.mkdtemp(dir="/tmp")
                os.chmod(folder, mode)
                db = folder + "/gate.sqlite"
                sock = folder + "/g.sock"
                out_init = io.StringIO()
                self.assertEqual(init_gate_db(db, "hermes-a", "demo-a", out=out_init), 0)
                ready = threading.Event()
                stop = threading.Event()
                serve_out = io.StringIO()
                result = {}

                def worker():
                    result["code"] = serve_gate(
                        db,
                        sock,
                        "hermes-a",
                        stop=stop,
                        ready=ready,
                        out=serve_out,
                    )

                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                self.assertTrue(ready.wait(5))
                self.assertTrue(json.loads(serve_out.getvalue().splitlines()[0])["ok"])
                stop.set()
                thread.join(timeout=5)
                self.assertEqual(result.get("code"), 0)
                os.chmod(folder, 0o700)

    def test_gs_db07_db_mode_in_start_line(self):
        db = self.folder + "/mode-report.sqlite"
        out = io.StringIO()
        self.assertEqual(init_gate_db(db, "hermes-a", "demo-a", out=out), 0)
        ready = threading.Event()
        stop = threading.Event()
        serve_out = io.StringIO()
        sock = self.folder + "/mode.sock"

        def worker():
            serve_gate(db, sock, "hermes-a", stop=stop, ready=ready, out=serve_out)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(5))
        self.assertEqual(
            json.loads(serve_out.getvalue().splitlines()[0])["db_mode"],
            "0600",
        )
        stop.set()
        thread.join(timeout=5)
        db_ro = self.folder + "/readonly.sqlite"
        out2 = io.StringIO()
        self.assertEqual(init_gate_db(db_ro, "hermes-a", "demo-a", out=out2), 0)
        os.chmod(db_ro, 0o400)
        ready2 = threading.Event()
        stop2 = threading.Event()
        serve_out2 = io.StringIO()
        sock2 = self.folder + "/mode2.sock"

        def worker2():
            serve_gate(db_ro, sock2, "hermes-a", stop=stop2, ready=ready2, out=serve_out2)

        thread2 = threading.Thread(target=worker2, daemon=True)
        thread2.start()
        self.assertTrue(ready2.wait(5))
        self.assertEqual(
            json.loads(serve_out2.getvalue().splitlines()[0])["db_mode"],
            "0400",
        )
        stop2.set()
        thread2.join(timeout=5)

    def test_cs07_main_wiring(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                [
                    "gate-serve",
                    "--db",
                    self.folder + "/missing.sqlite",
                    "--socket",
                    self.sock,
                    "--principal",
                    "hermes-a",
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(buf.getvalue().splitlines()[0]),
            {"error": "store_missing", "ok": False},
        )


if __name__ == "__main__":
    unittest.main()
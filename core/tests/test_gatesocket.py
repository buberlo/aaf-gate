import ast
import json
import os
import socket
import sqlite3
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

from framework.audit import verify
from framework.clock import FixedClock
from framework.gatekeeper import GateKeeper
from framework.gatesocket import MAX_FRAME, GateServer, deny, observe_fail, socket_check, socket_observe
from framework.hermes_adapter import build_observation
from framework.hermes_adapter import make_pre_tool_call
from framework.store import Store

from test_gate import sample_request

E2E_POLICY = {
    "schema_version": "gate-policy-0.1-draft",
    "tool_classes": {
        "gmail_read:call": "read",
        "gmail_send:call": "send",
    },
    "levels": {"read": "L4", "send": "L3"},
}


class GateSocketTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.path = self.folder + "/g.sock"
        self.server = None

    def tearDown(self):
        if self.server is not None:
            self.server.close()

    def _start(self, check, observe=None):
        self.server = GateServer(self.path, check, observe)
        self.server.start()
        time.sleep(0.05)

    def test_gs01_round_trip(self):
        seen = []

        def check(req):
            seen.append(req)
            return {
                "decision": "allow",
                "reason": "ok",
                "action_class": "read",
                "effective_level": "L4",
                "request_hash": "sha256:" + "a" * 64,
                "policy_hash": "sha256:" + "b" * 64,
                "audit_sequence": 1,
            }

        self._start(check)
        req = sample_request(tool="gmail_read", action="call")
        out = socket_check(self.path)(req)
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(seen, [req])

    def test_gs02_socket_mode_0600(self):
        self._start(lambda req: deny("x"))
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_gs03_no_server_unreachable(self):
        out = socket_check(self.path + ".missing")(sample_request())
        self.assertEqual(out["reason"], "core_unreachable")

    def test_gs04_client_timeout(self):
        def slow(req):
            time.sleep(1.0)
            return {"decision": "allow", "reason": "ok"}

        self._start(slow)
        out = socket_check(self.path, timeout=0.2)(sample_request())
        self.assertEqual(out["reason"], "core_timeout")

    def test_gs05_invalid_frame_then_ok(self):
        captured = []

        def check(req):
            captured.append(req)
            return {"decision": "allow", "reason": "ok"}

        self._start(check)
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.connect(self.path)
        raw.sendall(b"kein json\n")
        line = raw.recv(4096)
        raw.close()
        bad = json.loads(line.decode("utf-8").strip())
        self.assertEqual(bad["reason"], "invalid_frame")
        out = socket_check(self.path)(sample_request())
        self.assertEqual(out["decision"], "allow")
        self.assertEqual(len(captured), 1)

    def test_gs06_extra_key_invalid_frame(self):
        called = []

        def check(req):
            called.append(req)
            return {"decision": "allow", "reason": "ok"}

        self._start(check)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        frame = {"request": sample_request(), "principal_id": "ada"}
        sock.sendall((json.dumps(frame) + "\n").encode("utf-8"))
        line = sock.recv(4096)
        sock.close()
        out = json.loads(line.decode("utf-8").strip())
        self.assertEqual(out["reason"], "invalid_frame")
        self.assertEqual(called, [])

    def test_gs07_oversize_without_newline(self):
        self._start(lambda req: {"decision": "allow", "reason": "ok"})
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(self.path)
        sock.sendall(b"x" * (MAX_FRAME + 10))
        line = sock.recv(4096)
        sock.close()
        out = json.loads(line.decode("utf-8").strip())
        self.assertEqual(out["reason"], "invalid_frame")

    def test_gs08_check_error_and_bad_decision(self):
        def boom(req):
            raise RuntimeError("fail")

        self._start(boom)
        out = socket_check(self.path)(sample_request())
        self.assertEqual(out["reason"], "internal_error")

        def weird(req):
            return {"decision": "vielleicht", "reason": "x"}

        if self.server is not None:
            self.server.close()
        self._start(weird)
        out2 = socket_check(self.path)(sample_request())
        self.assertEqual(out2["reason"], "invalid_response")

    def test_gs09_close_idempotent(self):
        self._start(lambda req: {"decision": "allow", "reason": "ok"})
        self.assertTrue(os.path.exists(self.path))
        self.server.close()
        self.assertFalse(os.path.exists(self.path))
        out = socket_check(self.path)(sample_request())
        self.assertEqual(out["reason"], "core_unreachable")
        self.server.close()

    def test_gs10_e2e_hermes_socket(self):
        folder = tempfile.mkdtemp(dir="/tmp")
        db = str(Path(folder) / "s.sqlite")
        store = Store(db)
        store.add_principal("hermes-a", "demo-a", "agent")
        keeper = GateKeeper(store, FixedClock("2026-11-14T17:00:00Z"))
        sock_path = folder + "/g.sock"
        server = GateServer(
            sock_path,
            lambda req: keeper.check("hermes-a", req, E2E_POLICY),
        )
        server.start()
        time.sleep(0.05)
        check = socket_check(sock_path, 2.0)
        cb = make_pre_tool_call(check)
        self.assertIsNone(cb("gmail_read", {"q": "inbox"}))
        approve = cb("gmail_send", {"to": "x@y.invalid"})
        self.assertEqual(approve["action"], "approve")
        server.close()
        blocked = cb("gmail_read", {"q": "inbox"})
        self.assertEqual(
            blocked,
            {"action": "block", "message": "Core: denied (core_unreachable)"},
        )
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE tenant_id='demo-a'"
        ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertTrue(verify(conn, "demo-a")["ok"])
        conn.close()
        server.close()

    def test_gs11_unix_only(self):
        path = Path(__file__).resolve().parents[1] / "framework" / "gatesocket.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed = frozenset({"json", "os", "socket", "stat", "threading"})
        forbidden_modules = frozenset({"ssl", "urllib", "http", "importlib"})
        forbidden_calls = frozenset({"create_connection", "getaddrinfo", "gethostbyname"})
        af_unix_seen = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    base = alias.name.split(".")[0]
                    self.assertNotIn(base, forbidden_modules)
                    if not alias.name.startswith("framework"):
                        self.assertIn(base, allowed)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                base = module.split(".")[0] if module else ""
                if base:
                    self.assertNotIn(base, forbidden_modules)
                if module and not module.startswith("framework"):
                    self.assertIn(base, allowed)
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "socket":
                    if node.attr.startswith("AF_"):
                        self.assertEqual(node.attr, "AF_UNIX")
                        af_unix_seen = True
                    if node.attr in forbidden_calls:
                        self.fail(node.attr)
            elif isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in forbidden_calls:
                    self.fail(name)
        self.assertTrue(af_unix_seen)

    def test_gs12_observation_frames(self):
        obs = build_observation("t", "gate_unseen", None, "sha256:" + "1" * 64)
        received = []

        def observe(o):
            received.append(o)
            return {"ok": True, "reason": None, "audit_sequence": 9}

        self._start(lambda r: {"decision": "allow"})
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        sock.sendall((json.dumps({"observation": obs}) + "\n").encode("utf-8"))
        line = sock.recv(4096)
        sock.close()
        out = json.loads(line.decode("utf-8").strip())
        self.assertEqual(out["reason"], "invalid_frame")
        if self.server is not None:
            self.server.close()
        self._start(lambda r: {"decision": "allow"}, observe=observe)
        report = socket_observe(self.path)
        out2 = report(obs)
        self.assertTrue(out2["ok"])
        self.assertEqual(received, [obs])
        sock2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock2.connect(self.path)
        sock2.sendall(
            (json.dumps({"request": sample_request(), "observation": obs}) + "\n").encode("utf-8")
        )
        bad = json.loads(sock2.recv(4096).decode("utf-8").strip())
        sock2.close()
        self.assertEqual(bad["reason"], "invalid_frame")

    def test_gs13_observe_internal_error_and_unreachable(self):
        def boom(o):
            raise RuntimeError("x")

        self._start(lambda r: {"decision": "allow"}, observe=boom)
        out = socket_observe(self.path)(build_observation("t", "gate_unseen", None, "sha256:" + "0" * 64))
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "internal_error")
        missing = self.path + ".no"
        out2 = socket_observe(missing)(build_observation("t", "gate_unseen", None, "sha256:" + "0" * 64))
        self.assertEqual(out2["reason"], "core_unreachable")

    def test_gs14_socket_observe_invalid_response(self):
        import threading

        path = self.folder + "/bad.sock"
        if os.path.exists(path):
            os.unlink(path)
        listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listen.bind(path)
        listen.listen()
        stopped = threading.Event()

        def serve():
            while not stopped.is_set():
                try:
                    listen.settimeout(0.5)
                    conn, _ = listen.accept()
                except (socket.timeout, OSError):
                    continue
                _ = conn.recv(4096)
                conn.sendall(b'{"decision": "allow"}\n')
                conn.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            out = socket_observe(path)(build_observation("t", "gate_unseen", None, "sha256:" + "0" * 64))
            self.assertEqual(out["reason"], "invalid_response")
        finally:
            stopped.set()
            listen.close()
            if os.path.exists(path):
                os.unlink(path)
        path2 = self.folder + "/raw.sock"
        listen2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listen2.bind(path2)
        listen2.listen()

        def serve_raw():
            conn, _ = listen2.accept()
            conn.recv(4096)
            conn.sendall(b"not-json\n")
            conn.close()

        thread2 = threading.Thread(target=serve_raw, daemon=True)
        thread2.start()
        try:
            out2 = socket_observe(path2)(build_observation("t", "gate_unseen", None, "sha256:" + "0" * 64))
            self.assertEqual(out2["reason"], "invalid_response")
        finally:
            listen2.close()
            if os.path.exists(path2):
                os.unlink(path2)
        self.assertEqual(observe_fail("x"), {"ok": False, "reason": "x", "audit_sequence": None})


if __name__ == "__main__":
    unittest.main()
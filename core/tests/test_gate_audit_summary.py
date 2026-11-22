import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework import hermes_mcp
from framework.audit import append as audit_append
from framework.canon import SCHEMA_VERSION, hash_value
from framework.cli import init_gate_db, main
from framework.clock import Clock
from framework.gatekeeper import GATE_OPERATION, GateKeeper
from framework import hermes_adapter
from framework.store import Store

from test_hermes_adapter import build_observation


class SequenceClock(Clock):
    def __init__(self, stamps):
        self.stamps = list(stamps)
        self.index = 0

    def now(self):
        if self.index < len(self.stamps):
            value = self.stamps[self.index]
            self.index += 1
            return value
        return self.stamps[-1]


class GateAuditSummaryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(self.db, "hermes-a", "demo-a", out=io.StringIO())
        self.store = Store(self.db)
        self.policy = hermes_mcp.pilot1_policy()

    def _run(self, *extra):
        base = ["gate-audit", "--db", self.db, "--tenant", "demo-a"]
        base.extend(extra)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(base)
        body = json.loads(buf.getvalue().splitlines()[0])
        return code, body

    def test_su01_class_counts(self):
        keeper = GateKeeper(self.store, Clock())
        draft = hermes_mcp.registered_name("gmail", "create_draft")
        read = hermes_mcp.registered_name("gmail", "search_threads")
        for _ in range(2):
            keeper.check(
                "hermes-a",
                hermes_adapter.build_request(draft, {"body": "x"}),
                self.policy,
            )
        keeper.check(
            "hermes-a",
            hermes_adapter.build_request(read, {"query": "is:unread"}),
            self.policy,
        )
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertTrue(body["ok"])
        self.assertEqual(body["summary"]["total"], 3)
        self.assertEqual(body["summary"]["by_class"]["draft"]["allow"], 2)
        self.assertEqual(body["summary"]["by_class"]["read"]["allow"], 1)

    def test_su02_unknown_and_no_context(self):
        keeper = GateKeeper(self.store, Clock())
        unknown = "mcp_gmail_send_message"
        keeper.check(
            "hermes-a",
            hermes_adapter.build_request(unknown, {"x": 1}),
            self.policy,
        )
        conn = self.store.connect()
        conn.execute("BEGIN IMMEDIATE")
        audit_append(
            conn,
            "2026-11-14T16:00:00Z",
            schema_version=SCHEMA_VERSION,
            tenant_id="demo-a",
            actor_id="hermes-a",
            operation=GATE_OPERATION,
            outcome="allow",
            principal_kind="agent",
        )
        conn.execute("COMMIT")
        conn.close()
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertEqual(body["summary"]["by_class"]["action_class_unknown"]["deny"], 1)
        self.assertEqual(body["summary"]["by_class"]["no_context"]["allow"], 1)
        self.assertEqual(body["summary"]["total"], 2)

    def test_su03_time_window(self):
        stamps = ["2026-11-14T08:00:00Z", "2026-11-14T09:00:00Z", "2026-11-14T10:00:00Z"]
        keeper = GateKeeper(self.store, SequenceClock(stamps))
        tool = hermes_mcp.registered_name("gmail", "search_threads")
        req = hermes_adapter.build_request(tool, {"query": "a"})
        for _ in range(3):
            keeper.check("hermes-a", req, self.policy)
        code, body = self._run(
            "--summary",
            "--since",
            "2026-11-14T09:00:00Z",
            "--until",
            "2026-11-14T10:00:00Z",
        )
        self.assertEqual(code, 0)
        self.assertEqual(body["summary"]["total"], 1)

    def test_su04_time_errors(self):
        code, body = self._run("--summary", "--since", "gestern")
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "time_invalid")
        code, body = self._run(
            "--summary",
            "--since",
            "2026-11-14T10:00:00Z",
            "--until",
            "2026-11-14T09:00:00Z",
        )
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "time_range_invalid")
        code, body = self._run("--since", "2026-11-14T09:00:00Z")
        self.assertEqual(code, 2)
        self.assertEqual(body["error"], "time_requires_summary")

    def test_su05_many_events(self):
        keeper = GateKeeper(self.store, Clock())
        tool = hermes_mcp.registered_name("gmail", "search_threads")
        req = hermes_adapter.build_request(tool, {"query": "bulk"})
        policy = dict(self.policy)
        policy["limits"] = {k: dict(v) for k, v in self.policy["limits"].items()}
        policy["limits"]["read"]["max_calls"] = 1005
        for _ in range(1005):
            keeper.check("hermes-a", req, policy)
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertEqual(body["summary"]["total"], 1005)

    def test_su06_no_secrets_observe_excluded(self):
        secret = "secret-thread-4711"
        tool = hermes_mcp.registered_name("gmail", "create_draft")
        req = {
            "schema_version": "gate-request-0.1-draft",
            "runtime": "local",
            "tool": tool,
            "action": "call",
            "target": secret,
            "content_hash": hash_value({"body": secret}),
        }
        keeper = GateKeeper(self.store, Clock())
        keeper.check("hermes-a", req, self.policy)
        keeper.observe("hermes-a", build_observation("gmail", "gate_mismatch", "sha256:" + "a" * 64, "sha256:" + "b" * 64))
        code, body = self._run("--summary")
        self.assertEqual(code, 0)
        self.assertEqual(body["summary"]["total"], 1)
        rendered = json.dumps(body, ensure_ascii=False)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("payload_hash", rendered)

    def test_su07_non_summary_unchanged(self):
        keeper = GateKeeper(self.store, Clock())
        tool = hermes_mcp.registered_name("gmail", "search_threads")
        keeper.check("hermes-a", hermes_adapter.build_request(tool, {"q": "x"}), self.policy)
        code, body = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(set(body.keys()), {"events", "ok", "tenant", "verify"})


if __name__ == "__main__":
    unittest.main()
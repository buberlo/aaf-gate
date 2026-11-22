import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework import hermes_mcp
from framework.audit import append as audit_append, verify
from framework.canon import SCHEMA_VERSION, canonical_bytes, hash_value
from framework.cli import init_gate_db, main
from framework.clock import FixedClock
from framework.gatekeeper import GATE_OPERATION, OBSERVE_OPERATION, GateKeeper
from framework import hermes_adapter
from framework.store import Store

from test_gate import sample_request
from test_hermes_adapter import build_observation


class GateAuditContextTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(self.db, "hermes-a", "demo-a", out=io.StringIO())
        self.store = Store(self.db)
        self.keeper = GateKeeper(self.store, FixedClock("2026-11-14T15:00:00Z"))
        self.policy = hermes_mcp.pilot1_policy()

    def _row(self, tenant="demo-a"):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM audit_events WHERE tenant_id=? ORDER BY sequence DESC LIMIT 1",
            (tenant,),
        ).fetchone()
        conn.close()
        return row

    def _audit_text(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        parts = []
        for row in conn.execute("SELECT * FROM audit_events"):
            for key in row.keys():
                value = row[key]
                if value is not None:
                    parts.append(str(value))
        conn.close()
        return " ".join(parts)

    def test_ac01_create_draft_context_allow(self):
        tool = hermes_mcp.registered_name("gmail", "create_draft")
        req = hermes_adapter.build_request(tool, {"body": "x", "to": "y@example.invalid"})
        out = self.keeper.check("hermes-a", req, self.policy)
        self.assertEqual(out["decision"], "allow")
        row = self._row()
        expected = canonical_bytes(
            {
                "action": "call",
                "action_class": "draft",
                "content_hash": req["content_hash"],
                "tool": tool,
            }
        ).decode("utf-8")
        self.assertEqual(row["before_state"], expected)
        self.assertEqual(row["outcome"], "allow")

    def test_ac02_unknown_tool_action_class_null(self):
        tool = "mcp_gmail_send_message"
        req = hermes_adapter.build_request(tool, {"body": "x"})
        out = self.keeper.check("hermes-a", req, self.policy)
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "action_class_unknown")
        row = self._row()
        expected = canonical_bytes(
            {
                "action": "call",
                "action_class": None,
                "content_hash": req["content_hash"],
                "tool": tool,
            }
        ).decode("utf-8")
        self.assertEqual(row["before_state"], expected)
        self.assertEqual(row["error_code"], "action_class_unknown")

    def test_ac03_invalid_request_no_before(self):
        bad = sample_request()
        del bad["target"]
        out = self.keeper.check("hermes-a", bad, self.policy)
        self.assertEqual(out["decision"], "deny")
        self.assertEqual(out["reason"], "invalid_request")
        row = self._row()
        self.assertIsNone(row["before_state"])

    def test_ac04_no_plaintext_target_or_content(self):
        secret = "secret-thread-4711"
        tool = hermes_mcp.registered_name("gmail", "create_draft")
        content_hash = hash_value({"body": secret, "to": "z@example.invalid"})
        req = {
            "schema_version": "gate-request-0.1-draft",
            "runtime": "local",
            "tool": tool,
            "action": "call",
            "target": secret,
            "content_hash": content_hash,
        }
        self.assertNotEqual(content_hash, secret)
        self.keeper.check("hermes-a", req, self.policy)
        blob = self._audit_text()
        self.assertNotIn(secret, blob)

    def test_ac05_verify_mixed_events(self):
        tool = hermes_mcp.registered_name("gmail", "search_threads")
        req = hermes_adapter.build_request(tool, {"query": "is:unread"})
        self.keeper.check("hermes-a", req, self.policy)
        obs = build_observation(
            "gmail",
            "gate_mismatch",
            "sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
        )
        self.keeper.observe("hermes-a", obs)
        conn = self.store.connect()
        result = verify(conn, "demo-a")
        conn.close()
        self.assertTrue(result["ok"])
        self.assertFalse(result["head_mismatch"])

    def test_ac06_gate_audit_context_output(self):
        tool = hermes_mcp.registered_name("gmail", "create_draft")
        req = hermes_adapter.build_request(tool, {"body": "x"})
        self.keeper.check("hermes-a", req, self.policy)
        obs = build_observation(
            "gmail",
            "gate_unseen",
            None,
            "sha256:" + "c" * 64,
        )
        self.keeper.observe("hermes-a", obs)
        conn = self.store.connect()
        conn.execute("BEGIN IMMEDIATE")
        audit_append(
            conn,
            FixedClock("2026-11-14T15:01:00Z").now(),
            schema_version=SCHEMA_VERSION,
            tenant_id="demo-a",
            actor_id="hermes-a",
            operation=GATE_OPERATION,
            outcome="allow",
            principal_kind="agent",
            before="kaputt",
        )
        conn.execute("COMMIT")
        conn.close()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", self.db, "--tenant", "demo-a"])
        self.assertEqual(code, 0)
        body = json.loads(buf.getvalue().splitlines()[0])
        decide = [e for e in body["events"] if e["operation"] == "gate_decide"]
        observe = [e for e in body["events"] if e["operation"] == "gate_observe"]
        self.assertEqual(
            decide[0]["context"],
            {
                "action": "call",
                "action_class": "draft",
                "content_hash": hash_value({"body": "x"}),
                "tool": tool,
            },
        )
        self.assertIsNone(decide[-1]["context"])
        self.assertIsNone(observe[0]["context"])


if __name__ == "__main__":
    unittest.main()
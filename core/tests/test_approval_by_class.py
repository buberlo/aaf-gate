import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from framework.baseline import _summary_invalid, compute
from framework.canon import SCHEMA_VERSION, canonical_bytes, hash_value
from framework.cli import _gate_audit_event_context, init_gate_db, main
from framework.clock import FixedClock
from framework.gatekeeper import GATE_OPERATION, GateKeeper
from framework.audit import append as audit_append
from framework.store import Store

from test_gate import sample_policy, sample_request
from test_hermes_adapter import build_observation
from test_pilot_baseline import _timesheet, _verified_summary


L3_POLICY = {
    "schema_version": "gate-policy-0.1-draft",
    "tool_classes": {
        "gmail:*": "send",
        "files:*": "internal_write",
    },
    "levels": {
        "send": "L3",
        "internal_write": "L3",
    },
}


class ApprovalByClassTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(dir="/tmp")
        self.db = str(Path(self.folder) / "gate.sqlite")
        init_gate_db(self.db, "hermes-a", "demo-a", out=io.StringIO())
        self.store = Store(self.db)
        self.keeper = GateKeeper(self.store, FixedClock("2026-11-14T15:00:00Z"))
        self.clock = FixedClock("2026-11-14T15:00:00Z")

    def _summary(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["gate-audit", "--db", self.db, "--tenant", "demo-a", "--summary"])
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue().splitlines()[0])

    def test_ac01_decide_before_state_includes_content_hash(self):
        req = sample_request(action="send")
        out = self.keeper.check("hermes-a", req, sample_policy())
        self.assertEqual(out["decision"], "needs_approval")
        conn = self.store.connect()
        row = conn.execute(
            "SELECT before_state FROM audit_events WHERE sequence=1"
        ).fetchone()
        conn.close()
        data = json.loads(row[0])
        self.assertEqual(
            set(data.keys()),
            {"action", "action_class", "content_hash", "tool"},
        )
        self.assertEqual(data["content_hash"], req["content_hash"])

    def test_ac02_granted_linked_to_class(self):
        req = sample_request(action="send")
        h = req["content_hash"]
        self.keeper.check("hermes-a", req, sample_policy())
        self.keeper.observe("hermes-a", build_observation("gmail", "approval_granted", h, h))
        body = self._summary()
        self.assertEqual(body["summary"]["approvals"], {"granted": 1, "denied": 0})
        self.assertEqual(body["summary"]["approvals_by_class"], {"send": {"granted": 1, "denied": 0}})

    def test_ac03_denied_two_classes(self):
        req_send = sample_request(action="send", content_hash=hash_value({"a": 1}))
        req_write = sample_request(
            tool="files",
            action="write",
            content_hash=hash_value({"b": 2}),
        )
        self.keeper.check("hermes-a", req_send, L3_POLICY)
        self.keeper.observe(
            "hermes-a",
            build_observation("gmail", "approval_denied", req_send["content_hash"], req_send["content_hash"]),
        )
        self.keeper.check("hermes-a", req_write, L3_POLICY)
        self.keeper.observe(
            "hermes-a",
            build_observation("files", "approval_granted", req_write["content_hash"], req_write["content_hash"]),
        )
        body = self._summary()
        self.assertEqual(
            body["summary"]["approvals_by_class"],
            {
                "send": {"granted": 0, "denied": 1},
                "internal_write": {"granted": 1, "denied": 0},
            },
        )

    def test_ac04_unlinked_wrong_hash(self):
        req = sample_request(action="send")
        self.keeper.check("hermes-a", req, sample_policy())
        other = hash_value({"other": True})
        self.keeper.observe("hermes-a", build_observation("gmail", "approval_granted", other, other))
        body = self._summary()
        self.assertEqual(body["summary"]["approvals_by_class"], {"unlinked": {"granted": 1, "denied": 0}})

    def test_ac05_one_decide_two_observations(self):
        req = sample_request(action="send")
        h = req["content_hash"]
        self.keeper.check("hermes-a", req, sample_policy())
        self.keeper.observe("hermes-a", build_observation("gmail", "approval_granted", h, h))
        self.keeper.observe("hermes-a", build_observation("gmail", "approval_denied", h, h))
        body = self._summary()
        self.assertEqual(
            body["summary"]["approvals_by_class"],
            {"send": {"granted": 1, "denied": 0}, "unlinked": {"granted": 0, "denied": 1}},
        )

    def test_ac06_legacy_decide_context_unlinked(self):
        req = sample_request(action="send")
        h = req["content_hash"]
        legacy_before = canonical_bytes(
            {"action": "send", "action_class": "send", "tool": "gmail"}
        ).decode("utf-8")
        conn = self.store.connect()
        conn.execute("BEGIN IMMEDIATE")
        audit_append(
            conn,
            self.clock.now(),
            schema_version=SCHEMA_VERSION,
            tenant_id="demo-a",
            actor_id="hermes-a",
            operation=GATE_OPERATION,
            outcome="needs_approval",
            principal_kind="agent",
            before=legacy_before,
            payload_hash=h,
        )
        conn.execute("COMMIT")
        conn.close()
        ctx = _gate_audit_event_context("gate_decide", legacy_before)
        self.assertEqual(ctx, {"action": "send", "action_class": "send", "tool": "gmail"})
        self.keeper.observe("hermes-a", build_observation("gmail", "approval_granted", h, h))
        body = self._summary()
        self.assertIn("send", body["summary"]["by_class"])
        self.assertEqual(body["summary"]["approvals_by_class"], {"unlinked": {"granted": 1, "denied": 0}})

    def test_ac07_baseline_approval_rates(self):
        summary = _verified_summary({"read": {"allow": 1}})
        inner = dict(summary["summary"])
        inner["approvals"] = {"granted": 4, "denied": 2}
        inner["approvals_by_class"] = {
            "read": {"granted": 3, "denied": 1},
            "draft": {"granted": 1, "denied": 1},
            "unlinked": {"granted": 0, "denied": 0},
        }
        summary["summary"] = inner
        timesheet = _timesheet({"draft": [], "internal_write": [], "read": []})
        result = compute(summary, timesheet, 5)
        self.assertEqual(result["classes"]["read"]["approvals"], {"granted": 3, "denied": 1, "rate": 0.75})
        self.assertEqual(result["classes"]["draft"]["approvals"], {"granted": 1, "denied": 1, "rate": 0.5})
        self.assertEqual(result["classes"]["internal_write"]["approvals"]["rate"], None)
        self.assertEqual(result["approvals_unlinked"], 0)
        result_plain = compute(_verified_summary({"read": {"allow": 1}}), timesheet, 5)
        self.assertIsNone(result_plain["classes"]["read"]["approvals"])
        self.assertIsNone(result_plain["approvals_unlinked"])

    def test_ac08_summary_invalid_shapes(self):
        summary = _verified_summary({"send": {"allow": 1}})
        inner = dict(summary["summary"])
        inner["approvals"] = {"granted": 1, "denied": 0}
        base = dict(summary)
        base["summary"] = inner
        cases = [
            {**inner, "approvals_by_class": {"send": {"granted": 0}}},
            {**inner, "approvals_by_class": {"send": {"granted": -1, "denied": 0}}},
            {
                **inner,
                "approvals_by_class": {"send": {"granted": 2, "denied": 0}},
            },
            {**inner, "approvals_by_class": {"send": {"granted": True, "denied": 0}}},
        ]
        for broken in cases:
            s = dict(summary)
            s["summary"] = broken
            with self.assertRaises(ValueError) as caught:
                _summary_invalid(s)
            self.assertEqual(str(caught.exception), "summary_invalid")


if __name__ == "__main__":
    unittest.main()
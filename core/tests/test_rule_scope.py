import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.errors import CommandError
from framework.rules import load_rules
from framework.service import ROOT, Service
from test_i1 import fixture

FIXTURES = ROOT / "fixtures"


class RuleScopeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = str(Path(self.folder) / "scope.sqlite")

    def _service(self, provider_tenant=None):
        service = Service.open(self.path, provider_tenant=provider_tenant)
        for principal_id, tenant_id, kind, roles in (
            ("regelautor", "demo-a", "human", ("author",)),
            ("fremdautor", "demo-b", "human", ("author",)),
            ("op-a", "demo-a", "human", ("operator", "reviewer")),
            ("rulesagent", "demo-a", "agent", ("author",)),
        ):
            service.store.add_principal(principal_id, tenant_id, kind)
            for role in roles:
                service.store.bind_role(principal_id, role)
        return service

    def _rules_body(self, version: str, prefix: str) -> dict:
        body = dict(load_rules(FIXTURES / "material-prep-1.json"))
        body["rules_version"] = version
        body["draft_prefix"] = prefix
        return body

    def _rule_count(self) -> int:
        conn = sqlite3.connect(self.path)
        count = conn.execute("SELECT COUNT(*) FROM rule_sets").fetchone()[0]
        conn.close()
        return count

    def _command(self, version: str, prefix: str, key: str = "rs-key") -> dict:
        return {
            "operation": "activate_rules",
            "idempotency_key": key,
            "payload": {"activate": True, "body": self._rules_body(version, prefix)},
        }

    def _withdraw(self, version: str, key: str = "wd-key") -> dict:
        return {
            "operation": "withdraw_rules",
            "idempotency_key": key,
            "payload": {"withdraw": True, "rules_version": version},
        }

    def test_rs01_foreign_author_forbidden(self):
        service = self._service(provider_tenant="demo-a")
        before = self._rule_count()
        with self.assertRaises(CommandError) as ctx:
            service.command("fremdautor", self._command("rs01-rules", "RS01."))
        self.assertEqual(ctx.exception.code, "FORBIDDEN")
        self.assertEqual(self._rule_count(), before)
        conn = sqlite3.connect(self.path)
        audit = conn.execute(
            "SELECT outcome, error_code FROM audit_events WHERE actor_id='fremdautor' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(audit, ("rejected", "FORBIDDEN"))

    def test_rs02_provider_author_activates(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("rs02-rules", "RS02."))
        conn = sqlite3.connect(self.path)
        row = conn.execute(
            "SELECT rules_version FROM rule_sets WHERE rules_version=?",
            ("rs02-rules",),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_rs03_missing_provider_tenant_fail_closed(self):
        service = self._service()
        before = self._rule_count()
        with self.assertRaises(CommandError) as ctx:
            service.command("regelautor", self._command("rs03-rules", "RS03."))
        self.assertEqual(ctx.exception.code, "FORBIDDEN")
        self.assertEqual(self._rule_count(), before)

    def test_rs04_open_passes_provider_tenant(self):
        service = Service.open(self.path, provider_tenant="demo-a")
        self.assertEqual(service.provider_tenant, "demo-a")

    def test_rs05_foreign_retry_not_replayed(self):
        service = self._service(provider_tenant="demo-a")
        command = self._command("rs05-rules", "RS05.", key="rs5")
        service.command("regelautor", command)
        with self.assertRaises(CommandError) as ctx:
            service.command("fremdautor", command)
        self.assertEqual(ctx.exception.code, "FORBIDDEN")
        conn = sqlite3.connect(self.path)
        replayed = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE actor_id='fremdautor' AND outcome='replayed'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(replayed, 0)

    def test_wr01_withdraw_sets_state_and_audits(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("wr-rules", "WR."))
        service.command("regelautor", self._withdraw("wr-rules", key="wr1"))
        conn = sqlite3.connect(self.path)
        row = conn.execute(
            "SELECT state FROM rule_sets WHERE rules_version=?",
            ("wr-rules",),
        ).fetchone()
        audit = conn.execute(
            "SELECT operation, outcome FROM audit_events WHERE operation='withdraw_rules' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        activate = conn.execute(
            "SELECT outcome FROM audit_events WHERE operation='activate_rules' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "withdrawn")
        self.assertEqual(audit, ("withdraw_rules", activate[0]))

    def test_wr02_create_run_rejects_withdrawn_rules(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("wr-rules", "WR."))
        service.command("regelautor", self._withdraw("wr-rules", key="wr2a"))
        with self.assertRaises(CommandError) as ctx:
            service.command(
                "op-a",
                {
                    "operation": "create_run",
                    "idempotency_key": "wr2b",
                    "expected_state_version": 0,
                    "payload": {"rules_version": "wr-rules"},
                },
            )
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_wr03_existing_run_keeps_snapshot(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("wr-rules", "WR."))
        created = service.command(
            "op-a",
            {
                "operation": "create_run",
                "idempotency_key": "wr3a",
                "expected_state_version": 0,
                "payload": {"rules_version": "wr-rules"},
            },
        )
        before = service.command("op-a", {"operation": "get_run", "run_id": created["run_id"]})
        service.command("regelautor", self._withdraw("wr-rules", key="wr3b"))
        after = service.command("op-a", {"operation": "get_run", "run_id": created["run_id"]})
        self.assertEqual(after["rules_version"], "wr-rules")
        self.assertEqual(after["rules_hash"], before["rules_hash"])
        service.command(
            "op-a",
            {
                "operation": "register_source",
                "run_id": created["run_id"],
                "idempotency_key": "wr3c",
                "expected_state_version": 1,
                "expected_payload_revision": 0,
                "payload": fixture("d1.json"),
            },
        )

    def test_wr04_foreign_author_cannot_withdraw(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("wr-rules", "WR."))
        with self.assertRaises(CommandError) as ctx:
            service.command("fremdautor", self._withdraw("wr-rules", key="wr4"))
        self.assertEqual(ctx.exception.code, "FORBIDDEN")
        conn = sqlite3.connect(self.path)
        state = conn.execute(
            "SELECT state FROM rule_sets WHERE rules_version=?",
            ("wr-rules",),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(state, "active")

    def test_wr05_default_rules_cannot_withdraw(self):
        service = self._service(provider_tenant="demo-a")
        with self.assertRaises(CommandError) as ctx:
            service.command("regelautor", self._withdraw(service.rules["rules_version"], key="wr5"))
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")

    def test_wr06_second_withdraw_invalid_transition(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("wr-rules", "WR."))
        service.command("regelautor", self._withdraw("wr-rules", key="wr6a"))
        with self.assertRaises(CommandError) as ctx:
            service.command("regelautor", self._withdraw("wr-rules", key="wr6b"))
        self.assertEqual(ctx.exception.code, "INVALID_TRANSITION")

    def test_wr07_agent_author_forbidden(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("wr-rules", "WR."))
        with self.assertRaises(CommandError) as ctx:
            service.command("rulesagent", self._withdraw("wr-rules", key="wr7"))
        self.assertEqual(ctx.exception.code, "FORBIDDEN")

    def test_dv01_duplicate_version_different_body_rejected(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("dv-rules", "A.", key="dv1a"))
        body = self._rules_body("dv-rules", "B.")
        with self.assertRaises(CommandError) as ctx:
            service.command(
                "regelautor",
                {"operation": "activate_rules", "idempotency_key": "dv1b", "payload": {"activate": True, "body": body}},
            )
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        conn = sqlite3.connect(self.path)
        stored = json.loads(
            conn.execute("SELECT body_json FROM rule_sets WHERE rules_version=?", ("dv-rules",)).fetchone()[0]
        )
        audit = conn.execute(
            "SELECT outcome, error_code FROM audit_events WHERE actor_id='regelautor' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(stored["draft_prefix"], "A.")
        self.assertEqual(audit, ("rejected", "INVALID_INPUT"))

    def test_dv02_duplicate_version_same_body_rejected(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("dv2-rules", "DV2.", key="dv2a"))
        before = self._rule_count()
        body = self._rules_body("dv2-rules", "DV2.")
        with self.assertRaises(CommandError) as ctx:
            service.command(
                "regelautor",
                {"operation": "activate_rules", "idempotency_key": "dv2b", "payload": {"activate": True, "body": body}},
            )
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        self.assertEqual(self._rule_count(), before)

    def test_dv03_withdrawn_version_cannot_reactivate(self):
        service = self._service(provider_tenant="demo-a")
        service.command("regelautor", self._command("dv3-rules", "DV3.", key="dv3a"))
        service.command("regelautor", self._withdraw("dv3-rules", key="dv3b"))
        with self.assertRaises(CommandError) as ctx:
            service.command("regelautor", self._command("dv3-rules", "DV3.", key="dv3c"))
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")
        conn = sqlite3.connect(self.path)
        state = conn.execute(
            "SELECT state FROM rule_sets WHERE rules_version=?",
            ("dv3-rules",),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(state, "withdrawn")

    def test_dv04_default_rules_version_immutable(self):
        service = self._service(provider_tenant="demo-a")
        body = dict(self._rules_body(service.rules["rules_version"], "NEU."))
        with self.assertRaises(CommandError) as ctx:
            service.command(
                "regelautor",
                {"operation": "activate_rules", "idempotency_key": "dv4", "payload": {"activate": True, "body": body}},
            )
        self.assertEqual(ctx.exception.code, "INVALID_INPUT")


if __name__ == "__main__":
    unittest.main()
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.clock import FixedClock
from framework.configplane import ConfigError, ConfigPlane
from framework.errors import CommandError
from framework.rules import load_rules
from framework.service import ROOT, Service
from framework.store import Store

from seed_config import seed_config

ROOT_REPO = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


def template_body():
    return json.loads((ROOT_REPO / "templates" / "auto-service-appointment-intake" / "template.json").read_text(encoding="utf-8"))


def initial_values():
    return {
        "callback_hint": "Wir rufen zurück, sobald ein Termin frei ist.",
        "followup_questions": "Welche Arbeit? Welches Kennzeichen? Wann passt es?",
        "opening_hours": "Mo-Fr 8-17",
        "shop_name": "Synth-Werkstatt",
    }


class OrderAndRulesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def _kfz_plane(self):
        store = Store(str(Path(self.folder) / "cfg.sqlite"))
        seed_config(
            store,
            (
                ("konrad", "synth-tenant", "human", ("author",)),
                ("cust", "synth-tenant", "human", ("customer",)),
            ),
        )
        plane = ConfigPlane(store, FixedClock("2026-11-14T08:50:00Z"))
        plane.register_template("konrad", template_body())
        plane.release("konrad", "cfg-1", "auto-service-appointment-intake", "1.1.0", initial_values(), "rel-1")
        return plane, store

    def test_or01_unknown_field_order_independent_invalid_config(self):
        plane, _store = self._kfz_plane()
        first = {"followup_questions": "x", "zzz": 1}
        second = {}
        second["zzz"] = 1
        second["followup_questions"] = "x"
        for changes in (first, second):
            with self.assertRaises(ConfigError) as caught:
                plane.apply_changes("cust", "cfg-1", changes, "or1")
            self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_or02_fixed_field_order_independent_invalid_config(self):
        plane, _store = self._kfz_plane()
        first = {"allow_unattended_send": False, "followup_questions": "x"}
        second = {}
        second["followup_questions"] = "x"
        second["allow_unattended_send"] = False
        for changes in (first, second):
            with self.assertRaises(ConfigError) as caught:
                plane.apply_changes("cust", "cfg-1", changes, "or2")
            self.assertEqual(caught.exception.code, "INVALID_CONFIG")

    def test_or03_customer_locked_field_audited(self):
        plane, store = self._kfz_plane()
        with self.assertRaises(ConfigError) as caught:
            plane.apply_changes("cust", "cfg-1", {"followup_questions": "x"}, "or3")
        self.assertEqual(caught.exception.code, "LOCKED_FIELD")
        conn = sqlite3.connect(str(Path(self.folder) / "cfg.sqlite"))
        row = conn.execute(
            "SELECT outcome, error_code FROM audit_events WHERE operation='config.apply' AND outcome='rejected'"
        ).fetchone()
        conn.close()
        self.assertEqual(row, ("rejected", "LOCKED_FIELD"))

    def test_or04_active_release_unchanged_after_failures(self):
        plane, _store = self._kfz_plane()
        before = plane.active("cust", "cfg-1")["release_no"]
        for changes in (
            {"followup_questions": "x", "zzz": 1},
            {"allow_unattended_send": False, "followup_questions": "x"},
            {"followup_questions": "x"},
        ):
            with self.assertRaises(ConfigError):
                plane.apply_changes("cust", "cfg-1", changes, "or4-" + sorted(changes)[0])
        after = plane.active("cust", "cfg-1")["release_no"]
        self.assertEqual(before, after)

    def _rules_service(self):
        path = str(Path(self.folder) / "rules.sqlite")
        service = Service.open(path, provider_tenant="demo-a")
        for principal_id, tenant_id, kind, roles in (
            ("bea", "demo-b", "human", ("operator", "reviewer")),
            ("regelautor", "demo-a", "human", ("author",)),
            ("regelagent", "demo-a", "agent", ("author",)),
        ):
            service.store.add_principal(principal_id, tenant_id, kind)
            for role in roles:
                service.store.bind_role(principal_id, role)
        return path, service

    def test_rr01_reviewer_cannot_activate_rules(self):
        path, service = self._rules_service()
        rules = load_rules(FIXTURES / "material-prep-1.json")
        body = dict(rules)
        body["rules_version"] = "demo-b-only"
        body["draft_prefix"] = "B."
        conn = sqlite3.connect(path)
        before = conn.execute("SELECT COUNT(*) FROM rule_sets").fetchone()[0]
        conn.close()
        with self.assertRaises(CommandError) as caught:
            service.command(
                "bea",
                {"operation": "activate_rules", "idempotency_key": "rr1", "payload": {"activate": True, "body": body}},
            )
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        conn = sqlite3.connect(path)
        after = conn.execute("SELECT COUNT(*) FROM rule_sets").fetchone()[0]
        audit = conn.execute(
            "SELECT outcome, error_code FROM audit_events WHERE actor_id='bea' AND outcome='rejected' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(before, after)
        self.assertEqual(audit, ("rejected", "FORBIDDEN"))

    def test_rr02_author_activates_rules(self):
        path, service = self._rules_service()
        rules = load_rules(FIXTURES / "material-prep-1.json")
        body = dict(rules)
        body["rules_version"] = "author-rules"
        body["draft_prefix"] = "Auth."
        service.command(
            "regelautor",
            {"operation": "activate_rules", "idempotency_key": "rr2", "payload": {"activate": True, "body": body}},
        )
        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT rules_version FROM rule_sets WHERE rules_version=?", ("author-rules",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)

    def test_rr03_agent_author_forbidden_activate_rules(self):
        _path, service = self._rules_service()
        rules = load_rules(FIXTURES / "material-prep-1.json")
        body = dict(rules)
        body["rules_version"] = "agent-rules"
        with self.assertRaises(CommandError) as caught:
            service.command(
                "regelagent",
                {"operation": "activate_rules", "idempotency_key": "rr3", "payload": {"activate": True, "body": body}},
            )
        self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_rr04_unbind_author_blocks_replay(self):
        path, service = self._rules_service()
        rules = load_rules(FIXTURES / "material-prep-1.json")
        body = dict(rules)
        body["rules_version"] = "rr4-rules"
        body["draft_prefix"] = "R4."
        command = {
            "operation": "activate_rules",
            "idempotency_key": "rr4",
            "payload": {"activate": True, "body": body},
        }
        service.command("regelautor", command)
        service.store.unbind_role("regelautor", "author")
        with self.assertRaises(CommandError) as caught:
            service.command("regelautor", command)
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        conn = sqlite3.connect(path)
        replayed = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE operation='activate_rules' AND outcome='replayed'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(replayed, 0)

    def test_rr05_unbind_invalid_role_raises(self):
        path, service = self._rules_service()
        with self.assertRaises(ValueError) as caught:
            service.store.unbind_role("regelautor", "guest")
        self.assertEqual(str(caught.exception), "invalid_role")


if __name__ == "__main__":
    unittest.main()
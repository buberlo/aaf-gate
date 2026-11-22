import sqlite3
import unittest
from pathlib import Path

import framework.policy
import framework.rules
import framework.service
from framework.errors import CommandError
from framework.policy import evaluate, policy_hash, resolve
from framework.rules import load_rules, validate_rules

from test_i1 import FIXTURES, CANARY, Harness, fixture

RULES_C = FIXTURES / "material-prep-1c.json"


class PolicyTests(unittest.TestCase):
    def test_pm01_single_floor_source_and_invoke_tool(self):
        self.assertIs(framework.rules.FLOOR_FORBIDDEN, framework.policy.FLOOR_FORBIDDEN)
        self.assertIs(framework.service.FLOOR_FORBIDDEN, framework.policy.FLOOR_FORBIDDEN)
        self.assertIn("invoke_tool", framework.policy.FLOOR_FORBIDDEN)
        lab = Harness()
        with self.assertRaises(CommandError) as caught:
            lab.call("invoke_tool", payload={"x": 1})
        self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_pm02_ruleset_forbids_approve(self):
        lab = Harness()
        rules = load_rules(RULES_C)
        lab.call("activate_rules", payload={"activate": True, "body": rules}, actor="regelautor")
        run = lab.create(rules_version="material-prep-1c")
        for name in ("d1.json", "d2.json"):
            lab.register(run, fixture(name))
        validated = lab.call("validate", run, expected_state_version=1, expected_payload_revision=0)
        drafted = lab.call(
            "build_draft",
            validated,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
        )
        reviewed = lab.call(
            "request_review",
            drafted,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
        )
        exported = lab.call("export_run", reviewed["run_id"])
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        with self.assertRaises(CommandError) as caught:
            lab.call(
                "approve",
                reviewed,
                {
                    "conflict_disposition": disposition,
                    "input_manifest_hash": exported["run"]["input_manifest_hash"],
                    "payload_hash": exported["run"]["artifact_hash"],
                    "payload_revision": exported["run"]["payload_revision"],
                    "purpose": "commit_preparation",
                    "rules_hash": exported["run"]["rules_hash"],
                    "rules_version": exported["run"]["rules_version"],
                },
                expected_state_version=reviewed["state_version"],
                expected_payload_revision=exported["run"]["payload_revision"],
                payload_hash=exported["run"]["artifact_hash"],
                rules_hash=exported["run"]["rules_hash"],
            )
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        state = lab.call("export_run", reviewed["run_id"])["run"]["state"]
        self.assertEqual(state, "REVIEW")
        row = sqlite3.connect(lab.path).execute(
            "SELECT outcome, error_code FROM audit_events WHERE actor_id='ada' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row, ("rejected", "FORBIDDEN"))

    def test_pm03_validate_rules_floor_and_owner_role(self):
        body = dict(load_rules(FIXTURES / "material-prep-1.json"))
        body["forbidden_actions"] = [item for item in body["forbidden_actions"] if item != "invoke_tool"]
        with self.assertRaises(ValueError) as caught:
            validate_rules(body)
        self.assertEqual(str(caught.exception), "forbidden")
        bad = dict(body)
        bad["owner_role"] = "reviewer"
        bad["forbidden_actions"] = load_rules(FIXTURES / "material-prep-1.json")["forbidden_actions"]
        with self.assertRaises(ValueError) as caught:
            validate_rules(bad)
        self.assertEqual(str(caught.exception), "rules_keys")

    def test_pm04_resolve_rejects_loosen_and_unknown_keys(self):
        with self.assertRaises(ValueError) as caught:
            resolve([{"allow_operations": ["send"]}])
        self.assertEqual(str(caught.exception), "policy_loosen")
        with self.assertRaises(ValueError) as caught:
            resolve([{"forbidden_operations": ["approve"]}, {"allow_operations": ["approve"]}])
        self.assertEqual(str(caught.exception), "policy_loosen")
        with self.assertRaises(ValueError) as caught:
            resolve([{"x": []}])
        self.assertEqual(str(caught.exception), "policy_keys")

    def test_pm05_resolve_order_independent(self):
        a = [{"forbidden_operations": ["approve"]}]
        b = [{"forbidden_operations": ["commit"]}]
        left = resolve([a[0], b[0]])
        right = resolve([b[0], a[0]])
        self.assertEqual(left, right)
        self.assertEqual(policy_hash(left), policy_hash(right))
        floor_only = resolve([])
        self.assertEqual(
            set(floor_only["forbidden_operations"]),
            framework.policy.FLOOR_FORBIDDEN,
        )

    def test_pm06_standard_rules_pipeline_regression(self):
        lab = Harness()
        approved, exported = lab.prepare_commit()
        lab.call(
            "commit",
            approved,
            approval_id=approved["approval_id"],
            expected_state_version=approved["state_version"],
            expected_payload_revision=exported["run"]["payload_revision"],
            payload_hash=exported["run"]["artifact_hash"],
            rules_hash=exported["run"]["rules_hash"],
        )
        self.assertEqual(lab.call("export_run", approved["run_id"])["run"]["state"], "GESPEICHERT")

    def test_pm12_policy_reject_audit_has_no_payload_plaintext(self):
        lab = Harness()
        rules = load_rules(RULES_C)
        lab.call("activate_rules", payload={"activate": True, "body": rules}, actor="regelautor")
        run = lab.create(rules_version="material-prep-1c")
        for name in ("d1.json", "d2.json"):
            lab.register(run, fixture(name))
        validated = lab.call("validate", run, expected_state_version=1, expected_payload_revision=0)
        drafted = lab.call(
            "build_draft",
            validated,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
        )
        reviewed = lab.call(
            "request_review",
            drafted,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
        )
        exported = lab.call("export_run", reviewed["run_id"])
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        with self.assertRaises(CommandError):
            lab.call(
                "approve",
                reviewed,
                {
                    "conflict_disposition": disposition,
                    "input_manifest_hash": exported["run"]["input_manifest_hash"],
                    "payload_hash": exported["run"]["artifact_hash"],
                    "payload_revision": exported["run"]["payload_revision"],
                    "purpose": CANARY,
                    "rules_hash": exported["run"]["rules_hash"],
                    "rules_version": exported["run"]["rules_version"],
                },
                expected_state_version=reviewed["state_version"],
                expected_payload_revision=exported["run"]["payload_revision"],
                payload_hash=exported["run"]["artifact_hash"],
                rules_hash=exported["run"]["rules_hash"],
            )
        audits = sqlite3.connect(lab.path).execute("SELECT * FROM audit_events").fetchall()
        joined = " ".join(str(part) for row in audits for part in row)
        self.assertNotIn(CANARY, joined)


if __name__ == "__main__":
    unittest.main()
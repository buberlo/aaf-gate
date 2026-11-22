import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from framework.errors import CommandError
from framework.service import ROOT, Service

FIXTURES = ROOT / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def open_lab(folder):
    path = str(Path(folder) / "principals.sqlite")
    service = Service.open(path)
    store = service.store
    for principal_id, tenant_id, kind, roles in (
        ("ada", "demo-a", "human", ("operator", "reviewer")),
        ("bea", "demo-b", "human", ("operator", "reviewer")),
        ("hermes-agent", "demo-a", "agent", ("operator", "reviewer")),
        ("operator-only", "demo-a", "human", ("operator",)),
        ("unknown-style", "demo-a", "human", ("operator", "reviewer")),
    ):
        store.add_principal(principal_id, tenant_id, kind)
        for role in roles:
            store.bind_role(principal_id, role)
    store.disable_principal("unknown-style")
    return path, service


class PrincipalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path, self.service = open_lab(self.folder)
        self.key = 0

    def call(self, principal_id, operation, run_id=None, payload=None, **extra):
        self.key += 1
        command = {"operation": operation, "idempotency_key": extra.pop("idempotency_key", f"k{self.key}")}
        if run_id is not None:
            command["run_id"] = run_id
        if payload is not None:
            command["payload"] = payload
        command.update(extra)
        return self.service.command(principal_id, command)

    def test_pr01_agent_cannot_approve(self):
        created = self.call("hermes-agent", "create_run", payload={"rules_version": "material-prep-1"}, expected_state_version=0)
        run_id = created["run_id"]
        for name in ("d1.json", "d2.json"):
            drafted = self.call(
                "hermes-agent",
                "register_source",
                run_id,
                fixture(name),
                expected_state_version=1,
                expected_payload_revision=0,
            )
        validated = self.call("hermes-agent", "validate", run_id, expected_state_version=1, expected_payload_revision=0)
        drafted = self.call(
            "hermes-agent",
            "build_draft",
            run_id,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
        )
        reviewed = self.call(
            "hermes-agent",
            "request_review",
            run_id,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
        )
        exported = self.call("hermes-agent", "export_run", run_id=run_id)
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        before = sqlite3.connect(self.path).execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        with self.assertRaises(CommandError) as caught:
            self.call(
                "hermes-agent",
                "approve",
                run_id,
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
        after = sqlite3.connect(self.path).execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        self.assertEqual(before, after)

    def test_pr02_unknown_principal_is_audited(self):
        with self.assertRaises(CommandError) as caught:
            self.call("nobody-here", "get_run", run_id="missing")
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        row = sqlite3.connect(self.path).execute(
            "SELECT tenant_id, outcome FROM audit_events WHERE operation='unknown' AND outcome='rejected' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row[0], "unresolved")
        self.assertEqual(row[1], "rejected")

    def test_pr03_unbound_principal_is_forbidden(self):
        store = Service.open(str(Path(self.folder) / "extra.sqlite")).store
        store.add_principal("bare", "demo-a", "human")
        with self.assertRaises(CommandError) as caught:
            Service.open(str(Path(self.folder) / "extra.sqlite")).command(
                "bare",
                {"operation": "create_run", "idempotency_key": "x", "expected_state_version": 0},
            )
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        with self.assertRaises(CommandError) as caught:
            Service.open(str(Path(self.folder) / "extra.sqlite")).command(
                "bare",
                {"operation": "get_run", "run_id": "missing", "idempotency_key": "y"},
            )
        self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_pr04_disabled_principal_is_forbidden(self):
        with self.assertRaises(CommandError) as caught:
            self.call("unknown-style", "get_run", run_id="x")
        self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_pr05_audit_carries_principal_kind_and_role(self):
        created = self.call("ada", "create_run", payload={"rules_version": "material-prep-1"}, expected_state_version=0)
        row = sqlite3.connect(self.path).execute(
            "SELECT principal_kind, acting_role, outcome FROM audit_events WHERE operation='create_run' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(row[0], "human")
        self.assertEqual(row[1], "operator")
        self.assertEqual(row[2], "committed")
        run_id = created["run_id"]
        for name in ("d1.json", "d2.json"):
            self.call("hermes-agent", "register_source", run_id, fixture(name), expected_state_version=1, expected_payload_revision=0)
        validated = self.call("hermes-agent", "validate", run_id, expected_state_version=1, expected_payload_revision=0)
        drafted = self.call(
            "hermes-agent",
            "build_draft",
            run_id,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
        )
        reviewed = self.call(
            "hermes-agent",
            "request_review",
            run_id,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
        )
        exported = self.call("hermes-agent", "export_run", run_id=run_id)
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        with self.assertRaises(CommandError):
            self.call(
                "hermes-agent",
                "approve",
                run_id,
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
        rejected = sqlite3.connect(self.path).execute(
            "SELECT principal_kind, acting_role, outcome FROM audit_events WHERE operation='approve' AND outcome='rejected' ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(rejected[0], "agent")
        self.assertIsNone(rejected[1])
        self.assertEqual(rejected[2], "rejected")

    def test_pr06_foreign_tenant_read_stays_not_found(self):
        created = self.call("ada", "create_run", payload={"rules_version": "material-prep-1"}, expected_state_version=0)
        with self.assertRaises(CommandError) as caught:
            self.call("bea", "get_run", run_id=created["run_id"])
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_pr07_non_string_principal_is_forbidden(self):
        with self.assertRaises(CommandError) as caught:
            self.service.command(object(), {"operation": "get_run", "run_id": "x", "idempotency_key": "z"})
        self.assertEqual(caught.exception.code, "FORBIDDEN")

    def test_pr08_operator_only_cannot_approve(self):
        created = self.call("ada", "create_run", payload={"rules_version": "material-prep-1"}, expected_state_version=0)
        run_id = created["run_id"]
        for name in ("d1.json", "d2.json"):
            self.call("operator-only", "register_source", run_id, fixture(name), expected_state_version=1, expected_payload_revision=0)
        validated = self.call("operator-only", "validate", run_id, expected_state_version=1, expected_payload_revision=0)
        drafted = self.call(
            "operator-only",
            "build_draft",
            run_id,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
        )
        reviewed = self.call(
            "operator-only",
            "request_review",
            run_id,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
        )
        exported = self.call("ada", "export_run", run_id=run_id)
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        with self.assertRaises(CommandError) as caught:
            self.call(
                "operator-only",
                "approve",
                run_id,
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


if __name__ == "__main__":
    unittest.main()

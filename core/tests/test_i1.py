import json
import logging
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from framework.canon import format_berlin, hash_value
from framework.egress import reject_external
from framework.errors import CommandError, LostResponse
from framework.rules import decision_view, load_rules
from framework.principals import resolve
from framework.service import ROOT, Service

FIXTURES = ROOT / "fixtures"
CANARY = "KANARIEN-B-7f3a"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def seed_principals(store):
    existing = {
        row["principal_id"]
        for row in store.connect().execute("SELECT principal_id FROM principals").fetchall()
    }
    plans = (
        ("ada", "demo-a", "human", ("operator", "reviewer")),
        ("bea", "demo-b", "human", ("operator", "reviewer")),
        ("no-role", "demo-a", "human", ()),
        ("regelautor", "demo-a", "human", ("author",)),
    )
    for principal_id, tenant_id, kind, roles in plans:
        if principal_id in existing:
            continue
        store.add_principal(principal_id, tenant_id, kind)
        for role in roles:
            store.bind_role(principal_id, role)


def open_service(folder):
    path = str(Path(folder) / "i1.sqlite")
    service = Service.open(path, provider_tenant="demo-a")
    seed_principals(service.store)
    return path, service


def snap(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    data = {}
    for table in ("runs", "source_revisions", "claims", "approvals", "receipts", "audit_events", "feedback", "rule_sets"):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        data[table] = [dict(row) for row in rows]
    conn.close()
    return data


def domain(path):
    shot = snap(path)
    return {key: shot[key] for key in ("runs", "source_revisions", "claims", "approvals", "receipts", "feedback")}


class Harness:
    def __init__(self, folder=None, roles=("reviewer",)):
        self.folder = folder or tempfile.mkdtemp()
        self.path, self.service = open_service(self.folder)
        if roles == ("reviewer",):
            self.principal_id = "ada"
        else:
            self.principal_id = "no-role"
        self.keys = 0

    def call(self, operation, run=None, payload=None, actor=None, **extra):
        self.keys += 1
        command = {"operation": operation, "idempotency_key": extra.pop("idempotency_key", f"k{self.keys}")}
        if run is not None:
            command["run_id"] = run["run_id"] if isinstance(run, dict) else run
        if payload is not None:
            command["payload"] = payload
        command.update(extra)
        principal_id = actor if isinstance(actor, str) else self.principal_id
        return self.service.command(principal_id, command)

    def create(self, rules_version="material-prep-1", actor=None):
        return self.call("create_run", payload={"rules_version": rules_version}, expected_state_version=0, actor=actor)

    def register(self, run, body, actor=None):
        return self.call(
            "register_source",
            run,
            body,
            actor=actor,
            expected_state_version=run["state_version"],
            expected_payload_revision=run.get("payload_revision", 0),
        )

    def pipeline(self, names=("d1.json", "d2.json"), actor=None):
        run = self.create(actor=actor)
        for name in names:
            self.register(run, fixture(name), actor=actor)
        validated = self.call("validate", run, expected_state_version=1, expected_payload_revision=0, actor=actor)
        drafted = self.call(
            "build_draft",
            validated,
            expected_state_version=validated["state_version"],
            expected_payload_revision=0,
            actor=actor,
        )
        reviewed = self.call(
            "request_review",
            drafted,
            expected_state_version=drafted["state_version"],
            expected_payload_revision=drafted["payload_revision"],
            actor=actor,
        )
        return reviewed

    def approve(self, reviewed, actor=None):
        exported = self.call("export_run", reviewed["run_id"], actor=actor)
        artifact = exported["run"]["artifact"]
        disposition = "konflikt_sichtbar_keine_aufloesung" if artifact["conflicts"] else "keine"
        approved = self.call(
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
            actor=actor,
            expected_state_version=reviewed["state_version"],
            expected_payload_revision=exported["run"]["payload_revision"],
            payload_hash=exported["run"]["artifact_hash"],
            rules_hash=exported["run"]["rules_hash"],
        )
        return approved, exported

    def prepare_commit(self):
        reviewed = self.pipeline()
        approved, exported = self.approve(reviewed)
        return approved, exported


class I1Tests(unittest.TestCase):
    def test_t01_source_revisions_are_immutable(self):
        lab = Harness()
        run = lab.create()
        first = lab.register(run, fixture("d1.json"))
        second = lab.register(run, fixture("d2.json"))
        exported = lab.call("export_run", run["run_id"])
        by_id = {item["source_id"]: item for item in exported["sources"]}
        self.assertTrue(by_id["D1"]["synthetic"])
        self.assertTrue(by_id["D2"]["synthetic"])
        self.assertEqual([segment["locator"] for segment in by_id["D1"]["segments"]], ["p1", "p2"])
        self.assertEqual(by_id["D1"]["segments"][0]["text"], "Material inspection: 20 test specimens.")
        self.assertEqual(by_id["D1"]["content_hash"], first["content_hash"])
        self.assertEqual(by_id["D2"]["content_hash"], second["content_hash"])
        changed = fixture("d1.json")
        changed["segments"] = [{"locator": "p1", "text": "Material inspection: 21 test specimens."}]
        before = domain(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.register(run, changed)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(domain(lab.path)["source_revisions"], before["source_revisions"])
        revised = fixture("d1.json")
        revised["source_revision"] = 2
        revised["segments"] = [{"locator": "p1", "text": "Nachtrag ohne Menge."}]
        lab.register(run, revised)
        again = {item["source_id"] + ":" + str(item["source_revision"]): item for item in lab.call("export_run", run["run_id"])["sources"]}
        self.assertEqual(again["D1:1"]["segments"][0]["text"], "Material inspection: 20 test specimens.")
        self.assertEqual(again["D1:2"]["segments"][0]["text"], "Nachtrag ohne Menge.")
        self.assertEqual(format_berlin("2026-11-08T13:00:00Z"), "2026-11-08T14:00:00+0100")

    def test_t02_bad_facts_are_not_confirmed(self):
        lab = Harness()
        run = lab.create()
        lab.register(run, fixture("d1.json"))
        other = Harness()
        other_run = other.create(actor="bea")
        secret = fixture("d1.json")
        secret["segments"][0]["text"] = CANARY
        other.register(other_run, secret, actor="bea")
        foreign = other.call("export_run", other_run["run_id"], actor="bea")["sources"][0]
        before = domain(lab.path)
        candidates = [
            {"kind": "fact", "references": [{"content_hash": "sha256:aa", "locator": "", "source_id": "D1", "source_revision": 1}], "statement": "Test specimens: 20"},
            {"kind": "fact", "references": [{"content_hash": "sha256:aa", "locator": "p1", "source_id": "D1", "source_revision": 99}], "statement": "Test specimens: 20"},
            {"kind": "fact", "references": [{"content_hash": "sha256:dead", "locator": "p1", "source_id": "D1", "source_revision": 1}], "statement": "Test specimens: 20"},
            {"kind": "fact", "references": [{"content_hash": foreign["content_hash"], "locator": "p1", "source_id": foreign["source_id"], "source_revision": 1}], "statement": CANARY},
        ]
        for payload in candidates:
            with self.assertRaises(CommandError) as caught:
                lab.call("confirm_fact", run, payload, expected_state_version=1, expected_payload_revision=0)
            self.assertEqual(caught.exception.code, "INVALID_INPUT")
            self.assertNotIn(CANARY, str(caught.exception))
        self.assertEqual(domain(lab.path), before)
        validated = lab.call("validate", run, expected_state_version=1, expected_payload_revision=0)
        drafted = lab.call("build_draft", validated, expected_state_version=2, expected_payload_revision=0)
        artifact = lab.call("export_run", drafted["run_id"])["run"]["artifact"]
        self.assertEqual(artifact["open_questions"][0]["topic"], "lieferdatum")
        self.assertNotIn("lieferdatum", [fact["claim_type"] for fact in artifact["facts"]])

    def test_t03_conflict_and_open_date_stay_visible(self):
        lab = Harness()
        reviewed = lab.pipeline()
        artifact = lab.call("export_run", reviewed["run_id"])["run"]["artifact"]
        self.assertEqual(artifact["conflicts"][0]["values"], ["20", "24"])
        self.assertEqual(
            sorted(ref["source_id"] for ref in artifact["conflicts"][0]["references"]),
            ["D1", "D2"],
        )
        self.assertEqual(artifact["facts"], [])
        self.assertEqual(artifact["open_questions"][0]["topic"], "lieferdatum")
        self.assertNotIn("2026", artifact["work_draft"])
        self.assertIn("No quantity adopted", artifact["work_draft"])
        self.assertNotIn("24 test specimens documented", artifact["work_draft"])
        completed = subprocess.run(
            [sys.executable, "-m", "framework.cli"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        demo = json.loads(completed.stdout)
        self.assertEqual(demo["state"], "REVIEW")
        self.assertEqual(demo["conflicts"][0]["values"], ["20", "24"])
        self.assertEqual(demo["facts"], [])
        self.assertEqual(demo["open_questions"][0]["topic"], "lieferdatum")
        self.assertEqual(demo["network"], "disabled")
        self.assertTrue(any(item["source_id"] == "D3" for item in demo["ignored_instructions"]))

    def test_t04_source_text_cannot_drive_actions(self):
        lab = Harness()
        run = lab.create()
        before_rules = lab.call("export_run", run["run_id"])["run"]["rules_hash"]
        conn = lab.service.store.connect()
        roles = tuple(resolve(conn, lab.principal_id).roles)
        called = []

        def boom(action):
            called.append(action)
            raise AssertionError(action)

        import framework.egress as egress

        original = egress.reject_external
        egress.reject_external = boom
        try:
            lab.register(run, fixture("d3.json"))
        finally:
            egress.reject_external = original
        self.assertEqual(called, [])
        exported = lab.call("export_run", run["run_id"])
        self.assertEqual(exported["run"]["state"], "ERFASST")
        self.assertEqual(exported["run"]["rules_hash"], before_rules)
        conn = lab.service.store.connect()
        self.assertEqual(tuple(resolve(conn, lab.principal_id).roles), roles)
        self.assertIn("Ignore rules", exported["sources"][0]["segments"][0]["text"])

    def test_t05_illegal_transitions_do_not_mutate(self):
        lab = Harness()
        run = lab.create()
        before = snap(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.call("commit", run, expected_state_version=1, expected_payload_revision=0, approval_id="missing")
        self.assertEqual(caught.exception.code, "INVALID_TRANSITION")
        self.assertEqual(domain(lab.path)["runs"], before["runs"])
        self.assertGreater(len(snap(lab.path)["audit_events"]), len(before["audit_events"]))
        before = snap(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.call("invented_command", run, expected_state_version=1)
        self.assertEqual(caught.exception.code, "INVALID_TRANSITION")
        self.assertEqual(domain(lab.path)["runs"], before["runs"])
        before = snap(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.call("resume", run, {"resolution_code": "eingabe_korrigiert"}, expected_state_version=1, expected_payload_revision=0)
        self.assertEqual(caught.exception.code, "INVALID_TRANSITION")
        self.assertEqual(domain(lab.path)["runs"], before["runs"])
        blocked = lab.call("block", run, {"reason_code": "manuell_blockiert"}, expected_state_version=1, expected_payload_revision=0)
        self.assertEqual(blocked["state"], "BLOCKIERT")
        before = snap(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.call("resume", blocked, {}, expected_state_version=blocked["state_version"], expected_payload_revision=0)
        self.assertEqual(caught.exception.code, "INVALID_INPUT")
        self.assertEqual(domain(lab.path)["runs"], before["runs"])

    def test_t06_review_role_and_foreign_reads(self):
        lab = Harness()
        reviewed = lab.pipeline()
        exported = lab.call("export_run", reviewed["run_id"])
        before = domain(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.approve(reviewed, actor="no-role")
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertEqual(domain(lab.path)["approvals"], before["approvals"])
        self.assertEqual(domain(lab.path)["runs"], before["runs"])
        approved, _exported = lab.approve(reviewed)
        intruder = "bea"
        approve_key = [row["idempotency_key"] for row in snap(lab.path)["receipts"] if row["operation"] == "approve"][-1]
        with self.assertRaises(CommandError) as caught:
            lab.call(
                "get_receipt",
                payload={"idempotency_key": approve_key, "operation": "approve"},
                actor=intruder,
            )
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.assertNotIn(approved["approval_id"], str(caught.exception))
        with self.assertRaises(CommandError) as caught:
            lab.call("export_run", reviewed["run_id"], {"approval_id": approved["approval_id"]}, actor=intruder)
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.assertNotIn(exported["run"]["artifact_hash"], str(caught.exception))

    def test_t07_approval_is_bound_to_the_snapshot(self):
        lab = Harness()
        approved, exported = lab.prepare_commit()
        base = {
            "approval_id": approved["approval_id"],
            "expected_payload_revision": exported["run"]["payload_revision"],
            "expected_state_version": approved["state_version"],
            "payload_hash": exported["run"]["artifact_hash"],
            "rules_hash": exported["run"]["rules_hash"],
            "payload": {
                "input_manifest_hash": exported["run"]["input_manifest_hash"],
                "rules_version": exported["run"]["rules_version"],
            },
        }
        variants = [
            {**base, "payload_hash": "sha256:" + "ab" * 32},
            {**base, "expected_payload_revision": base["expected_payload_revision"] + 9},
            {**base, "rules_hash": "sha256:" + "cd" * 32},
            {**base, "payload": {"input_manifest_hash": "sha256:" + "ef" * 32, "rules_version": exported["run"]["rules_version"]}},
            {**base, "payload": {"input_manifest_hash": exported["run"]["input_manifest_hash"], "rules_version": "andere-version"}},
        ]
        for variant in variants:
            before = domain(lab.path)
            with self.assertRaises(CommandError) as caught:
                lab.call("commit", approved, **variant)
            self.assertEqual(caught.exception.code, "APPROVAL_INVALID")
            self.assertEqual(domain(lab.path)["runs"], before["runs"])
            self.assertEqual(domain(lab.path)["receipts"], before["receipts"])
        revised = lab.call(
            "revise",
            approved,
            expected_state_version=approved["state_version"],
            expected_payload_revision=exported["run"]["payload_revision"],
        )
        self.assertEqual(revised["state"], "ENTWURF")
        self.assertGreater(revised["state_version"], approved["state_version"])
        before = domain(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.call(
                "commit",
                revised,
                approval_id=approved["approval_id"],
                expected_state_version=revised["state_version"],
                expected_payload_revision=revised.get("payload_revision", exported["run"]["payload_revision"] + 1),
                payload_hash=exported["run"]["artifact_hash"],
                rules_hash=exported["run"]["rules_hash"],
            )
        self.assertEqual(caught.exception.code, "APPROVAL_INVALID")
        self.assertEqual(domain(lab.path)["runs"], before["runs"])
        rerun = lab.pipeline()
        self.assertNotEqual(rerun["run_id"], approved["run_id"])
        with self.assertRaises(CommandError):
            lab.call("commit", rerun, approval_id=approved["approval_id"], expected_state_version=rerun["state_version"], expected_payload_revision=1)

    def test_t08_same_key_commits_once(self):
        lab = Harness()
        approved, exported = lab.prepare_commit()
        command = {
            "approval_id": approved["approval_id"],
            "expected_payload_revision": exported["run"]["payload_revision"],
            "expected_state_version": approved["state_version"],
            "idempotency_key": "same-key",
            "payload_hash": exported["run"]["artifact_hash"],
            "rules_hash": exported["run"]["rules_hash"],
        }
        first = lab.call("commit", approved, **command)
        second = lab.call("commit", approved, **command)
        self.assertEqual(first["result_id"], second["result_id"])
        events = [row for row in snap(lab.path)["audit_events"] if row["operation"] == "commit" and row["outcome"] == "committed"]
        receipts = [row for row in snap(lab.path)["receipts"] if row["operation"] == "commit" and row["idempotency_key"] == "same-key"]
        self.assertEqual(len(events), 1)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(lab.call("export_run", approved["run_id"])["run"]["state"], "GESPEICHERT")
        other = Harness()
        approved_b, exported_b = other.prepare_commit()
        other.service.testing_fault = "after_commit"
        burst = {
            "approval_id": approved_b["approval_id"],
            "expected_payload_revision": exported_b["run"]["payload_revision"],
            "expected_state_version": approved_b["state_version"],
            "idempotency_key": "burst",
            "payload_hash": exported_b["run"]["artifact_hash"],
            "rules_hash": exported_b["run"]["rules_hash"],
        }
        with self.assertRaises(LostResponse):
            other.call("commit", approved_b, **burst)
        other.service.testing_fault = None
        replay = other.call("commit", approved_b, **burst)
        committed = [row for row in snap(other.path)["audit_events"] if row["operation"] == "commit" and row["outcome"] == "committed"]
        self.assertEqual(len(committed), 1)
        self.assertEqual(replay["result_id"], json.loads(snap(other.path)["receipts"][-1]["result_json"])["result_id"])
        barrier = threading.Barrier(2)
        results = []
        errors = []
        parallel = Harness()
        approved_c, exported_c = parallel.prepare_commit()
        body = {
            "approval_id": approved_c["approval_id"],
            "expected_payload_revision": exported_c["run"]["payload_revision"],
            "expected_state_version": approved_c["state_version"],
            "idempotency_key": "parallel",
            "payload_hash": exported_c["run"]["artifact_hash"],
            "rules_hash": exported_c["run"]["rules_hash"],
        }

        def worker():
            barrier.wait()
            try:
                results.append(parallel.call("commit", approved_c, **body))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["result_id"], results[1]["result_id"])
        self.assertEqual(len([row for row in snap(parallel.path)["audit_events"] if row["operation"] == "commit" and row["outcome"] == "committed"]), 1)

    def test_t09_same_key_different_payload_conflicts(self):
        lab = Harness()
        approved, exported = lab.prepare_commit()
        first = lab.call(
            "commit",
            approved,
            approval_id=approved["approval_id"],
            expected_payload_revision=exported["run"]["payload_revision"],
            expected_state_version=approved["state_version"],
            idempotency_key="once",
            payload_hash=exported["run"]["artifact_hash"],
            rules_hash=exported["run"]["rules_hash"],
        )
        before = domain(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.call(
                "commit",
                approved,
                approval_id=approved["approval_id"],
                expected_payload_revision=exported["run"]["payload_revision"],
                expected_state_version=approved["state_version"],
                idempotency_key="once",
                payload_hash="sha256:" + "11" * 32,
                rules_hash=exported["run"]["rules_hash"],
            )
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(domain(lab.path)["runs"], before["runs"])
        self.assertEqual(domain(lab.path)["receipts"], before["receipts"])
        self.assertEqual(lab.call("get_receipt", payload={"idempotency_key": "once", "operation": "commit"})["result_id"], first["result_id"])

    def test_t10_one_cas_winner(self):
        lab = Harness()
        approved, exported = lab.prepare_commit()
        barrier = threading.Barrier(2)
        outcomes = []

        def worker(key):
            barrier.wait()
            try:
                result = lab.call(
                    "commit",
                    approved,
                    approval_id=approved["approval_id"],
                    expected_payload_revision=exported["run"]["payload_revision"],
                    expected_state_version=approved["state_version"],
                    idempotency_key=key,
                    payload_hash=exported["run"]["artifact_hash"],
                    rules_hash=exported["run"]["rules_hash"],
                )
                outcomes.append(("ok", result["result_id"]))
            except CommandError as exc:
                outcomes.append((exc.code, None))

        threads = [threading.Thread(target=worker, args=("left",)), threading.Thread(target=worker, args=("right",))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        codes = sorted(item[0] for item in outcomes)
        self.assertEqual(codes, ["STALE_REVISION", "ok"])
        self.assertEqual(lab.call("export_run", approved["run_id"])["run"]["state"], "GESPEICHERT")
        commits = [row for row in snap(lab.path)["audit_events"] if row["operation"] == "commit" and row["outcome"] == "committed"]
        self.assertEqual(len(commits), 1)

    def test_t11_faults_roll_back(self):
        faults = ("after_state", "after_audit", "before_commit")
        for fault in faults:
            lab = Harness()
            approved, exported = lab.prepare_commit()
            before = snap(lab.path)
            lab.service.testing_fault = fault
            with self.assertRaises(CommandError) as caught:
                lab.call(
                    "commit",
                    approved,
                    approval_id=approved["approval_id"],
                    expected_payload_revision=exported["run"]["payload_revision"],
                    expected_state_version=approved["state_version"],
                    idempotency_key=fault,
                    payload_hash=exported["run"]["artifact_hash"],
                    rules_hash=exported["run"]["rules_hash"],
                )
            self.assertEqual(caught.exception.code, "STORAGE_UNAVAILABLE")
            self.assertEqual(snap(lab.path), before)
        lab = Harness()
        approved, exported = lab.prepare_commit()
        lab.service.testing_fault = "after_commit"
        payload = {
            "approval_id": approved["approval_id"],
            "expected_payload_revision": exported["run"]["payload_revision"],
            "expected_state_version": approved["state_version"],
            "idempotency_key": "lost",
            "payload_hash": exported["run"]["artifact_hash"],
            "rules_hash": exported["run"]["rules_hash"],
        }
        with self.assertRaises(LostResponse):
            lab.call("commit", approved, **payload)
        lab.service.testing_fault = None
        replay = lab.call("commit", approved, **payload)
        self.assertEqual(lab.call("export_run", approved["run_id"])["run"]["state"], "GESPEICHERT")
        self.assertTrue(replay["result_id"])
        self.assertEqual(len([row for row in snap(lab.path)["audit_events"] if row["operation"] == "commit" and row["outcome"] == "committed"]), 1)

    def test_t12_logical_tenant_checks_are_not_isolation(self):
        lab = Harness()
        run = lab.create()
        lab.register(run, fixture("d1.json"))
        secret_run = lab.create(actor="bea")
        secret_body = fixture("d2.json")
        secret_body["source_id"] = "B1"
        secret_body["segments"][0]["text"] = CANARY
        lab.register(secret_run, secret_body, actor="bea")
        approved_path = lab.pipeline()
        approved, _exported = lab.approve(approved_path)
        saved = lab.call(
            "commit",
            approved,
            approval_id=approved["approval_id"],
            expected_payload_revision=1,
            expected_state_version=approved["state_version"],
            payload_hash=lab.call("export_run", approved["run_id"])["run"]["artifact_hash"],
            rules_hash=lab.call("export_run", approved["run_id"])["run"]["rules_hash"],
        )
        raw = Path(lab.path).read_bytes()
        self.assertIn(CANARY.encode("utf-8"), raw)
        logs = []

        class Grab(logging.Handler):
            def emit(self, record):
                logs.append(record.getMessage())

        handler = Grab()
        lab.service.logger.addHandler(handler)
        lab.service.logger.setLevel(logging.INFO)
        attempts = []
        with self.assertRaises(CommandError) as caught:
            lab.call("export_run", secret_run["run_id"])
        attempts.append(caught.exception)
        with self.assertRaises(CommandError) as caught:
            lab.call("get_run", secret_run["run_id"])
        attempts.append(caught.exception)
        commit_key = [row["idempotency_key"] for row in snap(lab.path)["receipts"] if row["result_id"] == saved["result_id"]][-1]
        with self.assertRaises(CommandError) as caught:
            lab.call("get_receipt", payload={"idempotency_key": commit_key, "operation": "commit"}, actor="bea")
        attempts.append(caught.exception)
        with self.assertRaises(CommandError) as caught:
            lab.call("export_run", approved["run_id"], {"approval_id": approved["approval_id"]}, actor="bea")
        attempts.append(caught.exception)
        poisoned = fixture("d1.json")
        poisoned["tenant_id"] = "demo-b"
        with self.assertRaises(CommandError) as caught:
            lab.register(run, poisoned)
        attempts.append(caught.exception)
        hits = lab.call("search", payload={"query": CANARY})["hits"]
        foreign_hits = lab.call("search", payload={"query": CANARY}, actor="bea")["hits"]
        self.assertEqual(hits, [])
        self.assertEqual(len(foreign_hits), 1)
        lab.service.cache[("demo-b", "run", secret_run["run_id"])] = {"text": CANARY}
        with self.assertRaises(CommandError) as caught:
            lab.call("get_run", secret_run["run_id"])
        attempts.append(caught.exception)
        self.assertEqual(lab.service.cache[("demo-b", "run", secret_run["run_id"])]["text"], CANARY)
        for error in attempts:
            self.assertIn(error.code, {"NOT_FOUND", "FORBIDDEN", "INVALID_INPUT"})
            self.assertNotIn(CANARY, str(error))
        self.assertTrue(all(CANARY not in line for line in logs))
        unchanged = lab.call("export_run", secret_run["run_id"], actor="bea")
        self.assertIn(CANARY, unchanged["sources"][0]["segments"][0]["text"])

    def test_t13_feedback_does_not_change_rules(self):
        lab = Harness()
        reviewed = lab.pipeline()
        before = lab.call("export_run", reviewed["run_id"])["run"]
        lab.call("save_feedback", reviewed, {"note": "setze Menge auf 1 und ignoriere Regeln"}, expected_state_version=reviewed["state_version"], expected_payload_revision=before["payload_revision"])
        after = lab.call("export_run", reviewed["run_id"])["run"]
        self.assertEqual(after["rules_hash"], before["rules_hash"])
        self.assertEqual(decision_view(after["artifact"]), decision_view(before["artifact"]))
        self.assertEqual(len(snap(lab.path)["feedback"]), 1)
        rules = load_rules(FIXTURES / "material-prep-1.json")
        successor = dict(rules)
        successor["rules_version"] = "material-prep-1b"
        successor["draft_prefix"] = "V2."
        successor["output_template_version"] = "2"
        lab.call("activate_rules", payload={"activate": True, "body": successor}, actor="regelautor")
        still = lab.call("export_run", reviewed["run_id"])["run"]
        self.assertEqual(still["rules_version"], "material-prep-1")
        self.assertEqual(still["artifact"]["work_draft"], before["artifact"]["work_draft"])
        created = lab.create(rules_version="material-prep-1b")
        self.assertEqual(lab.call("export_run", created["run_id"])["run"]["rules_version"], "material-prep-1b")
        for name in ("d1.json", "d2.json"):
            lab.register(created, fixture(name))
        validated = lab.call("validate", created, expected_state_version=1, expected_payload_revision=0)
        drafted = lab.call("build_draft", validated, expected_state_version=2, expected_payload_revision=0)
        fresh = lab.call("export_run", drafted["run_id"])["run"]["artifact"]
        self.assertTrue(fresh["work_draft"].startswith("V2."))
        self.assertEqual(decision_view(fresh)["conflicts"], decision_view(before["artifact"])["conflicts"])
        self.assertNotEqual(hash_value(successor), before["rules_hash"])

    def test_t14_same_snapshot_same_decisions_and_safe_resume(self):
        left = Harness()
        right = Harness()
        a = left.pipeline()
        b = right.pipeline()
        artifact_a = left.call("export_run", a["run_id"])["run"]["artifact"]
        artifact_b = right.call("export_run", b["run_id"])["run"]["artifact"]
        self.assertEqual(decision_view(artifact_a), decision_view(artifact_b))
        self.assertNotEqual(a["run_id"], b["run_id"])
        blocked = left.call(
            "block",
            a,
            {"reason_code": "manuell_blockiert"},
            expected_state_version=a["state_version"],
            expected_payload_revision=left.call("export_run", a["run_id"])["run"]["payload_revision"],
        )
        resumed = left.call(
            "resume",
            blocked,
            {"resolution_code": "pruefung_wiederholt"},
            expected_state_version=blocked["state_version"],
            expected_payload_revision=left.call("export_run", blocked["run_id"])["run"]["payload_revision"],
        )
        self.assertEqual(resumed["state"], "REVIEW")
        self.assertNotEqual(resumed["state"], "FREIGEGEBEN")
        restored = left.call("export_run", resumed["run_id"])["run"]
        self.assertEqual(decision_view(restored["artifact"]), decision_view(artifact_a))
        with self.assertRaises(CommandError) as caught:
            left.call(
                "commit",
                resumed,
                approval_id="none",
                expected_state_version=resumed["state_version"],
                expected_payload_revision=restored["payload_revision"],
            )
        self.assertEqual(caught.exception.code, "INVALID_TRANSITION")

    def test_t15_fail_closed_without_network(self):
        rules = load_rules(FIXTURES / "material-prep-1.json")
        with self.assertRaises(CommandError) as caught:
            Service(None, rules).command("ada", {"operation": "create_run", "idempotency_key": "x"})
        self.assertEqual(caught.exception.code, "STORAGE_UNAVAILABLE")
        self.assertNotIn("sqlite", str(caught.exception).casefold())
        with self.assertRaises(CommandError) as caught:
            Service.open("/this/path/does/not/exist/i1.sqlite")
        self.assertEqual(caught.exception.code, "STORAGE_UNAVAILABLE")
        self.assertNotIn("does/not/exist", str(caught.exception))
        lab = Harness()
        muted = Service(lab.service.store, rules, audit_enabled=False)
        with self.assertRaises(CommandError) as caught:
            muted.command(lab.principal_id, {"operation": "create_run", "idempotency_key": "y", "expected_state_version": 0})
        self.assertEqual(caught.exception.code, "STORAGE_UNAVAILABLE")
        with self.assertRaises(CommandError) as caught:
            lab.service.command(None, {"operation": "create_run", "idempotency_key": "z"})
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        before = snap(lab.path)
        with self.assertRaises(CommandError) as caught:
            lab.call("invoke_tool", payload={"tool": "send"})
        self.assertEqual(caught.exception.code, "FORBIDDEN")
        self.assertEqual([row["state"] for row in snap(lab.path)["runs"]], [row["state"] for row in before["runs"]])
        sockets = []

        class Guard:
            def __init__(self, *args, **kwargs):
                pass

            def connect(self, address):
                sockets.append(address)
                raise AssertionError("network")

        import socket

        original = socket.socket
        socket.socket = Guard
        try:
            with self.assertRaises(RuntimeError):
                reject_external("send")
            with self.assertRaises(CommandError):
                lab.call("send")
        finally:
            socket.socket = original
        self.assertEqual(sockets, [])
        for source in (ROOT / "framework").glob("*.py"):
            text = source.read_text(encoding="utf-8")
            if source.name != "gatesocket.py":
                self.assertNotIn("import socket", text)
            self.assertNotIn("import urllib", text)
            self.assertNotIn("importlib", text)
            self.assertNotIn("__import__", text)
            self.assertNotIn("http.client", text)
            self.assertNotIn("requests", text)


if __name__ == "__main__":
    unittest.main()

import json
import logging
import sqlite3
import uuid
from pathlib import Path

from framework.audit import append as audit_append
from framework.canon import PRINCIPAL_ID, SCHEMA_VERSION, format_berlin, hash_text, hash_value, parse_utc
from framework.clock import Clock
from framework.errors import CommandError, LostResponse
from framework.idempotency import find, replay_result
from framework.policy import FLOOR_FORBIDDEN, evaluate, resolve as resolve_policy
from framework.principals import Principal, resolve
from framework.releases import active_release_hash, diff as release_diff, release_document
from framework.rules import decide, load_rules, manifest, validate_rules
from framework.store import Store

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "fixtures" / "material-prep-1.json"
COMMAND_FIELDS = {
    "operation",
    "run_id",
    "idempotency_key",
    "expected_state_version",
    "expected_payload_revision",
    "payload_hash",
    "rules_hash",
    "approval_id",
    "payload",
}
WRITE_OPERATIONS = {
    "create_run",
    "register_source",
    "validate",
    "build_draft",
    "request_review",
    "approve",
    "return_draft",
    "commit",
    "block",
    "resume",
    "revise",
    "supersede",
    "confirm_fact",
    "save_feedback",
    "activate_rules",
    "withdraw_rules",
}
READ_OPERATIONS = {"get_run", "get_receipt", "export_run", "release_diff", "search"}
OPERATION_ROLES = {
    "create_run": frozenset({"operator"}),
    "register_source": frozenset({"operator"}),
    "validate": frozenset({"operator"}),
    "build_draft": frozenset({"operator"}),
    "request_review": frozenset({"operator"}),
    "approve": frozenset({"reviewer"}),
    "return_draft": frozenset({"reviewer"}),
    "commit": frozenset({"reviewer"}),
    "revise": frozenset({"reviewer"}),
    "block": frozenset({"operator"}),
    "resume": frozenset({"operator"}),
    "supersede": frozenset({"operator"}),
    "confirm_fact": frozenset({"reviewer"}),
    "save_feedback": frozenset({"operator"}),
    "activate_rules": frozenset({"author"}),
    "withdraw_rules": frozenset({"author"}),
}
READ_ROLES = frozenset({"operator", "reviewer"})
AGENT_FORBIDDEN = frozenset(
    {"approve", "return_draft", "commit", "revise", "confirm_fact", "activate_rules", "withdraw_rules"}
)
TRANSITIONS = {
    ("CAPTURED", "validate"): "VALIDATED",
    ("VALIDATED", "build_draft"): "DRAFT",
    ("DRAFT", "request_review"): "REVIEW",
    ("REVIEW", "return_draft"): "DRAFT",
    ("REVIEW", "approve"): "APPROVED",
    ("APPROVED", "commit"): "STORED",
    ("APPROVED", "revise"): "DRAFT",
}
BLOCKABLE = {"CAPTURED", "VALIDATED", "DRAFT", "REVIEW", "APPROVED"}
BLOCK_REASONS = {"schema_incomplete", "source_unknown", "locator_missing", "manually_blocked"}
RESUME_REASONS = {"input_corrected", "check_repeated"}
MAX_ENVELOPE = 256000


class Injected(Exception):
    pass


class Replay(Exception):
    def __init__(self, result, principal, operation, request_hash, run_id=None):
        self.result = result
        self.principal = principal
        self.operation = operation
        self.request_hash = request_hash
        self.run_id = run_id


class Service:
    def __init__(self, store, rules: dict, clock=None, audit_enabled: bool = True, provider_tenant: str | None = None):
        self.store = store
        self.rules = validate_rules(rules)
        self.clock = clock or Clock()
        self.audit_enabled = audit_enabled
        self.provider_tenant = provider_tenant
        self.cache = {}
        self.testing_fault = None
        self.logger = logging.getLogger("framework.service")
        self._current_acting_role = None
        self._current_operation = "unknown"
        self._current_command_id = None
        if store is not None and audit_enabled:
            self._install_rules(self.rules, self.clock.now(), initial=True)

    @classmethod
    def open(cls, path: str, rules_path: Path | None = None, clock=None, audit_enabled: bool = True, provider_tenant: str | None = None):
        try:
            store = Store(path)
            rules = load_rules(rules_path or DEFAULT_RULES)
        except CommandError:
            raise
        except Exception:
            raise CommandError("STORAGE_UNAVAILABLE") from None
        return cls(store, rules, clock=clock, audit_enabled=audit_enabled, provider_tenant=provider_tenant)

    def command(self, principal_id, command: dict):
        self._current_operation = "unknown"
        self._current_command_id = None
        if self.store is None or not self.audit_enabled:
            raise CommandError("STORAGE_UNAVAILABLE")
        run_id = command.get("run_id") if isinstance(command, dict) else None
        if not isinstance(principal_id, str):
            self._deny_unresolved("invalid", run_id, "FORBIDDEN")
        audit_id = principal_id if PRINCIPAL_ID.fullmatch(principal_id) else "invalid"
        if audit_id == "invalid":
            self._deny_unresolved("invalid", run_id, "FORBIDDEN")
        conn = self.store.connect()
        try:
            principal = resolve(conn, principal_id)
        finally:
            conn.close()
        if principal is None:
            self._deny_unresolved(audit_id, run_id, "FORBIDDEN")
        self._current_acting_role = None
        if isinstance(command, dict):
            key = command.get("idempotency_key")
            if isinstance(key, str):
                self._current_command_id = key
            operation_name = command.get("operation")
            if (
                operation_name in WRITE_OPERATIONS
                or operation_name in READ_OPERATIONS
                or operation_name in FLOOR_FORBIDDEN
            ):
                self._current_operation = operation_name
        if not isinstance(command, dict) or set(command) - COMMAND_FIELDS:
            self._deny(principal, None, "INVALID_INPUT")
        if "tenant_id" in command or (isinstance(command.get("payload"), dict) and "tenant_id" in command["payload"]):
            self._deny(principal, command.get("run_id"), "FORBIDDEN")
        if "approver_id" in command or (isinstance(command.get("payload"), dict) and "approver_id" in command["payload"]):
            self._deny(principal, command.get("run_id"), "INVALID_INPUT")
        try:
            if len(hash_value(command)) and len(json.dumps(command, ensure_ascii=False).encode("utf-8")) > MAX_ENVELOPE:
                self._deny(principal, command.get("run_id"), "INVALID_INPUT")
        except (TypeError, ValueError):
            self._deny(principal, command.get("run_id"), "INVALID_INPUT")
        operation = command.get("operation")
        if operation in FLOOR_FORBIDDEN:
            self.logger.info("rejected %s", "FORBIDDEN")
            self._deny(principal, command.get("run_id"), "FORBIDDEN")
        self._current_acting_role = self._authorize(principal, operation, command.get("run_id"))
        if operation in READ_OPERATIONS:
            return self._read(principal, command)
        if operation not in WRITE_OPERATIONS:
            self._deny(principal, command.get("run_id"), "INVALID_TRANSITION")
        if not command.get("idempotency_key") or not isinstance(command.get("idempotency_key"), str):
            self._deny(principal, command.get("run_id"), "INVALID_INPUT")
        try:
            result = self._mutate(lambda conn: self._write(conn, principal, command))
        except CommandError as exc:
            if exc.code == "STORAGE_UNAVAILABLE":
                raise
            self._deny(principal, command.get("run_id"), exc.code)
        self.cache = {key: value for key, value in self.cache.items() if key[0] != principal.tenant_id}
        return result

    def _authorize(self, principal: Principal, operation, run_id) -> str:
        if operation in READ_OPERATIONS:
            if principal.roles & READ_ROLES:
                if "reviewer" in principal.roles:
                    return "reviewer"
                return "operator"
            self._deny(principal, run_id, "FORBIDDEN")
        if principal.kind == "agent" and operation in AGENT_FORBIDDEN:
            self._current_acting_role = None
            self._deny(principal, run_id, "FORBIDDEN")
        needed = OPERATION_ROLES.get(operation)
        if needed is None:
            self._deny(principal, run_id, "INVALID_TRANSITION")
        if not principal.roles & needed:
            self._deny(principal, run_id, "FORBIDDEN")
        if operation in {"activate_rules", "withdraw_rules"} and principal.tenant_id != self.provider_tenant:
            self._deny(principal, run_id, "FORBIDDEN")
        return sorted(principal.roles & needed)[0]

    def _deny_unresolved(self, actor_id: str, run_id, code: str) -> None:
        principal = Principal(actor_id, "unresolved", None, frozenset())
        self._current_acting_role = None
        self._deny(principal, run_id, code)

    def _read(self, principal, command):
        operation = command["operation"]
        try:
            conn = self.store.connect()
        except Exception as exc:
            raise CommandError("STORAGE_UNAVAILABLE") from exc
        try:
            if operation == "get_run":
                return self._get_run(conn, principal, command["run_id"])
            if operation == "export_run":
                payload = command.get("payload") or {}
                if set(payload) - {"approval_id"}:
                    raise CommandError("INVALID_INPUT")
                return self._export(conn, principal, command["run_id"], payload.get("approval_id"))
            if operation == "get_receipt":
                return self._get_receipt(conn, principal, command.get("payload") or {}, command.get("run_id"))
            if operation == "search":
                payload = command.get("payload") or {}
                if set(payload) - {"query"} or not isinstance(payload.get("query"), str):
                    raise CommandError("INVALID_INPUT")
                return self._search(conn, principal, payload["query"])
            if operation == "release_diff":
                payload = command.get("payload") or {}
                if payload:
                    raise CommandError("INVALID_INPUT")
                return self._release_diff(conn, principal, command["run_id"])
            raise CommandError("INVALID_TRANSITION")
        except CommandError:
            raise
        finally:
            conn.close()

    def _write(self, conn, principal, command):
        operation = command["operation"]
        self._enforce_policy(conn, principal, command)
        request_hash = hash_value(self._request_view(command))
        if operation != "confirm_fact":
            existing = find(
                conn,
                "receipts",
                principal.tenant_id,
                principal.principal_id,
                operation,
                command["idempotency_key"],
            )
            if existing is not None:
                result = replay_result(existing, request_hash, CommandError("IDEMPOTENCY_CONFLICT"))
                raise Replay(result, principal, operation, request_hash, command.get("run_id"))
        handler = getattr(self, f"_op_{operation}")
        return handler(conn, principal, command, request_hash)

    def _mutate(self, work):
        try:
            conn = self.store.connect()
        except Exception:
            raise CommandError("STORAGE_UNAVAILABLE") from None
        committed = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = work(conn)
            except Replay as replay:
                conn.execute("ROLLBACK")
                self._audit_replay(replay)
                return replay.result
            if self.testing_fault == "before_commit":
                raise Injected()
            conn.execute("COMMIT")
            committed = True
            if self.testing_fault == "after_commit":
                raise LostResponse()
            return result
        except LostResponse:
            raise
        except Injected:
            if not committed:
                self._safe_rollback(conn)
            raise CommandError("STORAGE_UNAVAILABLE") from None
        except CommandError:
            if not committed:
                self._safe_rollback(conn)
            raise
        except sqlite3.IntegrityError:
            if not committed:
                self._safe_rollback(conn)
            raise CommandError("CONFLICT") from None
        except Exception:
            if not committed:
                self._safe_rollback(conn)
            raise CommandError("STORAGE_UNAVAILABLE") from None
        finally:
            conn.close()

    def _safe_rollback(self, conn):
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass

    def _enforce_policy(self, conn, principal, command):
        operation = command["operation"]
        run_id = command.get("run_id")
        layers = []
        if run_id:
            row = conn.execute(
                "SELECT rules_body_json FROM runs WHERE tenant_id=? AND run_id=?",
                (principal.tenant_id, run_id),
            ).fetchone()
            if row is None:
                return
            rules = json.loads(row["rules_body_json"])
            layers = [{"forbidden_operations": rules["forbidden_actions"]}]
        elif operation == "create_run" and isinstance(command.get("payload"), dict):
            try:
                rules = self._rules_for(
                    conn,
                    command["payload"].get("rules_version") or self.rules["rules_version"],
                )
                layers = [{"forbidden_operations": rules["forbidden_actions"]}]
            except CommandError:
                return
        resolved = resolve_policy(layers)
        if evaluate(resolved, operation)["decision"] == "deny":
            raise CommandError("FORBIDDEN")

    def _deny(self, principal, run_id, code):
        self.logger.info("rejected %s", code)
        try:
            self._rejection(principal, run_id, code)
        except Exception:
            raise CommandError("STORAGE_UNAVAILABLE") from None
        raise CommandError(code)

    def _rejection(self, principal, run_id, code):
        conn = self.store.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._audit(
                conn,
                principal,
                run_id,
                self._current_operation,
                None,
                None,
                None,
                None,
                None,
                "rejected",
                code,
                command_id=self._current_command_id,
            )
            conn.execute("COMMIT")
        except Exception:
            self._safe_rollback(conn)
            raise
        finally:
            conn.close()

    def _op_create_run(self, conn, principal, command, request_hash):
        payload = self._payload(command, {"module_id", "module_version", "rules_version", "config_id"})
        rules = self._rules_for(conn, payload.get("rules_version") or self.rules["rules_version"])
        if payload.get("module_id", rules["module_id"]) != rules["module_id"]:
            raise CommandError("INVALID_INPUT")
        if payload.get("module_version", rules["module_version"]) != rules["module_version"]:
            raise CommandError("INVALID_INPUT")
        config_id = None
        release_hash = None
        if "config_id" in payload:
            config_id = payload["config_id"]
            if not isinstance(config_id, str) or not config_id:
                raise CommandError("INVALID_INPUT")
            release_hash = active_release_hash(conn, principal.tenant_id, config_id)
            if release_hash is None:
                raise CommandError("NOT_FOUND")
        run_id = str(uuid.uuid4())
        empty = hash_value([])
        now = self.clock.now()
        self._insert_run(
            conn,
            principal,
            run_id,
            rules,
            empty,
            "CAPTURED",
            1,
            0,
            None,
            None,
            None,
            now,
            config_id,
            release_hash,
        )
        result = {"result_revision": 1, "run_id": run_id, "state": "CAPTURED", "state_version": 1}
        return self._complete(conn, principal, command, request_hash, run_id, None, "CAPTURED", 1, None, None, result)

    def _op_register_source(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "CAPTURED")
        payload = self._payload(
            command,
            {"source_id", "source_revision", "origin_label", "captured_at", "synthetic", "segments"},
        )
        source = self._source_from_payload(payload)
        existing = conn.execute(
            "SELECT * FROM source_revisions WHERE tenant_id=? AND source_id=? AND source_revision=?",
            (principal.tenant_id, source["source_id"], source["source_revision"]),
        ).fetchone()
        if existing is not None:
            if existing["content_hash"] != source["content_hash"] or existing["segments_json"] != json.dumps(source["segments"], ensure_ascii=False, sort_keys=True):
                raise CommandError("INVALID_INPUT")
        else:
            self._limit_bytes(run, source)
            conn.execute(
                """INSERT INTO source_revisions (
                    tenant_id, source_id, source_revision, content_hash, origin_label, captured_at,
                    synthetic, segments_json, schema_version, object_id, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    principal.tenant_id,
                    source["source_id"],
                    source["source_revision"],
                    source["content_hash"],
                    source["origin_label"],
                    source["captured_at"],
                    1 if source["synthetic"] else 0,
                    json.dumps(source["segments"], ensure_ascii=False, sort_keys=True),
                    SCHEMA_VERSION,
                    str(uuid.uuid4()),
                    source["source_revision"],
                ),
            )
        self._attach_source(conn, run, source)
        result = {
            "content_hash": source["content_hash"],
            "locators": [segment["locator"] for segment in source["segments"]],
            "result_revision": run["state_version"],
            "source_id": source["source_id"],
            "source_revision": source["source_revision"],
            "synthetic": source["synthetic"],
        }
        return self._complete(conn, principal, command, request_hash, run["run_id"], run["state"], run["state"], run["state_version"], run["rules_hash"], None, result, bump=False)

    def _op_validate(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "CAPTURED")
        sources = self._run_sources(conn, principal, run)
        rules = json.loads(run["rules_body_json"])
        total = sum(len(json.dumps(source["segments"], ensure_ascii=False).encode("utf-8")) for source in sources)
        if not sources or len(sources) > rules["max_documents"] or total > rules["max_bytes"]:
            raise CommandError("INVALID_INPUT")
        digest = hash_value(manifest(sources))
        result = self._transition(conn, principal, command, request_hash, run, "validate", {"input_manifest_hash": digest})
        return result

    def _op_build_draft(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "VALIDATED")
        sources = self._run_sources(conn, principal, run)
        rules = json.loads(run["rules_body_json"])
        artifact = decide(sources, rules)
        if len(artifact["claims"]) > rules["max_claims"]:
            raise CommandError("INVALID_INPUT")
        self._replace_claims(conn, principal, run["run_id"], artifact["claims"])
        stored = {
            "claims": artifact["claims"],
            "conflicts": artifact["conflicts"],
            "facts": artifact["facts"],
            "ignored_instructions": artifact["ignored_instructions"],
            "open_questions": artifact["open_questions"],
            "rules_hash": run["rules_hash"],
            "rules_version": run["rules_version"],
            "work_draft": artifact["work_draft"],
        }
        payload_hash = hash_value(stored)
        return self._transition(
            conn,
            principal,
            command,
            request_hash,
            run,
            "build_draft",
            {
                "artifact_hash": payload_hash,
                "artifact_json": json.dumps(stored, ensure_ascii=False, sort_keys=True),
                "payload_revision": run["payload_revision"] + 1,
            },
        )

    def _op_request_review(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "DRAFT")
        if not run["artifact_hash"]:
            raise CommandError("INVALID_INPUT")
        return self._transition(conn, principal, command, request_hash, run, "request_review", {})

    def _op_approve(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "REVIEW")
        self._check_release(conn, run)
        payload = self._payload(
            command,
            {
                "purpose",
                "conflict_disposition",
                "payload_hash",
                "payload_revision",
                "rules_version",
                "rules_hash",
                "input_manifest_hash",
            },
        )
        artifact = json.loads(run["artifact_json"])
        self._assert_facts(conn, principal, artifact)
        if payload["purpose"] != "commit_preparation":
            raise CommandError("INVALID_INPUT")
        if artifact["conflicts"] and payload["conflict_disposition"] != "konflikt_sichtbar_keine_aufloesung":
            raise CommandError("APPROVAL_INVALID")
        if not artifact["conflicts"] and payload["conflict_disposition"] != "keine":
            raise CommandError("INVALID_INPUT")
        if payload["payload_hash"] != run["artifact_hash"] or payload["payload_revision"] != run["payload_revision"]:
            raise CommandError("APPROVAL_INVALID")
        if payload["rules_hash"] != run["rules_hash"] or payload["rules_version"] != run["rules_version"]:
            raise CommandError("APPROVAL_INVALID")
        if payload["input_manifest_hash"] != run["input_manifest_hash"]:
            raise CommandError("APPROVAL_INVALID")
        approval_id = str(uuid.uuid4())
        now = self.clock.now()
        conn.execute(
            """INSERT INTO approvals (
                tenant_id, approval_id, schema_version, object_id, revision, run_id, purpose,
                approver_id, approver_role, payload_hash, payload_revision, rules_version,
                rules_hash, input_manifest_hash, issued_at, revoked_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                principal.tenant_id,
                approval_id,
                SCHEMA_VERSION,
                str(uuid.uuid4()),
                run["run_id"],
                payload["purpose"],
                principal.actor_id,
                "reviewer",
                run["artifact_hash"],
                run["payload_revision"],
                run["rules_version"],
                run["rules_hash"],
                run["input_manifest_hash"],
                now,
            ),
        )
        return self._transition(conn, principal, command, request_hash, run, "approve", {}, approval_id=approval_id)

    def _op_commit(self, conn, principal, command, request_hash):
        run = self._load_run(conn, principal.tenant_id, command.get("run_id"))
        self._expect(run, command, check_payload=False)
        approval = None
        if command.get("approval_id"):
            approval = conn.execute(
                "SELECT * FROM approvals WHERE tenant_id=? AND approval_id=?",
                (principal.tenant_id, command["approval_id"]),
            ).fetchone()
            if approval is not None and (approval["revoked_at"] or not self._approval_matches(approval, command)):
                raise CommandError("APPROVAL_INVALID")
        if run["state"] != "APPROVED":
            raise CommandError("INVALID_TRANSITION")
        if approval is None:
            raise CommandError("NOT_FOUND")
        if run["run_id"] != approval["run_id"] or not self._approval_matches_run(approval, run):
            raise CommandError("APPROVAL_INVALID")
        self._check_release(conn, run)
        if self.testing_fault == "after_state":
            self._cas(conn, run, "STORED", {})
            raise Injected()
        self._cas(conn, run, "STORED", {})
        if self.testing_fault == "after_audit":
            self._audit(conn, principal, run["run_id"], "commit", run["state"], "STORED", run["state_version"] + 1, run["rules_hash"], run["artifact_hash"], "committed", None, approval["approval_id"])
            raise Injected()
        result = {
            "approval_id": approval["approval_id"],
            "result_revision": run["state_version"] + 1,
            "run_id": run["run_id"],
            "state": "STORED",
            "state_version": run["state_version"] + 1,
        }
        return self._complete(
            conn,
            principal,
            command,
            request_hash,
            run["run_id"],
            run["state"],
            "STORED",
            run["state_version"] + 1,
            run["rules_hash"],
            approval["approval_id"],
            result,
            bump=False,
        )

    def _op_return_draft(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "REVIEW")
        payload = self._payload(command, {"reason_code"})
        if payload.get("reason_code") not in BLOCK_REASONS:
            raise CommandError("INVALID_INPUT")
        return self._transition(conn, principal, command, request_hash, run, "return_draft", {})

    def _op_revise(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "APPROVED")
        now = self.clock.now()
        conn.execute(
            "UPDATE approvals SET revoked_at=? WHERE tenant_id=? AND run_id=? AND revoked_at IS NULL",
            (now, principal.tenant_id, run["run_id"]),
        )
        artifact = json.loads(run["artifact_json"])
        artifact["work_draft"] = artifact["work_draft"] + " Revision nach Widerruf."
        payload_hash = hash_value(artifact)
        return self._transition(
            conn,
            principal,
            command,
            request_hash,
            run,
            "revise",
            {
                "artifact_hash": payload_hash,
                "artifact_json": json.dumps(artifact, ensure_ascii=False, sort_keys=True),
                "payload_revision": run["payload_revision"] + 1,
            },
        )

    def _op_block(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, None)
        if run["state"] not in BLOCKABLE:
            raise CommandError("INVALID_TRANSITION")
        payload = self._payload(command, {"reason_code"})
        if payload.get("reason_code") not in BLOCK_REASONS:
            raise CommandError("INVALID_INPUT")
        self._expect(run, command)
        return self._transition(conn, principal, command, request_hash, run, "block", {"blocked_from": run["state"]}, target="BLOCKIERT")

    def _op_resume(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, None)
        if run["state"] != "BLOCKIERT":
            raise CommandError("INVALID_TRANSITION")
        payload = self._payload(command, {"resolution_code"})
        if payload.get("resolution_code") not in RESUME_REASONS:
            raise CommandError("INVALID_INPUT")
        target = run["blocked_from"]
        if target not in BLOCKABLE:
            raise CommandError("INVALID_TRANSITION")
        if run["input_manifest_hash"] != hash_value([]):
            current = hash_value(manifest(self._run_sources(conn, principal, run)))
            if current != run["input_manifest_hash"]:
                raise CommandError("INVALID_INPUT")
        self._expect(run, command)
        return self._transition(conn, principal, command, request_hash, run, "resume", {"blocked_from": None}, target=target)

    def _op_supersede(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, "STORED")
        rules = json.loads(run["rules_body_json"])
        new_id = str(uuid.uuid4())
        now = self.clock.now()
        self._insert_run(
            conn,
            principal,
            new_id,
            rules,
            hash_value([]),
            "CAPTURED",
            1,
            0,
            None,
            None,
            run["run_id"],
            now,
            run["config_id"],
            run["release_hash"],
        )
        result = {
            "origin_run_id": run["run_id"],
            "result_revision": 1,
            "run_id": new_id,
            "state": "CAPTURED",
            "state_version": 1,
        }
        return self._complete(conn, principal, command, request_hash, run["run_id"], run["state"], run["state"], run["state_version"], run["rules_hash"], None, result, bump=False)

    def _op_confirm_fact(self, conn, principal, command, request_hash):
        payload = self._payload(command, {"statement", "kind", "references"})
        self._candidate(conn, principal, payload)
        raise CommandError("INVALID_INPUT")

    def _op_save_feedback(self, conn, principal, command, request_hash):
        run = self._require_run(conn, principal, command, None)
        payload = self._payload(command, {"note"})
        note = payload.get("note")
        if not isinstance(note, str) or not note:
            raise CommandError("INVALID_INPUT")
        conn.execute(
            "INSERT INTO feedback (tenant_id, feedback_id, run_id, note, note_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (principal.tenant_id, str(uuid.uuid4()), run["run_id"], note, hash_text(note), self.clock.now()),
        )
        result = {"result_revision": run["state_version"], "rules_hash": run["rules_hash"], "rules_version": run["rules_version"], "run_id": run["run_id"]}
        return self._complete(conn, principal, command, request_hash, run["run_id"], run["state"], run["state"], run["state_version"], run["rules_hash"], None, result, bump=False, payload_hash=hash_text(note))

    def _op_activate_rules(self, conn, principal, command, request_hash):
        payload = command.get("payload")
        if not isinstance(payload, dict) or payload.get("activate") is not True or "body" not in payload:
            raise CommandError("INVALID_INPUT")
        if set(payload) - {"activate", "body"}:
            raise CommandError("INVALID_INPUT")
        try:
            body = validate_rules(payload["body"])
        except (ValueError, re_error()):
            raise CommandError("INVALID_INPUT")
        if conn.execute(
            "SELECT 1 FROM rule_sets WHERE rules_version=?",
            (body["rules_version"],),
        ).fetchone() is not None:
            raise CommandError("INVALID_INPUT")
        now = self.clock.now()
        conn.execute(
            "INSERT INTO rule_sets (rules_version, rules_hash, body_json, state, activated_at) VALUES (?, ?, ?, 'active', ?)",
            (body["rules_version"], hash_value(body), json.dumps(body, ensure_ascii=False, sort_keys=True), now),
        )
        result = {"result_revision": 1, "rules_hash": hash_value(body), "rules_version": body["rules_version"]}
        return self._complete(conn, principal, command, request_hash, None, None, None, 1, hash_value(body), None, result, bump=False)

    def _op_withdraw_rules(self, conn, principal, command, request_hash):
        payload = command.get("payload")
        if not isinstance(payload, dict) or payload.get("withdraw") is not True:
            raise CommandError("INVALID_INPUT")
        if set(payload) - {"withdraw", "rules_version"}:
            raise CommandError("INVALID_INPUT")
        version = payload.get("rules_version")
        if not isinstance(version, str) or not version:
            raise CommandError("INVALID_INPUT")
        if version == self.rules["rules_version"]:
            raise CommandError("INVALID_INPUT")
        row = conn.execute(
            "SELECT rules_hash, state FROM rule_sets WHERE rules_version=?",
            (version,),
        ).fetchone()
        if row is None:
            raise CommandError("INVALID_INPUT")
        if row["state"] == "withdrawn":
            raise CommandError("INVALID_TRANSITION")
        if row["state"] != "active":
            raise CommandError("INVALID_INPUT")
        updated = conn.execute(
            "UPDATE rule_sets SET state='withdrawn' WHERE rules_version=? AND state='active'",
            (version,),
        )
        if updated.rowcount != 1:
            raise CommandError("INVALID_TRANSITION")
        result = {
            "result_revision": 1,
            "rules_hash": row["rules_hash"],
            "rules_version": version,
            "state": "withdrawn",
        }
        return self._complete(
            conn,
            principal,
            command,
            request_hash,
            None,
            None,
            None,
            1,
            row["rules_hash"],
            None,
            result,
            bump=False,
        )

    def _candidate(self, conn, principal, payload):
        references = payload.get("references")
        if payload.get("kind") != "fact" or not isinstance(references, list) or len(references) != 1:
            raise CommandError("INVALID_INPUT")
        ref = references[0]
        if not isinstance(ref, dict):
            raise CommandError("INVALID_INPUT")
        locator = ref.get("locator")
        if not isinstance(locator, str) or not locator:
            raise CommandError("INVALID_INPUT")
        row = conn.execute(
            "SELECT * FROM source_revisions WHERE tenant_id=? AND source_id=? AND source_revision=?",
            (principal.tenant_id, ref.get("source_id"), ref.get("source_revision")),
        ).fetchone()
        if row is None:
            raise CommandError("INVALID_INPUT")
        if ref.get("content_hash") != row["content_hash"]:
            raise CommandError("INVALID_INPUT")
        segments = json.loads(row["segments_json"])
        if not any(segment["locator"] == locator for segment in segments):
            raise CommandError("INVALID_INPUT")

    def _assert_facts(self, conn, principal, artifact):
        for fact in artifact["facts"]:
            if fact["support_status"] != "supported" or fact["kind"] != "fact":
                raise CommandError("APPROVAL_INVALID")
            self._candidate(conn, principal, fact)

    def _transition(self, conn, principal, command, request_hash, run, operation, fields, approval_id=None, target=None):
        self._expect(run, command)
        destination = target or TRANSITIONS.get((run["state"], operation))
        if destination is None:
            raise CommandError("INVALID_TRANSITION")
        if self.testing_fault == "after_state":
            self._cas(conn, run, destination, fields)
            raise Injected()
        new_version = self._cas(conn, run, destination, fields)
        if self.testing_fault == "after_audit":
            self._audit(conn, principal, run["run_id"], operation, run["state"], destination, new_version, run["rules_hash"], fields.get("artifact_hash", run["artifact_hash"]), "committed", None, approval_id)
            raise Injected()
        result = {
            "result_revision": new_version,
            "run_id": run["run_id"],
            "state": destination,
            "state_version": new_version,
        }
        if approval_id:
            result["approval_id"] = approval_id
        if "artifact_hash" in fields:
            result["payload_hash"] = fields["artifact_hash"]
            result["payload_revision"] = fields["payload_revision"]
        return self._complete(
            conn,
            principal,
            command,
            request_hash,
            run["run_id"],
            run["state"],
            destination,
            new_version,
            run["rules_hash"],
            approval_id,
            result,
            bump=False,
            payload_hash=fields.get("artifact_hash", run["artifact_hash"]),
        )

    def _complete(self, conn, principal, command, request_hash, run_id, before, after, revision, rule_hash, approval_id, result, bump=False, payload_hash=None):
        result_id = str(uuid.uuid4())
        result = dict(result)
        result["result_id"] = result_id
        self._audit(conn, principal, run_id, command["operation"], before, after, revision, rule_hash, payload_hash, "committed", None, approval_id, command["idempotency_key"])
        if self.testing_fault == "after_audit" and command["operation"] not in {"commit"} and "after_audit_generic" == self.testing_fault:
            raise Injected()
        stored = dict(result)
        stored["occurred_at_berlin"] = format_berlin(self.clock.now())
        conn.execute(
            """INSERT INTO receipts (
                tenant_id, operation, idempotency_key, schema_version, object_id, revision, actor_id,
                request_hash, result_id, result_revision, outcome, result_json, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?)""",
            (
                principal.tenant_id,
                command["operation"],
                command["idempotency_key"],
                SCHEMA_VERSION,
                str(uuid.uuid4()),
                result["result_revision"],
                principal.actor_id,
                request_hash,
                result_id,
                result["result_revision"],
                json.dumps(stored, ensure_ascii=False, sort_keys=True),
                run_id,
            ),
        )
        return stored

    def _cas(self, conn, run, state, fields):
        assignments = ["state=?", "state_version=state_version+1", "revision=state_version+1"]
        values = [state]
        for key, value in fields.items():
            assignments.append(f"{key}=?")
            values.append(value)
        values.extend([run["tenant_id"], run["run_id"], run["state_version"]])
        cursor = conn.execute(
            f"UPDATE runs SET {', '.join(assignments)} WHERE tenant_id=? AND run_id=? AND state_version=?",
            values,
        )
        if cursor.rowcount != 1:
            raise CommandError("STALE_REVISION")
        return run["state_version"] + 1

    def _expect(self, run, command, check_payload: bool = True):
        if command.get("expected_state_version") != run["state_version"]:
            raise CommandError("STALE_REVISION")
        if check_payload and command.get("expected_payload_revision") not in (None, run["payload_revision"]):
            raise CommandError("STALE_REVISION")

    def _require_run(self, conn, principal, command, state):
        run = self._load_run(conn, principal.tenant_id, command.get("run_id"))
        if state is not None and run["state"] != state:
            raise CommandError("INVALID_TRANSITION")
        return run

    def _load_run(self, conn, tenant_id, run_id):
        if not isinstance(run_id, str) or not run_id:
            raise CommandError("NOT_FOUND")
        row = conn.execute("SELECT * FROM runs WHERE tenant_id=? AND run_id=?", (tenant_id, run_id)).fetchone()
        if row is None:
            raise CommandError("NOT_FOUND")
        return row

    def _approval(self, conn, principal, approval_id):
        if not isinstance(approval_id, str) or not approval_id:
            raise CommandError("APPROVAL_INVALID")
        row = conn.execute(
            "SELECT * FROM approvals WHERE tenant_id=? AND approval_id=?",
            (principal.tenant_id, approval_id),
        ).fetchone()
        if row is None:
            raise CommandError("NOT_FOUND")
        return row

    def _approval_matches(self, approval, command):
        checks = {
            "payload_hash": approval["payload_hash"],
            "rules_hash": approval["rules_hash"],
            "expected_payload_revision": approval["payload_revision"],
        }
        payload = command.get("payload") or {}
        if payload.get("rules_version") not in (None, approval["rules_version"]):
            return False
        if payload.get("input_manifest_hash") not in (None, approval["input_manifest_hash"]):
            return False
        for key, expected in checks.items():
            if command.get(key) not in (None, expected):
                return False
        return True

    def _approval_matches_run(self, approval, run):
        return (
            approval["payload_hash"] == run["artifact_hash"]
            and approval["payload_revision"] == run["payload_revision"]
            and approval["rules_hash"] == run["rules_hash"]
            and approval["rules_version"] == run["rules_version"]
            and approval["input_manifest_hash"] == run["input_manifest_hash"]
        )

    def _payload(self, command, allowed: set[str]):
        payload = command.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise CommandError("INVALID_INPUT")
        return payload

    def _source_from_payload(self, payload):
        revision = payload.get("source_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise CommandError("INVALID_INPUT")
        if not isinstance(payload.get("source_id"), str) or not payload["source_id"]:
            raise CommandError("INVALID_INPUT")
        if not isinstance(payload.get("origin_label"), str) or not payload["origin_label"]:
            raise CommandError("INVALID_INPUT")
        if not isinstance(payload.get("synthetic"), bool):
            raise CommandError("INVALID_INPUT")
        try:
            parse_utc(payload.get("captured_at"))
        except ValueError as exc:
            raise CommandError("INVALID_INPUT") from exc
        segments = payload.get("segments")
        if not isinstance(segments, list) or not segments:
            raise CommandError("INVALID_INPUT")
        clean = []
        seen = set()
        for segment in segments:
            if not isinstance(segment, dict) or set(segment) - {"locator", "text"}:
                raise CommandError("INVALID_INPUT")
            locator = segment.get("locator")
            text = segment.get("text")
            if not isinstance(locator, str) or not locator or locator in seen or not isinstance(text, str):
                raise CommandError("INVALID_INPUT")
            seen.add(locator)
            clean.append({"locator": locator, "text": text})
        content = {"origin_label": payload["origin_label"], "segments": clean}
        return {
            "captured_at": payload["captured_at"],
            "content_hash": hash_value(content),
            "origin_label": payload["origin_label"],
            "segments": clean,
            "source_id": payload["source_id"],
            "source_revision": revision,
            "synthetic": payload["synthetic"],
        }

    def _limit_bytes(self, run, source):
        rules = json.loads(run["rules_body_json"])
        incoming = len(json.dumps(source["segments"], ensure_ascii=False).encode("utf-8"))
        if incoming > rules["max_bytes"]:
            raise CommandError("INVALID_INPUT")

    def _attach_source(self, conn, run, source):
        rules = json.loads(run["rules_body_json"])
        refs = json.loads(run["source_refs_json"])
        entry = {"source_id": source["source_id"], "source_revision": source["source_revision"]}
        if entry in refs:
            return
        if len(refs) >= rules["max_documents"]:
            raise CommandError("INVALID_INPUT")
        refs.append(entry)
        cursor = conn.execute(
            "UPDATE runs SET source_refs_json=? WHERE tenant_id=? AND run_id=? AND state_version=?",
            (json.dumps(refs, ensure_ascii=False, sort_keys=True), run["tenant_id"], run["run_id"], run["state_version"]),
        )
        if cursor.rowcount != 1:
            raise CommandError("STALE_REVISION")

    def _run_sources(self, conn, principal, run):
        wanted = {(item["source_id"], item["source_revision"]) for item in json.loads(run["source_refs_json"])}
        return [
            source
            for source in self._sources(conn, principal.tenant_id)
            if (source["source_id"], source["source_revision"]) in wanted
        ]

    def _sources(self, conn, tenant_id):
        rows = conn.execute(
            "SELECT * FROM source_revisions WHERE tenant_id=? ORDER BY source_id, source_revision",
            (tenant_id,),
        ).fetchall()
        return [self._source_view(row) for row in rows]

    def _source_view(self, row):
        segments = json.loads(row["segments_json"])
        return {
            "captured_at": row["captured_at"],
            "content_hash": row["content_hash"],
            "origin_label": row["origin_label"],
            "segments": segments,
            "source_id": row["source_id"],
            "source_revision": row["source_revision"],
            "synthetic": bool(row["synthetic"]),
        }

    def _replace_claims(self, conn, principal, run_id, claims):
        conn.execute("DELETE FROM claims WHERE tenant_id=? AND run_id=?", (principal.tenant_id, run_id))
        for claim in claims:
            conn.execute(
                """INSERT INTO claims (
                    tenant_id, claim_id, run_id, schema_version, object_id, revision, statement,
                    kind, support_status, references_json, reviewer_note, claim_type
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
                (
                    principal.tenant_id,
                    str(uuid.uuid4()),
                    run_id,
                    SCHEMA_VERSION,
                    str(uuid.uuid4()),
                    claim["statement"],
                    claim["kind"],
                    claim["support_status"],
                    json.dumps(claim["references"], ensure_ascii=False, sort_keys=True),
                    claim["reviewer_note"],
                    claim["claim_type"],
                ),
            )

    def _insert_run(
        self,
        conn,
        principal,
        run_id,
        rules,
        manifest_hash,
        state,
        state_version,
        payload_revision,
        artifact_json,
        artifact_hash,
        origin,
        now,
        config_id=None,
        release_hash=None,
    ):
        conn.execute(
            """INSERT INTO runs (
                tenant_id, run_id, schema_version, object_id, revision, module_id, module_version,
                rules_version, rules_hash, rules_body_json, input_manifest_hash, source_refs_json,
                state, state_version, payload_revision, artifact_json, artifact_hash, blocked_from,
                origin_run_id, config_id, release_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)""",
            (
                principal.tenant_id,
                run_id,
                SCHEMA_VERSION,
                str(uuid.uuid4()),
                state_version,
                rules["module_id"],
                rules["module_version"],
                rules["rules_version"],
                hash_value(rules),
                json.dumps(rules, ensure_ascii=False, sort_keys=True),
                manifest_hash,
                state,
                state_version,
                payload_revision,
                artifact_json,
                artifact_hash,
                origin,
                config_id,
                release_hash,
                now,
            ),
        )

    def _check_release(self, conn, run):
        if run["release_hash"]:
            active = active_release_hash(conn, run["tenant_id"], run["config_id"])
            if active != run["release_hash"]:
                raise CommandError("STALE_RELEASE")

    def _rules_for(self, conn, version: str):
        row = conn.execute(
            "SELECT body_json, state FROM rule_sets WHERE rules_version=?",
            (version,),
        ).fetchone()
        if row is None or row["state"] != "active":
            raise CommandError("INVALID_INPUT")
        return json.loads(row["body_json"])

    def _install_rules(self, rules, now, initial=False):
        conn = self.store.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT OR IGNORE INTO rule_sets (rules_version, rules_hash, body_json, state, activated_at)
                   VALUES (?, ?, ?, 'active', ?)""",
                (rules["rules_version"], hash_value(rules), json.dumps(rules, ensure_ascii=False, sort_keys=True), now),
            )
            conn.execute("COMMIT")
        except Exception as exc:
            self._safe_rollback(conn)
            if initial:
                raise CommandError("STORAGE_UNAVAILABLE") from exc
            raise
        finally:
            conn.close()

    def _audit_replay(self, replay: Replay) -> None:
        conn = self.store.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._audit(
                conn,
                replay.principal,
                replay.run_id,
                replay.operation,
                None,
                None,
                None,
                None,
                replay.request_hash,
                "replayed",
                None,
            )
            conn.execute("COMMIT")
        except Exception:
            self._safe_rollback(conn)
            raise CommandError("STORAGE_UNAVAILABLE") from None
        finally:
            conn.close()

    def _get_run(self, conn, principal, run_id):
        cache_key = (principal.tenant_id, "run", run_id)
        if cache_key in self.cache:
            return self.cache[cache_key]
        run = self._load_run(conn, principal.tenant_id, run_id)
        view = self._run_view(run)
        self.cache[cache_key] = view
        return view

    def _release_diff(self, conn, principal, run_id):
        run = self._load_run(conn, principal.tenant_id, run_id)
        if not run["release_hash"]:
            return {"bound": False}
        active = active_release_hash(conn, run["tenant_id"], run["config_id"])
        if active is None:
            raise CommandError("NOT_FOUND")
        bound_doc = release_document(conn, run["tenant_id"], run["config_id"], run["release_hash"])
        active_doc = release_document(conn, run["tenant_id"], run["config_id"], active)
        if bound_doc is None or active_doc is None:
            raise CommandError("NOT_FOUND")
        stale = run["release_hash"] != active
        delta = release_diff(bound_doc, active_doc)
        if not stale:
            delta = {**delta, "changed": []}
        return {
            "active_release_hash": active,
            "bound": True,
            "run_release_hash": run["release_hash"],
            "stale": stale,
            **delta,
        }

    def _export(self, conn, principal, run_id, approval_id=None):
        row = self._load_run(conn, principal.tenant_id, run_id)
        run = self._get_run(conn, principal, run_id)
        claims = conn.execute(
            "SELECT statement, support_status, references_json FROM claims WHERE tenant_id=? AND run_id=?",
            (principal.tenant_id, run_id),
        ).fetchall()
        body = {
            "claims": [
                {
                    "references": json.loads(item["references_json"]),
                    "statement": item["statement"],
                    "support_status": item["support_status"],
                }
                for item in claims
            ],
            "run": run,
            "sources": self._run_sources(conn, principal, row),
        }
        if approval_id is not None:
            found = conn.execute(
                "SELECT approval_id, purpose FROM approvals WHERE tenant_id=? AND approval_id=?",
                (principal.tenant_id, approval_id),
            ).fetchone()
            if found is None:
                raise CommandError("NOT_FOUND")
            body["approval"] = {"approval_id": found["approval_id"], "purpose": found["purpose"]}
        return body

    def _get_receipt(self, conn, principal, payload, run_id):
        if set(payload) - {"operation", "idempotency_key"}:
            raise CommandError("INVALID_INPUT")
        operation = payload.get("operation")
        key = payload.get("idempotency_key")
        if not isinstance(operation, str) or not isinstance(key, str):
            raise CommandError("INVALID_INPUT")
        row = find(conn, "receipts", principal.tenant_id, principal.principal_id, operation, key)
        if row is None:
            raise CommandError("NOT_FOUND")
        if run_id and row["run_id"] not in (None, run_id):
            raise CommandError("NOT_FOUND")
        return json.loads(row["result_json"])

    def _search(self, conn, principal, query):
        hits = []
        for source in self._sources(conn, principal.tenant_id):
            for segment in source["segments"]:
                if query and query in segment["text"]:
                    hits.append({"locator": segment["locator"], "source_id": source["source_id"]})
        return {"hits": hits}

    def _run_view(self, run):
        return {
            "artifact": json.loads(run["artifact_json"]) if run["artifact_json"] else None,
            "artifact_hash": run["artifact_hash"],
            "blocked_from": run["blocked_from"],
            "config_id": run["config_id"],
            "created_at": run["created_at"],
            "created_at_berlin": format_berlin(run["created_at"]),
            "input_manifest_hash": run["input_manifest_hash"],
            "module_id": run["module_id"],
            "origin_run_id": run["origin_run_id"],
            "payload_revision": run["payload_revision"],
            "release_hash": run["release_hash"],
            "rules_hash": run["rules_hash"],
            "rules_version": run["rules_version"],
            "run_id": run["run_id"],
            "state": run["state"],
            "state_version": run["state_version"],
        }

    def _audit(self, conn, principal, run_id, operation, before, after, revision, rule_hash, payload_hash, outcome, error_code, approval_id=None, command_id=None):
        audit_append(
            conn,
            self.clock.now(),
            schema_version=SCHEMA_VERSION,
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            operation=operation,
            outcome=outcome,
            principal_kind=principal.kind,
            acting_role=self._current_acting_role,
            run_id=run_id,
            command_id=command_id,
            before=before,
            after=after,
            revision=revision,
            rule_hash=rule_hash,
            payload_hash=payload_hash,
            approval_id=approval_id,
            error_code=error_code,
        )

    def _request_view(self, command):
        return {
            "approval_id": command.get("approval_id"),
            "expected_payload_revision": command.get("expected_payload_revision"),
            "expected_state_version": command.get("expected_state_version"),
            "operation": command.get("operation"),
            "payload": command.get("payload") or {},
            "payload_hash": command.get("payload_hash"),
            "rules_hash": command.get("rules_hash"),
            "run_id": command.get("run_id"),
        }


def re_error():
    return ValueError

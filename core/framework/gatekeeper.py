import sqlite3
from datetime import datetime, timedelta

from framework.audit import append as audit_append
from framework.canon import SCHEMA_VERSION, canonical_bytes
from framework.clock import Clock
from framework.gate import CONTENT_HASH, decide
from framework.hermes_adapter import OBSERVATION_SCHEMA
from framework.principals import resolve
from framework.stopswitch import STOPPED_REASON, is_stopped

BUDGET_EXCEEDED = "budget_exceeded"
BUDGET_UNCONFIGURED = "budget_unconfigured"
GATE_OPERATION = "gate_decide"
OBSERVE_OPERATION = "gate_observe"
GATE_KINDS = frozenset({"agent", "service"})
OBSERVATION_KINDS = frozenset(
    {
        "gate_mismatch",
        "gate_unseen",
        "gate_ignored",
        "approval_granted",
        "approval_denied",
    }
)
OBSERVATION_KEYS = frozenset(
    {"schema_version", "runtime", "tool", "kind", "expected_hash", "observed_hash"}
)
RESULT_KEYS = (
    "decision",
    "reason",
    "action_class",
    "effective_level",
    "request_hash",
    "policy_hash",
    "audit_sequence",
)


class GateKeeper:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or Clock()

    def bind(self, principal_id, policy, unattended=False):
        def check(request):
            return self.check(principal_id, request, policy, unattended=unattended)

        return check

    def bind_observe(self, principal_id):
        def report(observation):
            return self.observe(principal_id, observation)

        return report

    def _observe_fail(self, reason):
        return {"ok": False, "reason": reason, "audit_sequence": None}

    def _observe_controls_ok(self, text):
        for ch in text:
            o = ord(ch)
            if o < 32 or o == 127:
                return False
        return True

    def _observe_field_ok(self, value):
        if not isinstance(value, str):
            return False
        if len(value) < 1 or len(value) > 128:
            return False
        return self._observe_controls_ok(value)

    def _observe_hash_ok(self, value):
        if value is None:
            return True
        if not isinstance(value, str):
            return False
        return CONTENT_HASH.fullmatch(value) is not None

    def _valid_observation(self, observation):
        if not isinstance(observation, dict):
            return False
        if set(observation.keys()) != OBSERVATION_KEYS:
            return False
        if observation["schema_version"] != OBSERVATION_SCHEMA:
            return False
        if not self._observe_field_ok(observation["runtime"]):
            return False
        if not self._observe_field_ok(observation["tool"]):
            return False
        if observation["kind"] not in OBSERVATION_KINDS:
            return False
        if not self._observe_hash_ok(observation["expected_hash"]):
            return False
        if not self._observe_hash_ok(observation["observed_hash"]):
            return False
        if observation["kind"] == "gate_unseen" and observation["expected_hash"] is not None:
            return False
        if observation["kind"] == "gate_ignored" and observation["expected_hash"] is None:
            return False
        if observation["kind"] in ("approval_granted", "approval_denied"):
            expected = observation["expected_hash"]
            observed = observation["observed_hash"]
            if expected is None or observed is None or expected != observed:
                return False
        return True

    def observe(self, principal_id, observation):
        if not self._valid_observation(observation):
            return self._observe_fail("invalid_observation")
        if not isinstance(principal_id, str) or not principal_id:
            return self._observe_fail("principal_unknown")
        try:
            conn = self.store.connect()
        except Exception:
            return self._observe_fail("storage_unavailable")
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
                principal = resolve(conn, principal_id)
                if principal is None:
                    self._rollback_quiet(conn)
                    return self._observe_fail("principal_unknown")
                if principal.kind not in GATE_KINDS:
                    row = audit_append(
                        conn,
                        self.clock.now(),
                        schema_version=SCHEMA_VERSION,
                        tenant_id=principal.tenant_id,
                        actor_id=principal.actor_id,
                        operation=OBSERVE_OPERATION,
                        outcome="deny",
                        principal_kind=principal.kind,
                        payload_hash=None,
                        rule_hash=None,
                        error_code="principal_kind_forbidden",
                    )
                    conn.execute("COMMIT")
                    return self._observe_fail("principal_kind_forbidden")
                row = audit_append(
                    conn,
                    self.clock.now(),
                    schema_version=SCHEMA_VERSION,
                    tenant_id=principal.tenant_id,
                    actor_id=principal.actor_id,
                    operation=OBSERVE_OPERATION,
                    outcome="flagged",
                    principal_kind=principal.kind,
                    payload_hash=observation["observed_hash"],
                    before=observation["expected_hash"],
                    error_code=observation["kind"],
                )
                conn.execute("COMMIT")
                return {"ok": True, "reason": None, "audit_sequence": row["sequence"]}
            except Exception:
                self._rollback_quiet(conn)
                return self._observe_fail("audit_unavailable")
        finally:
            conn.close()

    def _deny(self, reason, **fields):
        out = {key: None for key in RESULT_KEYS}
        out["decision"] = "deny"
        out["reason"] = reason
        for key, value in fields.items():
            if key in out:
                out[key] = value
        return out

    def _rollback_quiet(self, conn):
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def check(self, principal_id, request, policy, unattended=False):
        try:
            if not isinstance(principal_id, str) or not principal_id:
                return self._deny("principal_unknown")
            try:
                conn = self.store.connect()
            except Exception:
                return self._deny("storage_unavailable")
            try:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    principal = resolve(conn, principal_id)
                    if principal is None:
                        self._rollback_quiet(conn)
                        return self._deny("principal_unknown")
                    if principal.kind not in GATE_KINDS:
                        row = audit_append(
                            conn,
                            self.clock.now(),
                            schema_version=SCHEMA_VERSION,
                            tenant_id=principal.tenant_id,
                            actor_id=principal.actor_id,
                            operation=GATE_OPERATION,
                            outcome="deny",
                            principal_kind=principal.kind,
                            payload_hash=None,
                            rule_hash=None,
                            error_code="principal_kind_forbidden",
                        )
                        conn.execute("COMMIT")
                        return self._deny(
                            "principal_kind_forbidden",
                            audit_sequence=row["sequence"],
                        )
                    if is_stopped(conn, principal.tenant_id):
                        row = audit_append(
                            conn,
                            self.clock.now(),
                            schema_version=SCHEMA_VERSION,
                            tenant_id=principal.tenant_id,
                            actor_id=principal.actor_id,
                            operation=GATE_OPERATION,
                            outcome="deny",
                            principal_kind=principal.kind,
                            payload_hash=None,
                            rule_hash=None,
                            error_code=STOPPED_REASON,
                        )
                        conn.execute("COMMIT")
                        return self._deny(
                            STOPPED_REASON,
                            audit_sequence=row["sequence"],
                        )
                    result = decide(request, policy, unattended=unattended)
                    now = self.clock.now()
                    if (
                        result["decision"] in ("allow", "needs_approval")
                        and isinstance(policy, dict)
                        and "limits" in policy
                    ):
                        action_class = result["action_class"]
                        limits = policy["limits"]
                        if action_class not in limits:
                            result = {
                                **result,
                                "decision": "deny",
                                "reason": BUDGET_UNCONFIGURED,
                            }
                        else:
                            spec = limits[action_class]
                            since_dt = datetime.strptime(
                                now, "%Y-%m-%dT%H:%M:%SZ"
                            ) - timedelta(seconds=spec["window_seconds"])
                            since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                            used = conn.execute(
                                "SELECT COUNT(*) FROM gate_calls WHERE tenant_id=? AND action_class=? AND at > ?",
                                (principal.tenant_id, action_class, since),
                            ).fetchone()[0]
                            if used >= spec["max_calls"]:
                                result = {
                                    **result,
                                    "decision": "deny",
                                    "reason": BUDGET_EXCEEDED,
                                }
                    error_code = None if result["decision"] == "allow" else result["reason"]
                    before = None
                    if result["request_hash"] is not None:
                        before = canonical_bytes(
                            {
                                "action": request["action"],
                                "action_class": result["action_class"],
                                "content_hash": request["content_hash"],
                                "tool": request["tool"],
                            }
                        ).decode("utf-8")
                    row = audit_append(
                        conn,
                        now,
                        schema_version=SCHEMA_VERSION,
                        tenant_id=principal.tenant_id,
                        actor_id=principal.actor_id,
                        operation=GATE_OPERATION,
                        outcome=result["decision"],
                        principal_kind=principal.kind,
                        payload_hash=result["request_hash"],
                        rule_hash=result["policy_hash"],
                        before=before,
                        after=result["effective_level"],
                        error_code=error_code,
                    )
                    if (
                        result["decision"] in ("allow", "needs_approval")
                        and isinstance(policy, dict)
                        and "limits" in policy
                    ):
                        conn.execute(
                            "INSERT INTO gate_calls (tenant_id, action_class, at, audit_sequence) VALUES (?,?,?,?)",
                            (
                                principal.tenant_id,
                                result["action_class"],
                                now,
                                row["sequence"],
                            ),
                        )
                    conn.execute("COMMIT")
                    packaged = {key: result.get(key) for key in RESULT_KEYS if key != "audit_sequence"}
                    packaged["audit_sequence"] = row["sequence"]
                    return packaged
                except Exception:
                    self._rollback_quiet(conn)
                    return self._deny("audit_unavailable")
            finally:
                conn.close()
        except Exception:
            return self._deny("audit_unavailable")
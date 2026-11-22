import sqlite3

from framework.audit import append as audit_append
from framework.canon import SCHEMA_VERSION
from framework.clock import Clock
from framework.principals import resolve

STOP_OPERATION = "gate_stop"
RESUME_OPERATION = "gate_resume"
STOPPED_REASON = "stopped"
STOP_KINDS = frozenset({"human"})
STOP_ROLES = frozenset({"operator", "reviewer", "author", "customer"})
RESUME_ROLES = frozenset({"author"})


def is_stopped(conn, tenant_id) -> bool:
    row = conn.execute(
        "SELECT stopped FROM gate_stops WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    if row is None:
        return False
    return int(row["stopped"]) == 1


def _set_state(conn, tenant_id, stopped, now, principal_id, audit_sequence):
    conn.execute(
        """INSERT INTO gate_stops (tenant_id, stopped, changed_at, changed_by, audit_sequence)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id) DO UPDATE SET
        stopped=excluded.stopped,
        changed_at=excluded.changed_at,
        changed_by=excluded.changed_by,
        audit_sequence=excluded.audit_sequence""",
        (tenant_id, int(stopped), now, principal_id, audit_sequence),
    )


class StopSwitch:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or Clock()

    def _rollback_quiet(self, conn):
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _fail(self, reason, changed=False, stopped=None, audit_sequence=None):
        return {
            "ok": False,
            "reason": reason,
            "changed": changed,
            "stopped": stopped,
            "audit_sequence": audit_sequence,
        }

    def _ok(self, changed, stopped, audit_sequence):
        return {
            "ok": True,
            "reason": None,
            "changed": changed,
            "stopped": stopped,
            "audit_sequence": audit_sequence,
        }

    def _mutate(self, principal_id, want_stopped):
        if not isinstance(principal_id, str) or not principal_id:
            return self._fail("principal_unknown")
        try:
            conn = self.store.connect()
        except Exception:
            return self._fail("storage_unavailable")
        operation = STOP_OPERATION if want_stopped else RESUME_OPERATION
        allowed_roles = STOP_ROLES if want_stopped else RESUME_ROLES
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
                principal = resolve(conn, principal_id)
                if principal is None:
                    self._rollback_quiet(conn)
                    return self._fail("principal_unknown")
                current = is_stopped(conn, principal.tenant_id)
                before = "stopped" if current else "running"
                if principal.kind not in STOP_KINDS:
                    row = audit_append(
                        conn,
                        self.clock.now(),
                        schema_version=SCHEMA_VERSION,
                        tenant_id=principal.tenant_id,
                        actor_id=principal.actor_id,
                        operation=operation,
                        outcome="deny",
                        principal_kind=principal.kind,
                        before=before,
                        after=before,
                        payload_hash=None,
                        rule_hash=None,
                        error_code="principal_kind_forbidden",
                        acting_role=None,
                    )
                    conn.execute("COMMIT")
                    return self._fail(
                        "principal_kind_forbidden",
                        stopped=current,
                        audit_sequence=row["sequence"],
                    )
                if not principal.roles & allowed_roles:
                    row = audit_append(
                        conn,
                        self.clock.now(),
                        schema_version=SCHEMA_VERSION,
                        tenant_id=principal.tenant_id,
                        actor_id=principal.actor_id,
                        operation=operation,
                        outcome="deny",
                        principal_kind=principal.kind,
                        before=before,
                        after=before,
                        payload_hash=None,
                        rule_hash=None,
                        error_code="role_forbidden",
                        acting_role=None,
                    )
                    conn.execute("COMMIT")
                    return self._fail(
                        "role_forbidden",
                        stopped=current,
                        audit_sequence=row["sequence"],
                    )
                if want_stopped:
                    acting_role = sorted(principal.roles & STOP_ROLES)[0]
                else:
                    acting_role = "author"
                if current == want_stopped:
                    row = audit_append(
                        conn,
                        self.clock.now(),
                        schema_version=SCHEMA_VERSION,
                        tenant_id=principal.tenant_id,
                        actor_id=principal.actor_id,
                        operation=operation,
                        outcome="noop",
                        principal_kind=principal.kind,
                        before=before,
                        after=before,
                        payload_hash=None,
                        rule_hash=None,
                        acting_role=acting_role,
                    )
                    conn.execute("COMMIT")
                    return self._ok(False, current, row["sequence"])
                after = "stopped" if want_stopped else "running"
                row = audit_append(
                    conn,
                    self.clock.now(),
                    schema_version=SCHEMA_VERSION,
                    tenant_id=principal.tenant_id,
                    actor_id=principal.actor_id,
                    operation=operation,
                    outcome="allow",
                    principal_kind=principal.kind,
                    before=before,
                    after=after,
                    payload_hash=None,
                    rule_hash=None,
                    acting_role=acting_role,
                )
                _set_state(
                    conn,
                    principal.tenant_id,
                    want_stopped,
                    self.clock.now(),
                    principal.principal_id,
                    row["sequence"],
                )
                conn.execute("COMMIT")
                return self._ok(True, want_stopped, row["sequence"])
            except Exception:
                self._rollback_quiet(conn)
                return self._fail("audit_unavailable")
        finally:
            conn.close()

    def stop(self, principal_id):
        return self._mutate(principal_id, True)

    def resume(self, principal_id):
        return self._mutate(principal_id, False)

    def status(self, tenant_id):
        conn = self.store.connect()
        try:
            row = conn.execute(
                "SELECT stopped, changed_at, changed_by, audit_sequence"
                " FROM gate_stops WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return {
                "tenant": tenant_id,
                "stopped": False,
                "changed_at": None,
                "changed_by": None,
                "audit_sequence": None,
            }
        return {
            "tenant": tenant_id,
            "stopped": int(row["stopped"]) == 1,
            "changed_at": row["changed_at"],
            "changed_by": row["changed_by"],
            "audit_sequence": int(row["audit_sequence"]),
        }
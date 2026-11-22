import sqlite3

from framework.canon import PRINCIPAL_ID

SCHEMA = """
CREATE TABLE IF NOT EXISTS rule_sets (
    rules_version TEXT PRIMARY KEY,
    rules_hash TEXT NOT NULL,
    body_json TEXT NOT NULL,
    state TEXT NOT NULL,
    activated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    module_id TEXT NOT NULL,
    module_version TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    rules_hash TEXT NOT NULL,
    rules_body_json TEXT NOT NULL,
    input_manifest_hash TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    state TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    payload_revision INTEGER NOT NULL,
    artifact_json TEXT,
    artifact_hash TEXT,
    blocked_from TEXT,
    origin_run_id TEXT,
    config_id TEXT,
    release_hash TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, run_id)
);
CREATE TABLE IF NOT EXISTS source_revisions (
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    origin_label TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    synthetic INTEGER NOT NULL,
    segments_json TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, source_id, source_revision)
);
CREATE TABLE IF NOT EXISTS claims (
    tenant_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    statement TEXT NOT NULL,
    kind TEXT NOT NULL,
    support_status TEXT NOT NULL,
    references_json TEXT NOT NULL,
    reviewer_note TEXT,
    claim_type TEXT NOT NULL,
    PRIMARY KEY (tenant_id, claim_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    tenant_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    approver_id TEXT NOT NULL,
    approver_role TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_revision INTEGER NOT NULL,
    rules_version TEXT NOT NULL,
    rules_hash TEXT NOT NULL,
    input_manifest_hash TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    revoked_at TEXT,
    PRIMARY KEY (tenant_id, approval_id)
);
CREATE TABLE IF NOT EXISTS receipts (
    tenant_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    request_hash TEXT NOT NULL,
    result_id TEXT NOT NULL,
    result_revision INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    result_json TEXT NOT NULL,
    run_id TEXT,
    PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    run_id TEXT,
    command_id TEXT,
    before_state TEXT,
    after_state TEXT,
    revision INTEGER,
    rule_hash TEXT,
    payload_hash TEXT,
    approval_id TEXT,
    outcome TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    operation TEXT,
    error_code TEXT,
    principal_kind TEXT,
    acting_role TEXT,
    prev_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    UNIQUE (tenant_id, sequence)
);
CREATE TABLE IF NOT EXISTS audit_heads (
    tenant_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    head_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_APPEND_ONLY');
END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_APPEND_ONLY');
END;
CREATE TABLE IF NOT EXISTS principals (
    principal_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('human', 'agent', 'service')),
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS role_bindings (
    principal_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('operator', 'reviewer', 'author', 'customer')),
    PRIMARY KEY (principal_id, role)
);
CREATE TABLE IF NOT EXISTS gate_stops (
    tenant_id TEXT PRIMARY KEY,
    stopped INTEGER NOT NULL CHECK(stopped IN (0, 1)),
    changed_at TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    audit_sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_calls (
    tenant_id TEXT NOT NULL,
    action_class TEXT NOT NULL,
    at TEXT NOT NULL,
    audit_sequence INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS gate_calls_window ON gate_calls (tenant_id, action_class, at);
CREATE TABLE IF NOT EXISTS feedback (
    tenant_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    note TEXT NOT NULL,
    note_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, feedback_id)
);
CREATE TABLE IF NOT EXISTS config_templates (
    template_id TEXT NOT NULL,
    template_version TEXT NOT NULL,
    body_json TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    synthetic INTEGER NOT NULL,
    PRIMARY KEY (template_id, template_version)
);
CREATE TABLE IF NOT EXISTS config_releases (
    tenant_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    release_no INTEGER NOT NULL,
    template_id TEXT NOT NULL,
    template_version TEXT NOT NULL,
    resolved_json TEXT NOT NULL,
    release_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, config_id, release_no)
);
CREATE TABLE IF NOT EXISTS config_active (
    tenant_id TEXT NOT NULL,
    config_id TEXT NOT NULL,
    release_no INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, config_id)
);
CREATE TABLE IF NOT EXISTS config_idempotency (
    tenant_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
);
"""


class Store:
    def __init__(self, path: str):
        self.path = path
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        self._migrate(conn)
        return conn

    def _migrate(self, conn: sqlite3.Connection) -> None:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "audit_counter" in tables:
            raise RuntimeError("schema_audit_outdated")
        if "role_bindings" in tables:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='role_bindings'"
            ).fetchone()
            if row and row[0] and "'customer'" not in row[0]:
                raise RuntimeError("schema_role_bindings_outdated")
        for table in ("receipts", "config_idempotency"):
            if table in tables and not self._idempotency_actor_pk(conn, table):
                raise RuntimeError("schema_idempotency_outdated")
        if "audit_events" in tables:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()}
            if "event_hash" not in columns:
                raise RuntimeError("schema_audit_outdated")
            if "principal_kind" not in columns:
                conn.execute("ALTER TABLE audit_events ADD COLUMN principal_kind TEXT")
            if "acting_role" not in columns:
                conn.execute("ALTER TABLE audit_events ADD COLUMN acting_role TEXT")
        if "audit_heads" in tables:
            head_columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_heads)").fetchall()}
            if "head_hash" not in head_columns:
                raise RuntimeError("schema_audit_outdated")
        if "runs" in tables:
            run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "config_id" not in run_columns:
                conn.execute("ALTER TABLE runs ADD COLUMN config_id TEXT")
            if "release_hash" not in run_columns:
                conn.execute("ALTER TABLE runs ADD COLUMN release_hash TEXT")

    def _idempotency_actor_pk(self, conn: sqlite3.Connection, table: str) -> bool:
        columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(int(row["pk"]) > 0 and row["name"] == "actor_id" for row in columns)

    def add_principal(self, principal_id: str, tenant_id: str, kind: str) -> None:
        if PRINCIPAL_ID.fullmatch(principal_id) is None:
            raise ValueError("invalid_principal_id")
        if kind not in ("human", "agent", "service"):
            raise ValueError("invalid_kind")
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO principals (principal_id, tenant_id, kind, disabled) VALUES (?, ?, ?, 0)",
                (principal_id, tenant_id, kind),
            )
            conn.commit()
        finally:
            conn.close()

    def bind_role(self, principal_id: str, role: str) -> None:
        if role not in ("operator", "reviewer", "author", "customer"):
            raise ValueError("invalid_role")
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO role_bindings (principal_id, role) VALUES (?, ?)",
                (principal_id, role),
            )
            conn.commit()
        finally:
            conn.close()

    def unbind_role(self, principal_id: str, role: str) -> None:
        if role not in ("operator", "reviewer", "author", "customer"):
            raise ValueError("invalid_role")
        conn = self.connect()
        try:
            conn.execute(
                "DELETE FROM role_bindings WHERE principal_id=? AND role=?",
                (principal_id, role),
            )
            conn.commit()
        finally:
            conn.close()

    def disable_principal(self, principal_id: str) -> None:
        conn = self.connect()
        try:
            conn.execute(
                "UPDATE principals SET disabled=1 WHERE principal_id=?",
                (principal_id,),
            )
            conn.commit()
        finally:
            conn.close()

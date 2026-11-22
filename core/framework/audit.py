from framework.canon import hash_value

GENESIS = "sha256:" + "0" * 64

CHAIN_FIELDS = (
    "event_id",
    "sequence",
    "schema_version",
    "tenant_id",
    "actor_id",
    "run_id",
    "command_id",
    "before_state",
    "after_state",
    "revision",
    "rule_hash",
    "payload_hash",
    "approval_id",
    "outcome",
    "occurred_at",
    "operation",
    "error_code",
    "principal_kind",
    "acting_role",
    "prev_hash",
)


def _digest_body(row: dict) -> str:
    return hash_value({key: row[key] for key in CHAIN_FIELDS})


def append(
    conn,
    now: str,
    *,
    schema_version: str,
    tenant_id: str,
    actor_id: str,
    operation,
    outcome: str,
    principal_kind=None,
    acting_role=None,
    run_id=None,
    command_id=None,
    before=None,
    after=None,
    revision=None,
    rule_hash=None,
    payload_hash=None,
    approval_id=None,
    error_code=None,
) -> dict:
    head = conn.execute(
        "SELECT seq, head_hash FROM audit_heads WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    if head is None:
        sequence = 1
        prev_hash = GENESIS
    else:
        sequence = int(head["seq"]) + 1
        prev_hash = head["head_hash"]
    event_id = hash_value({"sequence": sequence, "tenant_id": tenant_id})
    row = {
        "event_id": event_id,
        "sequence": sequence,
        "schema_version": schema_version,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "run_id": run_id,
        "command_id": command_id,
        "before_state": before,
        "after_state": after,
        "revision": revision,
        "rule_hash": rule_hash,
        "payload_hash": payload_hash,
        "approval_id": approval_id,
        "outcome": outcome,
        "occurred_at": now,
        "operation": operation,
        "error_code": error_code,
        "principal_kind": principal_kind,
        "acting_role": acting_role,
        "prev_hash": prev_hash,
    }
    event_hash = _digest_body(row)
    row["event_hash"] = event_hash
    conn.execute(
        """INSERT INTO audit_events (
            event_id, sequence, schema_version, tenant_id, actor_id, run_id, command_id,
            before_state, after_state, revision, rule_hash, payload_hash, approval_id,
            outcome, occurred_at, operation, error_code, principal_kind, acting_role,
            prev_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["event_id"],
            row["sequence"],
            row["schema_version"],
            row["tenant_id"],
            row["actor_id"],
            row["run_id"],
            row["command_id"],
            row["before_state"],
            row["after_state"],
            row["revision"],
            row["rule_hash"],
            row["payload_hash"],
            row["approval_id"],
            row["outcome"],
            row["occurred_at"],
            row["operation"],
            row["error_code"],
            row["principal_kind"],
            row["acting_role"],
            row["prev_hash"],
            row["event_hash"],
        ),
    )
    conn.execute(
        """INSERT INTO audit_heads (tenant_id, seq, head_hash) VALUES (?, ?, ?)
           ON CONFLICT(tenant_id) DO UPDATE SET seq = excluded.seq, head_hash = excluded.head_hash""",
        (tenant_id, sequence, event_hash),
    )
    return row


def _audit_head(conn, tenant_id: str):
    head_row = conn.execute(
        "SELECT seq, head_hash FROM audit_heads WHERE tenant_id=?",
        (tenant_id,),
    ).fetchone()
    if head_row is None:
        return None
    return {"head_hash": head_row[1], "seq": int(head_row[0])}


def _head_mismatch(head, count: int, last_event_hash) -> bool:
    if count == 0:
        return head is not None
    if head is None:
        return True
    return head["seq"] != count or head["head_hash"] != last_event_hash


def _verify_result(ok: bool, count: int, head, first_break, mismatch: bool) -> dict:
    if mismatch:
        ok = False
    return {
        "count": count,
        "first_break": first_break,
        "head": head,
        "head_mismatch": mismatch,
        "ok": ok,
    }


def verify(conn, tenant_id: str) -> dict:
    head = _audit_head(conn, tenant_id)
    rows = conn.execute(
        """SELECT sequence, prev_hash, event_hash, event_id, schema_version, tenant_id, actor_id,
           run_id, command_id, before_state, after_state, revision, rule_hash, payload_hash,
           approval_id, outcome, occurred_at, operation, error_code, principal_kind, acting_role
           FROM audit_events WHERE tenant_id=? ORDER BY sequence""",
        (tenant_id,),
    ).fetchall()
    count = len(rows)
    if count == 0:
        mismatch = _head_mismatch(head, count, None)
        return _verify_result(True, 0, GENESIS, None, mismatch)
    tail_hash = rows[-1]["event_hash"]
    mismatch = _head_mismatch(head, count, tail_hash)

    def fail(sequence: int, ok: bool = False):
        return _verify_result(ok, count, tail_hash, sequence, mismatch)

    expected_prev = GENESIS
    for index, row in enumerate(rows, start=1):
        sequence = int(row["sequence"])
        if sequence != index:
            return fail(sequence)
        if row["prev_hash"] != expected_prev:
            return fail(sequence)
        body = {
            "event_id": row["event_id"],
            "sequence": row["sequence"],
            "schema_version": row["schema_version"],
            "tenant_id": row["tenant_id"],
            "actor_id": row["actor_id"],
            "run_id": row["run_id"],
            "command_id": row["command_id"],
            "before_state": row["before_state"],
            "after_state": row["after_state"],
            "revision": row["revision"],
            "rule_hash": row["rule_hash"],
            "payload_hash": row["payload_hash"],
            "approval_id": row["approval_id"],
            "outcome": row["outcome"],
            "occurred_at": row["occurred_at"],
            "operation": row["operation"],
            "error_code": row["error_code"],
            "principal_kind": row["principal_kind"],
            "acting_role": row["acting_role"],
            "prev_hash": row["prev_hash"],
        }
        if _digest_body(body) != row["event_hash"]:
            return fail(sequence)
        expected_prev = row["event_hash"]
    return _verify_result(True, count, tail_hash, None, mismatch)
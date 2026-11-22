import json

ALLOWED = frozenset({"receipts", "config_idempotency"})


def find(conn, table, tenant_id, actor_id, operation, key):
    if table not in ALLOWED:
        raise ValueError("invalid_table")
    if table == "receipts":
        return conn.execute(
            "SELECT * FROM receipts WHERE tenant_id=? AND actor_id=? AND operation=? AND idempotency_key=?",
            (tenant_id, actor_id, operation, key),
        ).fetchone()
    return conn.execute(
        "SELECT request_hash, result_json FROM config_idempotency WHERE tenant_id=? AND actor_id=? AND operation=? AND idempotency_key=?",
        (tenant_id, actor_id, operation, key),
    ).fetchone()


def replay_result(row, request_hash, conflict_error):
    if row is None:
        return None
    if row["request_hash"] != request_hash:
        raise conflict_error
    return json.loads(row["result_json"])
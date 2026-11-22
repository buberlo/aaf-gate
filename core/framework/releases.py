import json


def active_release_hash(conn, tenant_id, config_id):
    row = conn.execute(
        """SELECT r.release_hash FROM config_active a
           JOIN config_releases r
             ON r.tenant_id = a.tenant_id AND r.config_id = a.config_id AND r.release_no = a.release_no
           WHERE a.tenant_id = ? AND a.config_id = ?""",
        (tenant_id, config_id),
    ).fetchone()
    if row is None:
        return None
    return row["release_hash"]


def release_document(conn, tenant_id, config_id, release_hash):
    row = conn.execute(
        """SELECT resolved_json FROM config_releases
           WHERE tenant_id = ? AND config_id = ? AND release_hash = ?
           LIMIT 1""",
        (tenant_id, config_id, release_hash),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["resolved_json"])


def diff(old: dict, new: dict) -> dict:
    old_values = old.get("values") or {}
    new_values = new.get("values") or {}
    old_access = old.get("field_access") or {}
    new_access = new.get("field_access") or {}
    names = sorted(set(old_values) | set(new_values))
    changed = []
    values = {}
    for name in names:
        old_v = old_values.get(name)
        new_v = new_values.get(name)
        if old_v != new_v:
            changed.append(name)
            access = new_access.get(name, old_access.get(name))
            if access == "customer_editable":
                values[name] = {
                    "from": old_values[name] if name in old_values else None,
                    "to": new_values[name] if name in new_values else None,
                }
    template_changed = (
        old.get("template_id") != new.get("template_id")
        or old.get("template_version") != new.get("template_version")
        or old.get("template_body_hash") != new.get("template_body_hash")
    )
    floor_changed = old.get("core_floor_hash") != new.get("core_floor_hash")
    return {
        "changed": sorted(changed),
        "floor_changed": floor_changed,
        "template_changed": template_changed,
        "values": values,
    }
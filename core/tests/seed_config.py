def seed_config(store, principals):
    conn = store.connect()
    try:
        existing = {
            row["principal_id"]
            for row in conn.execute("SELECT principal_id FROM principals").fetchall()
        }
    finally:
        conn.close()
    for principal_id, tenant_id, kind, roles in principals:
        if principal_id in existing:
            continue
        store.add_principal(principal_id, tenant_id, kind)
        for role in roles:
            store.bind_role(principal_id, role)
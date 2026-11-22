from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    principal_id: str
    tenant_id: str
    kind: str | None
    roles: frozenset

    @property
    def actor_id(self) -> str:
        return self.principal_id


def resolve(conn, principal_id: str) -> Principal | None:
    row = conn.execute(
        "SELECT principal_id, tenant_id, kind, disabled FROM principals WHERE principal_id=?",
        (principal_id,),
    ).fetchone()
    if row is None or int(row["disabled"]) != 0:
        return None
    bindings = conn.execute(
        "SELECT role FROM role_bindings WHERE principal_id=?",
        (principal_id,),
    ).fetchall()
    roles = frozenset(binding["role"] for binding in bindings)
    return Principal(row["principal_id"], row["tenant_id"], row["kind"], roles)

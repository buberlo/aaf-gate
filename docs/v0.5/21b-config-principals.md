# ConfigPlane and principals (draft v0.5, step 21b)

| Operation | Role |
|-----------|--------|
| register_template, release, rollback | author |
| apply_changes, active, release_record | author or customer (apply: author before customer) |

- Agent principals cannot perform writing config operations; read with matching binding remains allowed.
- Every rejected write is audited (`principal_kind`, `acting_role`, error_code `FORBIDDEN`).
- Outdated `role_bindings` schema without author/customer in CHECK fails on open with `schema_role_bindings_outdated`, without silent migration.
- Open: idempotency key per principal (backlog 67).

Status: implemented and tested on PR branch, not released for production.

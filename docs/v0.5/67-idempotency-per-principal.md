# Idempotency per principal (draft v0.5, step 67)

Rights check, then lookup over (tenant_id, principal_id, operation, idempotency_key), request-hash compare, on hit replay with audit (`outcome=replayed`), else work and receipt. Rejected config operations in `_with_key` are audited (`rejected`); the key stays free.

Service receipts and `config_idempotency` bind `actor_id` in the primary key. Outdated schema fails with `schema_idempotency_outdated`.

Open: key expiry (backlog 68).

Status: implemented and tested on PR branch, not released for production.

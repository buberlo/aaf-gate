# Principals and role bindings (draft v0.5, step 21a)

- Service callers pass only a `principal_id`. Core reads tenant, kind (human, agent, service), and roles from storage.
- Roles per operation: operator prepares, reviewer approves, returns, closes, or confirms. Missing role → rejection and audit.
- Core floor: an agent principal can never approve, return, close, rework, confirm facts, or activate rules, even with binding.
- Limit: `principal_id` is still self-reported by the caller. Security boundary moves when core takes it from the transport channel (backlog 22/41). ConfigPlane follows in step 21b.

Status: implemented and tested only on PR branch, not released for production.

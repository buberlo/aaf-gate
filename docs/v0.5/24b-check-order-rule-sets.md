# Check order for field changes and activate_rules (draft v0.5, step 24b)

ConfigPlane evaluates `changes` in sorted field order: unknown field → `INVALID_CONFIG`; `fixed` → `INVALID_CONFIG`; for customers anything except `customer_editable` → `LOCKED_FIELD`; then type, bounds, and floor as before.

`activate_rules` requires role `author` (not `reviewer`); agents with `author` remain excluded. Rights are rechecked before every replay — `unbind_role` and repeat call with same idempotency key → `FORBIDDEN`, no replay.

Open: `rule_sets` still global; scope per template/tenant is a separate step. Key expiry (68).

Status: implemented and tested on PR branch, not released for production.

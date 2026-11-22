# Shared audit write function (draft v0.5, step 54)

One write path `audit.append` for service and ConfigPlane. Sequence per tenant via `audit_heads`; `event_id` is `hash_value({"sequence", "tenant_id"})`. ConfigPlane events from `_with_key` carry `command_id` (idempotency key). Preparation for hash chain (47) and chain copy export (38).

Note: rows in a shared database are not tenant isolation.

Status: implemented and tested on PR branch, not released for production.

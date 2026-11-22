# Audit v1 (draft v0.5, step 47)

Hash chain per tenant via `prev_hash` and `event_hash` with GENESIS; head in `audit_heads.head_hash`; verify checks continuity and hashes. Rejections store attempted operation and `command_id`. Triggers prevent UPDATE/DELETE via app connection; they do not replace file permissions. Truncation at the end is detected only by an exported chain copy.

Status: implemented and tested on PR branch, not released for production.

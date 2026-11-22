# Known write paths per table (draft v0.5)

- `core/tests/test_architecture.py` freezes every place that writes to a table via SQL literal (table, module, innermost function).
- A new or removed write site needs a deliberate list change. The change is visible in the PR diff.
- Modules without database writes: appliance, cli, canon, clock, egress, errors, rules.
- Table names in SQL are always literals. Dynamic column lists are allowed (`runs._cas`).
- Known double path: `audit_events` and `audit_counter` written by service and configplane. Target is one shared audit write function.
- Limit: the test sees only Python in `core/framework`. Direct SQLite writes outside the API are not prevented. That remains file permissions and future triggers.

Status: implemented and tested only on PR branch, not released for production.

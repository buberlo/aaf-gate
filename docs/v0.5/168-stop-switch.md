# 168 Kill switch (V1-1)

## Status

Implemented and locally tested (core 41575e0, CLI 17831ee, docs and device checklist in this commit). Not tested on device. Not released for production.

## Effect

Stop applies per tenant. While active, the gate rejects every request with reason `stopped` and writes an audit entry with `operation` `gate_decide` and `error_code` `stopped`.

## Roles

Any human principal of the tenant with at least one of `operator`, `reviewer`, `author`, or `customer` may stop. Only a human principal with role `author` may resume. Agent and service principals may neither stop nor resume. Stop and resume are logged as `gate_stop` and `gate_resume`, as are rejected attempts.

## Commands

- `gate-principal-add`: create a human principal with roles.
- `gate-stop`: set stop for the tenant of the principal.
- `gate-resume`: clear stop (author role only).
- `gate-status`: read stop state (read-only, no migration).
- `gate-audit --summary`: includes `summary.stops` with `stop`, `resume`, `rejected`, `denied_while_stopped`, and current `stopped`.

## Exit codes (gate-stop / gate-resume)

- `0` when `ok` is true (also for `noop` with `changed` false).
- `1` for `principal_unknown`, `principal_kind_forbidden`, or `role_forbidden`.
- `2` for precheck, storage, or audit errors.

## Open limits

Creating principals and role bindings via `gate-principal-add` is not audited and is not atomic (known, post-v1). Customer admin consent on resume is organizational. How Hermes shows rejection while stopped must be checked on device. The device checklist includes the stop probe under Pilot 1.

Not tested on device.

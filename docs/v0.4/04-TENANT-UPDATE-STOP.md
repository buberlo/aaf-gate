# Tenant, update, stop · v0.4

As of 12.11.2026 Europe/Berlin. Draft. Code in this track separates only logically by `tenant_id`.

## Tenant

Target for a real customer: own runtime, own credentials, own database. None of that is built. Profiles, roles, or a second principal in the same SQLite file are not isolation. T12 showed that for the earlier core. The configuration model behaves the same: a foreign principal does not see the release, but the file still contains it.

## Update

A new template or customer version becomes active only as a new release. There is no silent overwrite of the running number. An update is therefore the same path as a release: build, hash, move pointer, audit.

## Stop and fallback

Before assistance later acts outward, the customer must be able to stop it without Konrad and continue the task by hand. This switch is not implemented in this increment. The prototype does not send. The switch is the next behavior increment, not a claim about today’s code.

Addendum 21.11.2026: The switch is now in core and CLI, locally tested, not on device yet. Status and limits: [v0.5/168-stop-switch.md](../v0.5/168-stop-switch.md).
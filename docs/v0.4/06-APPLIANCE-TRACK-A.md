# Track A · appliance · v0.4

As of 12.11.2026, 17:01 Europe/Berlin. Draft plus a locally checked package without a Mac. Nothing is production.

## Goal

The customer gets a Mac mini configured by Konrad. Hermes Agent runs on it with a profile from the industry template. The customer should not bypass the gate in normal use. Other hardware or a VM may follow. That is not designed.

## Accounts

Only Konrad is admin. The customer works as a standard user without admin rights and without `sudo`. Whether macOS enforces this separation must be verified on device. Physical access by the customer lifts a pure software gate. That is an honest limit, not a promise of absolute lockdown.

## Hermes on device

Documented: managed scope as root-owned files, default `/etc/hermes/config.yaml` and `/etc/hermes/.env`, directory mode `0755`, files `0644`. Only pinned keys apply to all users. `hermes config set` rejects them. `hermes doctor` shows the directory.

Not documented: a dedicated macOS layout. Docs call v1 Linux/POSIX-first and native macOS layouts out of v1 scope. This repository therefore does not install under `/etc` and does not claim the Mac mini already has the gate.

Konrad must set `HERMES_MANAGED_DIR`. If the customer can set the variable, the gate is gone.

The managed `.env` is locally readable by everyone. Secrets and the customer’s credentials do not belong there. They stay in the customer profile on their machine. The package contains no `.env` and no `auth.json`.

The industry template for Hermes is a profile distribution: `distribution.yaml`, `SOUL.md`, `config.yaml`. Versioning via Git tag or commit is documented. `hermes profile update` leaves reminders, sessions, and credentials untouched. Newly delivered cron jobs arrive paused. Whether we use cron is open.

## Approval for computer and mail

Computer Use is documented for macOS. Accessibility and screen recording must be granted for CuaDriver. After a driver update rights may expire. Click, keyboard, drag, and scroll need the same approval as dangerous commands. Without response the action is rejected unless someone enables unsafe mode. The package does not pin this mode. It sets `approvals.mode: manual`, `cron_mode: deny`, and `unattended_mode: deny` unless the template allows unattended sending. That is our mapping of documented keys.

## Pilot 1: Gmail integration (scope)

Pilot 1 uses Google’s official Gmail MCP server (`https://gmailmcp.googleapis.com/mcp/v1`, registered in Hermes as MCP server `gmail`). This service is a Developer Preview. Participation must be requested and granted by Google; without approval the integration is not usable.

There is no commitment that the service stays available, stable, or unchanged. Google may change or end preview terms, scope, or operation at any time.

The official Gmail MCP has no send tool in the tool list relevant to Pilot 1; that fits the pilot goal of drafts and reads without automatic sending. Credentials and OAuth are created on the customer’s Mac, not in the appliance package. Sending via other paths still needs approvals (gate, Hermes `approvals.mode`) if offered at all.

Label tools (`label_message`, `label_thread`, `unlabel_message`, `unlabel_thread`) may set or remove only label IDs listed in `plugins.entries.aaf-gate.settings.label_ids` in the merged Hermes config. System labels (INBOX, SPAM, TRASH, UNREAD, CATEGORY_*, and comparable reserved IDs) are never allowed. Without valid `label_ids` the gate rejects all label calls with `action_class_unknown`. Whether Hermes delivers YAML lists from `ctx.get_config` as Python lists must be confirmed on device.

### label_ids in managed scope

With gate service, `build_appliance_package` optionally writes `label_ids` (sorted allowlist of custom label names) to `plugins.entries.aaf-gate.settings.label_ids` in `managed/config.yaml`. Without a package parameter that is `[]` and blocks label calls until a package is built with a list. `label_ids` without gate service is rejected with `LABEL_IDS_WITHOUT_GATE`; invalid values with `LABEL_IDS_INVALID`. Whether Hermes merges lists in managed scope with user lists is not described in Hermes docs (open point in package, ASSUMPTIONS.md). `verify-device` reports `gate_settings_in_profile` if user or profile `config.yaml` contains `aaf-gate`, `socket_path`, or `label_ids` outside the `    - aaf-gate` enabled line; all aaf-gate settings come only from managed scope.

## Operations

FileVault, updates, remote access for Konrad only and only with customer consent, backup, restore, and rebuild are appliance requirements. They are not documented as Hermes features. Hermes and macOS updates can change behavior. They are not applied automatically.

Stop is a requirement: the customer must halt assistance and continue the task by hand. Stop is implemented in core and locally tested (`gate-stop`, `gate-resume`, `gate-status`). It is not accepted on device.

## Delivery and ownership

Setup as far as possible remotely after the Mac is at the customer. The first admin step may need a short local part. Whether the customer buys or rents hardware is an open business decision. No pricing.

## What the code does today

Core builds from the released version a directory with distribution, managed config, assumptions, rollback note, and a dry-run script. Dry-run does not run `sudo` and does not write under `/etc`. That is implemented and locally tested. It is not an installation.

## Manual acceptance on Mac mini

This list has not been executed. Running on device is Konrad’s step.

Pilot 1 is without sending and without Computer Use: Hermes gets no accessibility or screen-recording grants; no send tool is enabled.

- Admin only Konrad; customer without admin and without `sudo`.
- FileVault on. Recovery key only with the parties involved.
- `hermes doctor` shows the managed directory set by Konrad.
- Standard user cannot change the managed file.
- `hermes config set` on `approvals.mode` is rejected.
- Customer cannot set `HERMES_MANAGED_DIR`.
- Steps from the package `MANUAL-MAC-CHECKS.md` in this order: `verify-hermes-dump`, device probes G1–G3, stop probe S1–S9 (customer can stop; resume only authorized role), `gate-audit` with `--anchor`, backup probe B1–B3, finish with `verify-device`.
- Record evidence in package `ACCEPTANCE-RECORD.md`; copy off device.
- Core database backup and restore: backup probe B1–B3 from `MANUAL-MAC-CHECKS.md` (`gate-backup`, `gate-restore`; locally tested, open on device).
- Hermes update does not run on its own.

## Rebuild from last release

This guide has not been executed on device. Running it is Konrad’s step.

1. Set up macOS again; accounts as in manual acceptance: admin only Konrad, customer without admin, FileVault on.
2. Install Hermes at the pinned version. Update does not run on its own.
3. Apply the kept package copy of the last active release. `verify-install` with `package_hash` from the last `ACCEPTANCE-RECORD.md` reports ok true. There is no CLI command yet to rebuild the package.
4. Startup step 1 (`gate-init`) is skipped because `gate-init` rejects an existing database with `db_exists`. Instead `gate-restore --backup <last backup> --db <database path per package> --expect-anchor <tenant_id>=<anchor from manifest>`. Abort on `anchor_mismatch` or `chain_invalid`. Output must include `tenant_id` from `ACCEPTANCE-RECORD.md`; `gate-restore` does not check that itself.
5. `gate-status --tenant <tenant_id>`. If a stop active before failure is missing from backup, set `gate-stop` again.
6. Startup chain from step 2. Customer sets up Gmail access on device again; it is neither in package nor backup.
7. Full manual acceptance with new `ACCEPTANCE-RECORD.md`. `verify-device` with `--audit-anchor <anchor from step 4>`.

Limit: events after the last backup are lost. Backup cadence is open.
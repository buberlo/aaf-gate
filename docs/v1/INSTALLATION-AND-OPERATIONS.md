# Installation and operations 1.0.0rc1

This guide has **not yet executed on device**. Running it on hardware is Konrad’s step.

## Prerequisites

Python 3 with standard library only for the core (`cd core/`). Hermes v0.23.1 on the Mac mini. Admin access only for Konrad; customer without admin rights; FileVault on. Check core version with `python3 -m framework.cli version`.

## Build package

The appliance package is built via the Python API `build_appliance_package` in `core/framework/appliance.py`; there is no CLI command for it. Keep a package copy off the device; record `package_hash`. `core_version` is listed in `ACCEPTANCE-RECORD.md` and is part of `package_hash`.

## Initial installation

Startup order is not defined here; see the package’s `MANUAL-MAC-CHECKS.md` and [docs/v0.4/06-APPLIANCE-TRACK-A.md](../v0.4/06-APPLIANCE-TRACK-A.md). Create the core database with `gate-init`. Verify installation with `verify-install`. Final check with `verify-device`. Record results in `ACCEPTANCE-RECORD.md`.

## Operations

The customer stops with `gate-stop`. Resume only for an authorized role via `gate-resume`. Read state with `gate-status`. Verify the audit chain with `gate-audit`. Call-limit assumptions per class are in [docs/v0.5/171-call-limits.md](../v0.5/171-call-limits.md). Hermes or macOS updates without renewed acceptance are not planned.

## Backup and restore

Backup with `gate-backup`, restore with `gate-restore` and `--expect-anchor`. Abort on `anchor_mismatch` or `chain_invalid`. Rebuild from the last release: [docs/v0.4/06-APPLIANCE-TRACK-A.md](../v0.4/06-APPLIANCE-TRACK-A.md), section Rebuild from last release. Backup cadence is open.

## Limits

See [docs/v1/STATUS.md](STATUS.md).
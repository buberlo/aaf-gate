# Status v1 candidate 1.0.0rc1

This release is **not released for production**.

## Implemented and locally tested

- Kill switch (41575e0, 17831ee, 5484531).
- Call-limit gate per time window and class (58dccbe and this commit). Start values are an **Assumption** in the Pilot 1 policy: window 3600 s, read 160, draft 40, internal_write 80. After a **pilot week**, **reset** from recorded `gate_calls`.
- Device acceptance pack with probe script, `verify-hermes-probe`, and `ACCEPTANCE-RECORD.md` (8c8da50, c30faf0).
- Backup and restore with audit hash-chain verification (813fe28, 575bef9).
- Version and release documentation (79dd30b and this commit).

Locally tested means: standard-library unit tests on a development machine, not on the appliance.

## Open on device

Full manual acceptance on the Mac mini (see [docs/v0.4/06-APPLIANCE-TRACK-A.md](../v0.4/06-APPLIANCE-TRACK-A.md), section Manual acceptance), device probes **G1**–G3 (including how Hermes merges lists in the managed scope, whether `ctx.get_config` delivers YAML lists as lists, and what status `post_tool_call` returns), stop probes S1–**S9**, backup probes B1–**B3**, **`verify-device`**. The worker snapshot path with managed overlay is not covered by any probe.

## Open with Google

**Gmail** MCP is a **Developer Preview**; Google has not granted access for Pilot 1. Without it there is no Gmail pilot.

## Open legally and organizationally before real customer data

Processing agreement or contract with the customer, purpose-limited model processing at the model provider in use, data minimization, deletion, objection and recovery process, responsibilities, and remote access only with the customer’s consent. This document makes no compliance commitment. EU hosting or a no-training pledge alone does not make a product GDPR-aligned.

## Not included

Sending mail, Computer Use, a separate agent loop, channels, true tenant isolation beyond one device, CLI command to build the package (the package is produced via the Python API `build_appliance_package`).
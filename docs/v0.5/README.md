# v0.5 — implemented code steps (index)

This directory extends v0.4 with **evidence and partial specifications** for code already implemented. v0.4 remains product direction and is not rewritten here. As of 22.11.2026.

## Placement

- **locally tested:** `cd core && python3 -m unittest discover -s tests -q` → currently `Ran 719 tests`, `OK`
- **tested on device:** no (Mac mini appliance not yet accepted)
- **production:** no

## Topic blocks (commits, excerpt)

| Area | Example commits / content |
|---|---|
| K1 & release | Release numbering, field sovereignty, principals, policy core, audit write, idempotency |
| Device mode & install | device-mode, verify-install, installation check, package hashes (files 96–98, 100–101) |
| Gate core | Gate decision, GateKeeper, Hermes adapter, Unix socket, gate-serve |
| Hermes plugin | Package `adapters/hermes/aaf-gate`, digest, verify-plugin, ledger/observations, socket from plugin settings (152a), managed-scope socket (152b) |
| Appliance track A | Package build, launchd plist, gate-init, checklists (gate, pilot, approvals, foreign hooks), pin-audit, relay/telemetry, Hermes commit, verify-device (install, managed, dump, launchd, plugin, profile, active_profile) |
| Pin & hooks | verify-hooks, pin-audit, raft-platform/disabled, profile blocklists |
| Kill switch (V1-1) | Core 41575e0, CLI 17831ee, device checklist and [168-stop-switch.md](168-stop-switch.md) |
| Call limits (V1-2) | Core 58dccbe, Pilot 1 policy and [171-call-limits.md](171-call-limits.md), this commit |

Individual files here (e.g. `96-device-mode.md`, `97-package-device-mode.md`) describe increments in draft wording; what actually runs remains the code and [EVIDENCE-I1.md](../EVIDENCE-I1.md).

## Not in this tree

- Graphical operator console: not in this tree
- Agent loop, provider wiring, production Hermes operation on customer Mac

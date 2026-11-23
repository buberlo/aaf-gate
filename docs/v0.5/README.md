# v0.5 — historical increment notes

## Status guide

- **Historical increment notes.** These pages record draft specifications, implementation, and open items at each step. References to PR branches describe where the work was tested at the time. Later increments may resolve items marked open.
- **Current release status.** See [v1 STATUS](../v1/STATUS.md) for the 1.0.0rc1 candidate and its verification limits. Local tests ran on a development machine. Mac mini acceptance remains open, and the candidate has no production release or compliance commitment.
- **Implementation and evidence.** Use the code to check current behavior and [EVIDENCE-I1.md](../EVIDENCE-I1.md) for the local I1 prototype evidence.

The 22.11.2026 snapshot recorded 719 passing tests from `cd core && python3 -m unittest discover -s tests -q`.

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

## Not in this tree

- Graphical operator console: not in this tree
- Agent loop, provider wiring, production Hermes operation on customer Mac

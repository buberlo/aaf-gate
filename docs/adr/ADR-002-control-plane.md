# ADR-002 · Two tracks: appliance and own core

As of 12.11.2026, 17:01 Europe/Berlin. Replaces the earlier same-day version that treated Hermes only as a later channel. Platform research remains data material. Licenses were not re-checked here.

## Decision

Track A has priority for the start. A customer receives a Mac mini set up by Konrad with Hermes Agent and a locked profile. Computer control and a mail integration are the goal insofar as verified on device. Hermes is the starting platform for that.

Track B runs alongside. The in-house core remains the source for industry template, customer values, gate, release, hash, and rollback. The same release produces the package for track A. An own agent loop with the same capabilities is the later replacement, not today’s runtime. Hermes becomes replaceable then.

None of the reviewed third-party platforms with resale limits becomes the base. That rests on research, not a new license review.

## Documented Hermes basis

Checked 12.11.2026 against public docs, not an installed program:

- Managed scope under `/etc/hermes`, root-owned, leaf-wise stronger than user config. `hermes config set` rejects a pinned key. `hermes doctor` shows the directory.
- Whoever can set `HERMES_MANAGED_DIR` lifts the gate. Docs require the administrator to set this variable.
- A broken managed file does not block startup.
- Managed `.env` is locally readable by all. It is unsuitable for secrets.
- Profile distribution is a Git repo with `distribution.yaml`, `SOUL.md`, and `config.yaml`. Versions are tags, branches, or commit SHAs. `auth.json` and `.env` stay with the user.
- `approvals.mode` knows `manual`, `smart`, and `off`. `cron_mode` and `unattended_mode` know `deny` and `approve`.
- Computer Use is described for macOS. Accessibility and screen recording apply to CuaDriver. Destructive control uses the same approval as dangerous commands.
- The Google Workspace skill describes Gmail. Sending should happen only after confirmation.

## Open or assumption only

- Native managed-scope locations for macOS are explicitly out of v1 scope. Whether `/etc/hermes` on a Mac mini has the same effect must be checked on device.
- Whether `approvals.mode` necessarily covers the Gmail skill is not documented.
- Hermes does not lock `SOUL.md`. Text there is a prompt.
- FileVault, user accounts, remote maintenance, and backup are macOS, not Hermes.
- Mapping “no unattended sending” to `approvals.mode: manual` is our use of documented keys, not a Hermes function of that name.

## Consequence

K1 remains the template model. Core additionally produces an appliance package and dry-run without system changes. A real Mac mini is not accepted with that alone.
# I1 evidence

Note on public state (22.11.2026): Historical draft packages `docs/v0.1`–`docs/v0.3` and the marketing site under `web/` are not in this public repository. The text below is the original local I1 evidence and stays unchanged as a record.


As of 08.11.2026 Europe/Berlin. This evidence is separate from drafts under `v0.1/` and `v0.2/`. Their status wording stays unchanged: there I1 is not implemented and tests are planned. Here only what ran in this repository is recorded.

## Placement

Implemented: a local single-process `material-prep-1` with SQLite, CLI, and decisions described in ADR-001.

Tested: the fifteen mandatory tests T01–T15, twice in a row successfully.

Production: no. No customer access, no scheduler, no model, no network or send adapter, no HTTP interface.

T12 documents only logical rejection in one shared database file. The same file holds both demo principals’ content. That is not customer isolation.

Not executed: T16–T19, S01–S04, and document check DOC as software test. I2, I3, and I4 have not started.

## Command

```bash
cd core && python3 -m unittest discover -s tests -v
cd core && python3 -m framework.cli
```

## Observed result

First run: `Ran 15 tests`, `OK`, about 0.58 s.
Second run immediately after: `Ran 15 tests`, `OK`, about 0.59 s.

The CLI demo run ends in state `REVIEW`, shows conflict of quantities 20 and 24, open question `delivery_date`, no evidenced facts, and `network: disabled`. The CLI does not approve by itself.

| Test | Observation in this run |
|---|---|
| T01 | Hash, locator, and synthetic preserved; change to same revision rejected; new version only as new SourceRevision |
| T02 | Missing locator, unknown revision, tampered hash, and foreign source not confirmed as fact; no business mutation; delivery date stays open |
| T03 | Conflict 20/24 with both evidences, no adopted quantity, no invented date; CLI shows the same |
| T04 | D3 does not change rule hash, state, or roles and calls no tool |
| T05 | Commit from CAPTURED, unknown command, and resume without block or resolution leave business state unchanged; rejection audited where storage allows |
| T06 | Role without review right writes no approval; foreign principal does not see approval and receipt |
| T07 | Divergent payload hash, payload revision, rule hash, rule version, or manifest → `APPROVAL_INVALID` without storage; revoked approval likewise |
| T08 | Same key and hash: one commit, one result id, also after lost response and with two concurrent calls |
| T09 | Same key, different hash: `IDEMPOTENCY_CONFLICT`, old result remains |
| T10 | Two keys, same starting version: one success, one `STALE_REVISION` |
| T11 | Failure after state, after audit, before commit restores prior state; after commit retry delivers receipt |
| T12 | Foreign run, receipt, approval, export, search, and cache access return no B content. Raw file still contains it |
| T13 | Feedback leaves rules and decisions unchanged; a new rule version applies only to a new run |
| T14 | Same snapshots yield same claim and guard decisions; resume returns to REVIEW and does not skip approval |
| T15 | Missing database, missing audit, missing actor, and forbidden network action end without success and without socket |

## Limits of this evidence

Tests run on local Python 3.12 against a temporary SQLite file. They are not business customer acceptance, not load testing, and not evidence for host separation, backups, restore, or deletion.

## Configuration model K1

As of 12.11.2026 Europe/Berlin. Implemented and locally tested. Not released for production.

A synthetic industry template `auto-service-appointment-intake` and synthetic customer configuration go through registration, release with hash, rejection of a locked field, change of an editable field, and rollback. The older release stays unchanged. A foreign principal does not see the version. That is not a separate customer environment.

Command: `cd core && python3 -m unittest discover -s tests -v`. Result 12.11.2026, first K1 run: `Ran 21 tests`, `OK`. Appliance dry-run is in the same command and described below only when the run includes it.

Track A builds a Hermes package from this release. It writes nothing on the system. Managed scope on macOS is not documented as a native layout and is marked open in the package. The combined test run after profiles is `Ran 28 tests`, `OK`: T01–T15, seven configuration tests including array, enum, and min/max, four package tests, and two profile tests.

## Homepage clarity pass

As of 08.11.2026 Europe/Berlin. Only the public page, content check, and this paragraph. `docs/v0.1`, `docs/v0.2`, `docs/v0.3`, and prototype code are unchanged.

The page explains in plain language what is eased, shows three invented examples with situation, help, outcome, and remaining responsibility, and describes the path from first message to handoff. Agentic AI appears lower on the page and is explained in one sentence. A text check secures the hero. It is not a user study.

Check for this pass: typecheck, build, adapted content check, and browser at desktop 1440×900 and mobile 390×844, including open question, axe without serious or critical findings, no forms, and HTTP 404 for internal documents. I1 regression with unchanged `core/`: `Ran 15 tests`, `OK`, about 0.52 s. No deployment.

## Homepage and v0.3, separate from prototype

As of 08.11.2026 Europe/Berlin. Prototype code unchanged in this increment. v0.1 and v0.2 not rewritten. New: `docs/v0.3/` and sharpened page.

The page describes recurring digitally reachable information flows for companies of different industries and sizes. It does not set pricing strategy and marks the internal demonstration as invented. Economic viability is not claimed.

Clarity check: hero text check, not a user study. Hero names the offer, cross-industry audience, pre-structuring requests, and start via flow check. Measurement note sits at the demonstration, not in the hero.

Regression of unchanged I1 tests on 08.11.2026: `python3 -m unittest discover -s core/tests -q` from repository, `Ran 15 tests`, `OK`, about 0.58 s. Homepage after: typecheck, build, content check including new doc links, browser desktop 1440×900 and mobile 390×844, axe without serious or critical findings, HTTP 404 for internal documents. No deployment.

## Full core run and doc update

As of 15.11.2026 Europe/Berlin. README, `docs/v0.5/README.md`, and homepage state (`web/stand.html`) aligned to code on branch `cursor/homepage-i1-v01-a1f9`. Historical sections above unchanged.

Command: `cd core && python3 -m unittest discover -s tests -q`

Observed result: `Ran 467 tests`, `OK`, about 11.4 s.

Tested on device: no. Production: no. No deployment.
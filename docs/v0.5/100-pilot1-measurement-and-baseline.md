# Pilot 1: measurement and pilot-baseline

November 2026. Supplement to acceptance lines in the appliance package (Pilot 1 checklist, points 4 and 5) and the CLI command `pilot-baseline`.

## gate-audit --summary

At the end of the pilot phase, produce a summary with `gate-audit --summary`. It includes among others `summary.total`, `summary.by_class`, `summary.by_tool`, and since gate observation counting `summary.observations` with `gate_mismatch`, `gate_unseen`, and `other`.

## pilot-baseline

The command `python3 -m framework.cli pilot-baseline --summary <file> --timesheet <file>` computes from the confirmed summary and a timesheet file (`schema_version` 1 or 2, unit `minutes`) median and manual minutes per tool class. Optional `--min-samples` (default 5).

Output under `baseline` includes among others `manual_minutes_total`, `net_minutes_total` (for schema_version 2 with enough review samples), and `observations` and `observations_clean` when the summary contains `summary.observations`.

## Prerequisite for external minute figures

Minute numbers from `pilot-baseline` (per class, `manual_minutes_total`, `net_minutes_total`) may be used or given to customers only when `baseline.observations_clean` is exactly `true`. If `observations_clean` is `false` or `null`, no minute figures go outward; clarify the summary and audit first (e.g. with `gate-audit` without `--summary` as in checklist point 4).

## --require-clean

`--require-clean` exits `pilot-baseline` with code 1 and `{"error": "observations_not_clean", "ok": false}` when `observations_clean` is not `true` (including missing or old summaries without `observations`). Computed fields are not reported as successful acceptance in that case.

## Approved executions

When the summary contains `summary.approvals_by_class`, `pilot-baseline` adds per class `approved_executed`, `manual_minutes_approved`, and `net_minutes_approved`, and at top level `approved_incomplete`, `manual_minutes_approved_total`, and `net_minutes_approved_total`. These approved minutes are separate from `manual_minutes_total` and `net_minutes_total`; they are an upper bound because `approval_granted` also includes failed executions (`post_tool_call` with `status` `error`). Approved fields are internal only and are not given to customers. The `observations_clean` rule applies in addition.

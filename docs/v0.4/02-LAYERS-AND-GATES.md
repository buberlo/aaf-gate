# Layers, locks, and roles · v0.4

As of 12.11.2026 Europe/Berlin. Draft, except where evidence names a test.

## Three layers

1. Core. It validates the release, rejects forbidden changes, writes audit, and knows no industry specifics.
2. Industry template. It names fields for a task type and, per field, whether it is locked or customer-editable. The template is versioned and is not silently overwritten.
3. Customer configuration. It fills fields for a synthetic or later real customer. Only a released version is effective, not a draft.

## Who may change what

| Role | May |
|---|---|
| Author, Konrad | Register template, put any field change into a new release, roll back |
| Customer | Change only fields with `customer_editable`. That creates a new release. Locked fields are rejected. |
| No role | Nothing |

A customer-role change to a locked field does not change the active release. It is logged as a rejection. The attempted text is not in the error message.

In the first increment there is no queue for locked change requests. They are rejected. Later review by Konrad is a separate increment.

## What does not apply

A role or principal id in the same file is not a separate customer environment. ADR-002 states the same.
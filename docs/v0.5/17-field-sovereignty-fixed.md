# Field sovereignty fixed (draft v0.5)

- Templates may declare fields with `access: fixed` and required `value`. The value flows into the document and `release_hash` on release.
- Profile and configuration values must not set `fixed` fields; the author cannot override them via release or change. Only a new template version changes the value.
- `allow_*` security fields in repo templates auto service `1.1.0`, b2b-lead-research, and ecommerce-product-sales (from `0.2.0`, list rules from `0.3.0`) are `fixed: false`. Core checks policy floor for all `allow_*` values in the resolved document.
- Package build relies on the resolved release, not a separate floor check.

Status: implemented and tested only on PR branch, not released for production.

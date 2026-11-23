# Release binding (draft v0.5, step 28a)

The release document contains `template_body_hash`, `core_schema_version`, and `core_floor_hash` beside values and metadata. Same-named templates with different content or bounds yield different `release_hash`. Rollback activates a stored document unchanged including binding fields.

This step left open the work to bind `release_hash` at `create_run`, check for `STALE_RELEASE` on approve/close after a new release, and include the rule-set hash in the release. See [step 28b](28b-run-release-binding.md) for the subsequent run binding and stale-release checks.

Status: implemented and tested on PR branch, not released for production.

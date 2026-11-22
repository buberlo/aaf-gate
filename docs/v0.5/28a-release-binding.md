# Release binding (draft v0.5, step 28a)

The release document contains `template_body_hash`, `core_schema_version`, and `core_floor_hash` beside values and metadata. Same-named templates with different content or bounds yield different `release_hash`. Rollback activates a stored document unchanged including binding fields.

Open (28b): run binds `release_hash` at `create_run`, `STALE_RELEASE` on approve/close after new release, rule-set hash in release.

Status: implemented and tested on PR branch, not released for production.

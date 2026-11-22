# Bind run to configuration release (draft v0.5, step 28b)

`create_run` may pass optional `config_id`; the run stores `config_id` and active `release_hash` at start time. `approve` and `commit` compare via `releases.active_release_hash` with bound hash and reject with `STALE_RELEASE` when another release is active. Rollback to content-equal release (same hash) is not stale.

Open: bind `rules_hash`/`policy_hash` into approval (30), rule-set scope (88).

Status: implemented and tested on PR branch, not released for production.

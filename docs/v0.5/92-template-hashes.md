# Expected template hashes (draft v0.5, step 92)

`templates/HASHES.json` lists per `template_id@template_version` the released `body_hash` after `validate_template` and `hash_value`. Tests (`check_template`) and optional `build_appliance_package(..., expected=...)` compare registered templates or release documents to this list. Mismatches yield `TEMPLATE_HASH_MISMATCH`, unknown versions `TEMPLATE_UNLISTED`.

Every content template change needs a new version number and updated hash entry. The file is not signed; it guards against mistakes and repo drift, not a write-capable attacker on the repository (signature: planned point 23).

Status: implemented and tested on PR branch, not released for production.

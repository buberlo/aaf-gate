# Release numbering (draft v0.5)
- `release_no` per config strictly increasing: MAX(release_no)+1, independent of active pointer.
- Rollback only moves the active pointer and does not create a release.
- Every release document carries `base_release_no` (active release when changing; null on first release); the field is part of `release_hash`.
- Planned, not implemented: `expected_release_hash` with error `STALE_BASE`; mapping database errors to `INTERNAL_ERROR` with audit.
Status: implemented and tested only on PR branch, not released for production.

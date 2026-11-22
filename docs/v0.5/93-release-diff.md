# Release diff for reviewers (draft v0.5, step 93)

The read operation `release_diff` compares the configuration release bound at run start with the currently active release. After `STALE_RELEASE`, a reviewer with operator or reviewer role sees which fields changed. Full values appear only for `customer_editable` fields; locked or fixed fields only as name in `changed`. The response is not cached because the active release can change continuously.

Open: UI presentation, mandatory acknowledgment before another `approve` (undecided).

Status: implemented and tested on PR branch, not released for production.

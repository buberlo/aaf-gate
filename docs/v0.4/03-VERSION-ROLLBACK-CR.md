# Version, release, rollback, change · v0.4

As of 12.11.2026 Europe/Berlin.

## Release

A release is the resolved configuration: template, version, values, which fields are locked. It gets a monotonic number and a SHA-256 over canonical form from I1 (`canon-1`). Same number and same values yield the same hash. The row is not updated afterward.

The author releases the first version. That is the active state.

## Editable fields

If the customer changes an editable field, a new release is created. The previous one remains stored. That is the change implemented in the first increment, not silent overwrite.

## Rollback

The author points the active pointer at an older release. Younger rows remain. The older release’s hash applies again. A customer cannot roll back.

## Request beyond that

If the customer wants something on a locked field, in this state that is not an order but a rejection. A later flow may turn it into a request Konrad reviews and releases only as a new release. Until then: locked stays locked.

Every outward action the template does not allow stays impermissible. The synthetic workshop template sets `allow_unattended_send` to false and locks the field.
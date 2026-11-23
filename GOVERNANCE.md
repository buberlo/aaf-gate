# Governance

This document describes how this community-owned project makes decisions and how maintainers are added. The repository owner (Konrad Kern) published aaf-gate under Apache-2.0 as a foundation and is not actively maintaining day-to-day work.

## Principles

- **Lazy consensus**: Proposals that receive no substantive objection within a reasonable review window may proceed. Silence after review is not automatic approval if tests fail or scope is unclear.
- **Tests and honesty**: Changes must keep the documented test workflow green. Do not add false claims about production readiness, compliance, or adoption.
- **Build on the idea**: Forks, commercial use, and substantial rewrites are welcome under the license.

## Roles

### Contributors

Anyone may open issues, discussions, and pull requests without special permission.

### Maintainers

Maintainers can merge pull requests, triage issues, manage releases (if the group chooses), and help enforce the [Code of Conduct](CODE_OF_CONDUCT.md). Maintainer access is granted by the repository owner when a contributor has demonstrated sustained, high-quality work.

### Repository owner

The owner holds admin access on the personal account repository. The owner is an **emergency brake** only: reversing clearly harmful changes, addressing security or conduct escalations, or restoring repository access if maintainers are unavailable. The owner is not expected to lead feature development or routine review.

## Becoming a maintainer

Typical path (flexible case by case):

1. Several **merged, non-trivial** pull requests (fixes, tests, adapters, documentation with substance—not typo-only edits).
2. Consistent respect for project conventions, tests, and the code of conduct.
3. A **request via GitHub issue**, stating why you want maintainer access and pointing at your merged work.
4. **Lazy consensus** among existing maintainers (or the owner if no maintainers yet): no unresolved objections after a reasonable period (often about one to two weeks).
5. The **owner grants** write or maintainer access on GitHub.

The owner may decline or defer requests that lack demonstrated contribution or community support.

## Decision making

- **Routine code changes**: Reviewed in pull requests; merge when tests pass and lazy consensus is reached among reviewers/maintainers.
- **Governance or policy changes**: Substantive edits to this file or to contributor expectations should be discussed in an issue or discussion first, then merged via pull request.
- **Disputes**: Discuss in the issue or pull request thread. If unresolved, maintainers decide; the owner steps in only as emergency brake.

## Moving to a neutral GitHub organization

When a stable **maintainer group** exists and agrees, the project may move to a neutral GitHub organization (new org or an existing community org). Suggested steps:

1. Open an issue outlining the proposal, target org name, and initial admin/maintainer list.
2. Achieve lazy consensus among active maintainers and the owner.
3. Transfer or mirror the repository per GitHub’s transfer process, update README and contributor docs, and redirect links as needed.

Until that happens, the canonical home remains `github.com/buberlo/aaf-gate`.

## Security and conduct

- Security reports: see [SECURITY.md](SECURITY.md).
- Conduct: see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
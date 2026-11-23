# Contributing to aaf-gate

Thank you for your interest in this project. All contributions must be in English.

## What we expect

- Keep the test suite green before you open or update a pull request.
- Match existing code style and conventions in the area you change.
- Describe what changed and why in the pull request body.
- Do not add compliance claims, production-readiness claims, or invented metrics unless they are backed by facts in this repository.

## Development setup

Requirements:

- Python 3 (same major version you use to run the tests locally)
- No third-party Python packages: `core/` uses the standard library only

Clone the repository and work from the repo root.

## Running tests

From the repository root, run the suite exactly as documented in [README.md](README.md#quickstart):

```bash
cd core
python3 -m unittest discover -s tests -q
```

A successful run ends with `OK` and reports the total number of tests executed (for example `Ran 719 tests`).

Optional checks while developing:

```bash
cd core
python3 -m framework.cli version
```

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make focused changes; avoid unrelated edits.
3. Run the full test suite and confirm it passes.
4. Open a pull request against `main` and fill out the pull request template.
5. Respond to review feedback. Maintainers follow lazy consensus as described in [GOVERNANCE.md](GOVERNANCE.md).

## Questions

Use [GitHub Discussions](https://github.com/buberlo/aaf-gate/discussions) for questions, ideas, and design conversation. Use issues for bugs and concrete feature or adapter proposals.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Please read it before participating.
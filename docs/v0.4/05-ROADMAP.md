# Roadmap · v0.4

As of 12.11.2026 Europe/Berlin. Small increments. Acceptance means: the named test run is green and evidence separates draft, implemented, and tested. None of this is production.

| Increment | Content | Acceptance |
|---|---|---|
| K1 | Schema, validation, locks, release with hash, rollback, audit, synthetic workshop template | tests green, T01–T15 green |
| A1, this track | Same release produces a Hermes package and dry-run without system changes | package test green, no Mac acceptance |
| A2 | Manual acceptance on a real Mac mini | list in appliance doc, open today |
| K2 | Kill switch and simple budget | test refuses action and writes audit, no network |
| K3 | Request for locked field waits until author releases new version | customer alone does not change field |
| B1 | Own agent loop with same capabilities, Hermes replaceable later | same release and lock tests without sending |

Track A has priority for the start. Track B remains the shared template source.
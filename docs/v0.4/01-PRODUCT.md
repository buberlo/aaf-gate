# Product · customizable assistant · v0.4

As of 12.11.2026 Europe/Berlin. Product direction draft. Nothing here is released for production. There are no customers, no prices, and no measured outcomes.

Konrad sells an assistant for recurring tasks. The task comes from an industry template. Konrad adapts the template for a customer, releases a version, and can roll back to an earlier one. After that the customer changes only fields explicitly marked editable for them in that release.

Two tracks share the same template. Track A starts on a Mac mini set up by Konrad with Hermes. Track B is the in-house core that should carry the same capabilities without Hermes later. Appliance details are in [06-APPLIANCE-TRACK-A.md](06-APPLIANCE-TRACK-A.md).

AI is used only when it helps for the task. If a fixed rule suffices, the rule stays. The assistant does not send anything on its own.

## Status

| Part | State |
|---|---|
| This description | draft |
| Configuration model in core | implemented and locally tested; see evidence |
| Hermes appliance package | built locally and dry-run checked, not installed on a Mac |
| Hermes on Mac mini, Computer Use, Gmail | Track A target, not verified on device |
| Own agent loop | not implemented |
| Real customer separation | not implemented and not claimed |
| Production operation | no |
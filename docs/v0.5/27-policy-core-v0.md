# Policy core v0 (draft v0.5, step 27)

Floor lives in `core/policy/floor.json` as single source; `FLOOR_FORBIDDEN` is used by rules validation and service. Rule sets may only tighten `forbidden_actions` (superset of floor); at runtime order is rights → policy → replay → handler. Loosening via `allow_operations` is forbidden (`policy_loosen`). `policy_hash` prepares releases (step 28).

Open: policy on read path, `policy.json` per template under `templates/<id>`, key expiry (68), budget (48).

Status: implemented and tested on PR branch, not released for production.

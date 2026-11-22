# b2b-lead-research-client 0.1.0

Status: draft.

Purpose: one client under a lead-research operator. Each client is its own profile with parent template `b2b-lead-research-client`.

Tasks: capture offer, industries, size, region, roles, exclusions, tone, and goal. Research stays off while `approved_for_live_research` is false.

Autonomy: configuration only. No live search without approval true.

Editable: descriptive fields, character limit up to 300, activity window in days. Tone only `Sie` or `Du`. Goal only `Austausch`, `Produkt`, or `Webinar`.

Locked: `approved_for_live_research` false.

Release: author only. Rollback author only.

Limits: core checks these fields. It does not search contacts or count real messages.

Open: whether the operator may set research approval later.

# lead-scout-example

Status: draft. Synthetic example profile, not a real customer.

Purpose: customer profile for someone who finds contacts for clients and needs copy-paste texts. Industry lives in template `b2b-lead-research`. This profile only sets their way of working.

Confirmed and used here: synthetic example name, wish for up to 10 contacts per client per day, messages up to 300 characters, no platform login by the system, follow-up only after acceptance, status only after human confirmation.

Not confirmed and marked `SYN` or as suggestion: Friday as briefing day, Telegram as channel, local storage, 200 character default, clients A–D.

Tasks: drafts. No send, no login, no live research while approval stays false.

Autonomy: drafts. No send, no login, no live research while `approved_for_live_research` stays false.

Editable: name, briefing day, storage, daily count up to 10, default character limit up to 300, and in child profiles target description within enums.

Release: new version author role only. Customer role fails on locked fields. Tests verify that.

Child profiles in `client/`: synthetic placeholders A–D.

Limits: core enforces fields. It does not count real messages, detect duplicates, or switch a browser block in Hermes. Those runtime rules stay open.

Open: retention, provider, whether research approval may be set later, and whether separate user accounts per client are needed.

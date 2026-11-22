# auto-service-appointment-intake 1.1.0

Status: draft. Not set up or tested on a device.

Purpose: appointment intake and follow-up questions for a workshop as an example, not the only industry.

Scope: hours, pickup or customer number, vehicle model, workshop location.

Autonomy: follow-up questions may be prepared. Sending stays locked (`allow_unattended_send` false and locked).

Customer-editable: name, opening hours, notice text. Locked: fixed questions and sending.

Release: a new version only via core. Rollback author role only.

Connectors: none. Computer Use: not configured. Telegram only as a field value if set later.

Boundary: core enforces configuration, not a live mail run.

Open: whether this template later needs a Hermes distribution with more files.

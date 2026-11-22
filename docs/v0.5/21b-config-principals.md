# ConfigPlane und Principals (Entwurf v0.5, Schritt 21b)

| Operation | Rolle |
|-----------|--------|
| register_template, release, rollback | author |
| apply_changes, active, release_record | author oder customer (apply: author vor customer) |

- Agent-Principals dürfen keine schreibende Config-Operation ausführen; Lesen mit passender Bindung bleibt möglich.
- Jede abgelehnte schreibende Operation wird auditiert (principal_kind, acting_role, error_code FORBIDDEN).
- Veraltetes role_bindings-Schema ohne author/customer im CHECK bricht beim Öffnen mit schema_role_bindings_outdated ab, ohne stillen Umbau.
- Offen: Idempotenzschlüssel je Principal (Backlog 67).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
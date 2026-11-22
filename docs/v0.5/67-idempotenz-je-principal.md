# Idempotenz je Principal (Entwurf v0.5, Schritt 67)

Rechteprüfung, dann Lookup über (tenant_id, principal_id, operation, idempotency_key), Request-Hash-Vergleich, bei Treffer Replay mit Audit (`outcome=replayed`), sonst Arbeit und Receipt. Abgelehnte Config-Operationen in `_with_key` werden auditiert (`rejected`), der Schlüssel bleibt frei.

Service-Receipts und `config_idempotency` binden `actor_id` in den Primärschlüssel. Veraltetes Schema bricht mit `schema_idempotency_outdated` ab.

Offen: Schlüsselablauf (Backlog 68).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
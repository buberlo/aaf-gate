# Gemeinsame Audit-Schreibfunktion (Entwurf v0.5, Schritt 54)

Ein Schreibpfad `audit.append` für Service und ConfigPlane. Die Sequenz läuft je Mandant über `audit_heads`; `event_id` ist `hash_value({"sequence", "tenant_id"})`. ConfigPlane-Ereignisse aus `_with_key` tragen `command_id` (Idempotenzschlüssel). Vorbereitung für Hash-Kette (47) und Kettenkopf-Export (38).

Hinweis: Zeilen in einer gemeinsamen Datenbank sind keine Mandantenisolation.

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
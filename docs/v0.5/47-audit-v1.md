# Audit v1 (Entwurf v0.5, Schritt 47)

Hash-Kette je Mandant über prev_hash und event_hash mit GENESIS; Kopf in audit_heads.head_hash; verify prüft Lückenlosigkeit und Hashes. Ablehnungen speichern die versuchte Operation und command_id. Trigger verhindern UPDATE/DELETE über die App-Verbindung, ersetzen keine Dateirechte. Kürzen der Kette am Ende erkennt nur ein exportierter Kettenkopf.

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
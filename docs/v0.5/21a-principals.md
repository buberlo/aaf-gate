# Principals und Rollenbindungen (Entwurf v0.5, Schritt 21a)

- Service-Aufrufer übergeben nur eine principal_id. Tenant, Art (human, agent, service) und Rollen liest der Kern aus dem Speicher.
- Rollen je Operation: operator bereitet vor, reviewer gibt frei, schließt ab oder bestätigt. Fehlt die Rolle, wird die Operation abgelehnt und auditiert.
- Kern-Floor: Ein Agent-Principal kann nie freigeben, zurückgeben, abschließen, überarbeiten, Fakten bestätigen oder Regeln aktivieren, auch nicht mit Bindung.
- Grenze: Die principal_id ist noch Selbstauskunft des Aufrufers. Zur Sicherheitsgrenze wird sie erst, wenn der Kern sie aus dem Transportkanal bestimmt (Backlog 22/41). ConfigPlane folgt in Schritt 21b.

Status: implementiert und getestet nur im PR-Branch, nicht produktiv freigegeben.

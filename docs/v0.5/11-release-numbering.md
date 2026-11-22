# Release-Nummerierung (Entwurf v0.5)
- release_no je Config streng steigend: MAX(release_no)+1, unabhängig vom aktiven Zeiger.
- Rollback bewegt nur den aktiven Zeiger und erzeugt kein Release.
- Jedes Release-Dokument trägt base_release_no (aktives Release beim Ändern, beim ersten Release null); das Feld ist Teil von release_hash.
- Geplant, nicht implementiert: expected_release_hash mit Fehler STALE_BASE; Abbildung von Datenbankfehlern auf INTERNAL_ERROR mit Audit.
Status: implementiert und getestet nur im PR-Branch, nicht produktiv freigegeben.

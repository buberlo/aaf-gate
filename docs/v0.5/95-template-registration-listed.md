# Vorlagen-Registrierung gegen HASHES.json (Entwurf v0.5, Schritt 95)

`ConfigPlane(..., expected=...)` übernimmt die Map aus `templates/HASHES.json`. Ist `expected` gesetzt (Kunden-/Appliance-Modus), prüft `register_template` nach Validierung und Hash-Bildung, aber vor dem Speicherzugriff, ob die Version gelistet ist und der Hash übereinstimmt (`TEMPLATE_UNLISTED`, `TEMPLATE_HASH_MISMATCH`). Ablehnungen werden wie bei `INVALID_CONFIG` auditiert. `expected=None` lässt das bisherige Verhalten für Anbieter-Entwicklung unverändert.

Offen (96): Modus aus gesperrter Gerätekonfiguration erzwingen, nicht nur vom Aufrufer setzen.

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
# Prüfreihenfolge Feldänderungen und activate_rules (Entwurf v0.5, Schritt 24b)

ConfigPlane wertet `changes` in sortierter Feldreihenfolge aus: unbekanntes Feld → INVALID_CONFIG; `fixed` → INVALID_CONFIG; für Kunden alles außer `customer_editable` → LOCKED_FIELD; danach Typ, Grenzen und Floor wie bisher.

`activate_rules` erfordert die Rolle `author` (nicht `reviewer`); Agenten mit `author` bleiben ausgeschlossen. Rechte werden vor jedem Replay neu geprüft — `unbind_role` und erneuter Aufruf mit gleichem Idempotenz-Schlüssel → FORBIDDEN, kein Replay.

Offen: `rule_sets` weiterhin global; Scope je Vorlage/Tenant eigener Schritt. Schlüsselablauf (68).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
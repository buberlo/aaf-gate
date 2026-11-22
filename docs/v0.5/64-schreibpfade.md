# Bekannte Schreibpfade je Tabelle (Entwurf v0.5)

- core/tests/test_architecture.py friert alle Stellen ein, die per SQL-Literal in eine Tabelle schreiben (Tabelle, Modul, innerste Funktion).
- Eine neue oder entfernte Schreibstelle braucht eine bewusste Änderung der Liste. Die Änderung ist im PR-Diff sichtbar.
- Module ohne Datenbankschreibzugriff: appliance, cli, canon, clock, egress, errors, rules.
- Tabellennamen stehen in SQL immer als Literal. Dynamische Spaltenlisten sind erlaubt (runs._cas).
- Bekannter Doppelpfad: audit_events und audit_counter werden von service und configplane geschrieben. Ziel ist eine gemeinsame Audit-Schreibfunktion.
- Grenze: Der Test sieht nur Python-Code in core/framework. Direktes Schreiben in die SQLite-Datei außerhalb der API verhindert er nicht. Das bleibt Aufgabe von Dateirechten und späteren Triggern.

Status: implementiert und getestet nur im PR-Branch, nicht produktiv freigegeben.

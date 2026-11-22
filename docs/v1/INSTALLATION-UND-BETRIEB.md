# Installation und Betrieb 1.0.0rc1

Diese Anleitung ist nicht am Gerät ausgeführt. Ausführen ist Konrads Schritt.

## Voraussetzungen

Python 3 mit nur Standardbibliothek für den Kern (`cd core/`). Hermes v0.23.1 auf dem Mac mini. Admin nur Konrad, Kunde ohne Adminrechte, FileVault an. Die Kernversion prüft man mit `python3 -m framework.cli version`.

## Paket bauen

Das Appliance-Paket entsteht über die Python-API `build_appliance_package` in `core/framework/appliance.py`; es gibt kein CLI-Kommando dafür. Eine Paketkopie wird außerhalb des Geräts aufbewahrt; den `package_hash` notiert man. Die `core_version` steht in `ACCEPTANCE-RECORD.md` und geht in den `package_hash` ein.

## Erstinstallation

Die Reihenfolge der Startkette steht nicht hier, sondern in `MANUAL-MAC-CHECKS.md` des Pakets und in [docs/v0.4/06-APPLIANCE-SPUR-A.md](../v0.4/06-APPLIANCE-SPUR-A.md). Die Kern-Datenbank legt man mit `gate-init` an. Die Installation prüft man mit `verify-install`. Den Abschluss prüft man mit `verify-device`. Die Ergebnisse trägt man in `ACCEPTANCE-RECORD.md` ein.

## Betrieb

Der Kunde stoppt mit `gate-stop`. Aufheben nur mit berechtigter Rolle über `gate-resume`. Den Zustand liest man mit `gate-status`. Die Auditkette prüft man mit `gate-audit`. Die Annahme zu den Aufruflimits je Klasse steht in [docs/v0.5/171-kostenlimit.md](../v0.5/171-kostenlimit.md). Updates von Hermes oder macOS ohne erneute Abnahme sind nicht vorgesehen.

## Sicherung und Wiederherstellung

Sicherung mit `gate-backup`, Wiederherstellung mit `gate-restore` und `--expect-anchor`. Bei `anchor_mismatch` oder `chain_invalid` abbrechen. Neuaufsetzen aus der letzten Release siehe [docs/v0.4/06-APPLIANCE-SPUR-A.md](../v0.4/06-APPLIANCE-SPUR-A.md), Abschnitt Neuaufsetzen aus der letzten Release. Wie oft gesichert wird, ist offen.

## Grenzen

Siehe [docs/v1/STATUS.md](STATUS.md).
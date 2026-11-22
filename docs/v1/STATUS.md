# Status v1-Kandidat 1.0.0rc1

Dieser Stand ist nicht produktiv freigegeben.

## Implementiert und lokal getestet

- Stopp-Schalter (41575e0, 17831ee, 5484531).
- Kostenlimit-Gate je Zeitfenster und Klasse (58dccbe und dieser Commit). Startwerte als Annahme in der Pilot-1-Policy: Fenster 3600 s, read 160, draft 40, internal_write 80. Nach der Pilotwoche neu festlegen aus `gate_calls`.
- Gerätetestpaket mit Probe-Skript, verify-hermes-probe und ACCEPTANCE-RECORD.md (8c8da50, c30faf0).
- Backup und Wiederherstellung mit Prüfung der Audit-Hashkette (813fe28, 575bef9).
- Version und Release-Unterlagen (79dd30b und dieser Commit).

Lokal getestet heißt: Unittests der Standardbibliothek auf einem Entwicklungsrechner, nicht am Gerät.

## Offen am Gerät

Die gesamte Manuelle Abnahme am Mac mini (siehe [docs/v0.4/06-APPLIANCE-SPUR-A.md](../v0.4/06-APPLIANCE-SPUR-A.md), Abschnitt Manuelle Abnahme), Geräteproben G1–G3 (u. a. wie Hermes Listen im Managed Scope zusammenführt, ob ctx.get_config YAML-Listen als Liste liefert, welchen status post_tool_call liefert), Stopp-Probe S1–S9, Backup-Probe B1–B3, verify-device. Der Worker-Snapshot-Pfad mit Managed-Overlay wird von keiner Probe geprüft.

## Offen bei Google

Der Gmail-MCP ist eine Developer Preview; die Freigabe des Zugangs für Pilot 1 durch Google liegt nicht vor. Ohne sie kein Pilot mit Gmail.

## Offen rechtlich und organisatorisch vor echten Kundendaten

Auftragsverarbeitung oder Vertrag mit dem Kunden, zweckgebundene Modellverarbeitung beim genutzten Modellanbieter, Datenminimierung, Lösch-, Widerspruchs- und Wiederherstellungsprozess, Verantwortlichkeiten und Fernzugang nur mit Zustimmung des Kunden. Dieses Dokument enthält keine Compliance-Zusage. Deutsche Server oder Verzicht auf Modelltraining machen ein Produkt nicht allein datenschutzkonform.

## Nicht enthalten

Senden von Mails, Computer Use, eigene Agentenschleife, Kanäle, echte Mandantenisolation über ein Gerät hinaus, CLI-Kommando zum Paketbau (das Paket entsteht über die Python-API `build_appliance_package`).
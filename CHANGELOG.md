# CHANGELOG

## 1.0.0rc1 (v1-Kandidat, nicht produktiv freigegeben)

- Stopp-Schalter: Kern 41575e0, CLI gate-principal-add/gate-stop/gate-resume/gate-status 17831ee, Gerätecheckliste und Doku 5484531.
- Kostenlimit-Gate je Zeitfenster und Klasse: 58dccbe. Startwerte als Annahme in der Pilot-1-Policy (Fenster 3600 s, read 160, draft 40, internal_write 80; nach Pilotwoche aus gate_calls neu festlegen); dieser Commit.
- Gerätetestpaket: Probe-Skript und verify-hermes-probe 8c8da50, ACCEPTANCE-RECORD.md c30faf0.
- Backup und Wiederherstellung mit Prüfung der Audit-Hashkette: gate-backup/gate-restore 813fe28, Backup-Probe und Anleitung Neuaufsetzen 575bef9.
- Version und Release-Unterlagen: 79dd30b; Abnahmeprotokoll mit core_version, [docs/v1/STATUS.md](docs/v1/STATUS.md) und [docs/v1/INSTALLATION-AND-OPERATIONS.md](docs/v1/INSTALLATION-AND-OPERATIONS.md): dieser Commit.

Nicht enthalten / offen: Abnahme am Mac mini, Developer-Preview-Freigabe des Gmail-MCP bei Google, rechtliche und organisatorische Voraussetzungen vor echten Kundendaten, kein Senden, kein Computer Use. Keine Compliance-Zusagen, keine Preise.
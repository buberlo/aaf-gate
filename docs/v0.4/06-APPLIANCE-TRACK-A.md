# Spur A · Appliance · v0.4

Stand 12.11.2026, 17:01 Europe/Berlin. Entwurf plus ein lokal geprüftes Paket ohne Mac. Produktiv ist nichts.

## Ziel

Der Kunde bekommt einen Mac mini, den Konrad fertig konfiguriert. Darauf läuft Hermes Agent mit einem Profil aus der Branchenvorlage. Der Kunde soll die Sperre im normalen Gebrauch nicht aushebeln. Später können andere Hardware oder eine VM dazukommen. Das ist nicht entworfen.

## Konten

Admin ist nur Konrad. Der Kunde arbeitet als Standardbenutzer ohne Adminrechte und ohne `sudo`. Ob macOS diese Trennung so durchsetzt, ist am Gerät zu prüfen. Physischer Zugriff des Kunden hebt eine reine Software-Sperre auf. Das ist eine ehrliche Grenze, kein Versprechen absoluter Sperre.

## Hermes auf dem Gerät

Belegt ist Managed Scope als root-eigene Dateien, standardmäßig `/etc/hermes/config.yaml` und `/etc/hermes/.env`, Rechte Verzeichnis `0755` und Dateien `0644`. Nur die gepinnten Schlüssel gelten für alle Benutzer. `hermes config set` lehnt sie ab. `hermes doctor` zeigt das Verzeichnis.

Nicht belegt ist ein eigener macOS-Ort. Die Doku nennt v1 Linux/POSIX-first und native macOS-Lagen als außerhalb von v1. Deshalb installiert dieses Repository nichts nach `/etc` und behauptet nicht, dass der Mac mini die Sperre schon hat.

`HERMES_MANAGED_DIR` muss Konrad festlegen. Kann der Kunde die Variable setzen, ist die Sperre weg.

Die Managed-`.env` ist lokal für jeden lesbar. Geheimnisse und die Zugangsdaten des Kunden gehören nicht dorthin. Sie bleiben im Profil des Kunden auf seinem Gerät. Das Paket enthält keine `.env` und keine `auth.json`.

Die Branchenvorlage für Hermes ist eine Profile Distribution: `distribution.yaml`, `SOUL.md`, `config.yaml`. Versionierung über Git-Tag oder Commit ist belegt. `hermes profile update` lässt Erinnerungen, Sitzungen und Zugangsdaten liegen. Neu gelieferte Cron-Jobs kommen pausiert an. Ob wir Cron nutzen, ist offen.

## Freigabe für Computer und Mail

Computer Use ist für macOS belegt. Bedienungshilfen und Bildschirmaufnahme müssen für CuaDriver gesetzt sein. Nach einem Treiberupdate können die Rechte veralten. Klick, Tastatur, Ziehen und Scrollen verlangen dieselbe Freigabe wie gefährliche Befehle. Ohne Antwort wird die Aktion abgelehnt, außer jemand schaltet den unsicheren Modus ein. Diesen Modus pinnt das Paket nicht. Es setzt `approvals.mode: manual`, `cron_mode: deny` und `unattended_mode: deny`, sofern die Vorlage kein unbeaufsichtigtes Senden erlaubt. Das ist unsere Zuordnung belegter Schlüssel.

## Pilot 1: Gmail-Anbindung (Leistungsumfang)

Pilot 1 nutzt für Gmail den offiziellen Gmail-MCP-Server von Google (`https://gmailmcp.googleapis.com/mcp/v1`, in Hermes als MCP-Server `gmail` registriert). Dieser Dienst ist eine Developer Preview. Die Teilnahme muss bei Google beantragt und freigegeben werden; ohne Freigabe ist die Anbindung nicht nutzbar.

Es gibt keine Zusage, dass der Dienst verfügbar, stabil oder unverändert bleibt. Google kann Preview-Bedingungen, Umfang oder den Betrieb jederzeit ändern oder einstellen.

Der offizielle Gmail-MCP liefert in der für Pilot 1 relevanten Werkzeugliste kein Senden-Tool; das passt zum Pilotziel Entwürfe und Lesen ohne automatisches Senden. Zugangsdaten und OAuth entstehen auf dem Mac des Kunden, nicht im Appliance-Paket. Senden über andere Wege unterliegt weiterhin Freigaben (Gate, Hermes `approvals.mode`), sofern überhaupt angeboten.

Label-Werkzeuge (`label_message`, `label_thread`, `unlabel_message`, `unlabel_thread`) dürfen nur Label-IDs setzen oder entfernen, die in `plugins.entries.aaf-gate.settings.label_ids` der zusammengeführten Hermes-Konfiguration stehen. Systemlabels (INBOX, SPAM, TRASH, UNREAD, CATEGORY_* und vergleichbare reservierte IDs) sind nie freigebbar. Ohne gültige `label_ids`-Einstellung lehnt das Gate alle Label-Aufrufe mit `action_class_unknown` ab. Ob Hermes `ctx.get_config` für Listen aus YAML eine Python-Liste liefert, ist am Gerät zu bestätigen.

### label_ids im Managed Scope

Mit Gate-Dienst schreibt `build_appliance_package` optional `label_ids` (sortierte Positivliste eigener Label-Namen) nach `plugins.entries.aaf-gate.settings.label_ids` in `managed/config.yaml`. Ohne Paketparameter ist das `[]` und sperrt Label-Aufrufe, bis ein Paket mit Liste gebaut wird. `label_ids` ohne Gate-Dienst wird mit `LABEL_IDS_WITHOUT_GATE` abgelehnt; ungültige Werte mit `LABEL_IDS_INVALID`. Ob Hermes Listen im Managed Scope mit Benutzerlisten zusammenführt, ist in der Hermes-Doku nicht beschrieben (offener Punkt im Paket, ASSUMPTIONS.md). verify-device meldet `gate_settings_in_profile`, wenn die Benutzer- oder eine Profil-`config.yaml` `aaf-gate`, `socket_path` oder `label_ids` außerhalb der Zeile `    - aaf-gate` der enabled-Liste enthält; alle aaf-gate-Einstellungen kommen ausschließlich aus dem Managed Scope.

## Betrieb

FileVault, Updates, ein Fernzugang nur für Konrad und nur mit Zustimmung des Kunden, Backup, Wiederherstellung und Neuaufsetzen sind Anforderungen an die Appliance. Sie sind nicht als Hermes-Funktionen belegt. Updates von Hermes und macOS können Verhalten ändern. Sie werden nicht automatisch eingespielt.

Der Stopp ist eine Anforderung: der Kunde muss die Hilfe anhalten und die Aufgabe von Hand weiterführen können. Der Stopp ist im Kern implementiert und lokal getestet (gate-stop, gate-resume, gate-status). Am Gerät ist er nicht abgenommen.

## Lieferung und Eigentum

Einrichtung so weit wie möglich aus der Ferne, nachdem der Mac beim Kunden steht. Der erste Admin-Schritt kann einen kurzen lokalen Anteil brauchen. Ob der Kunde die Hardware kauft oder mietet, ist eine offene Geschäftsentscheidung. Kein Preis.

## Was der Code heute tut

Der Kern erzeugt aus der freigegebenen Release ein Verzeichnis mit Distribution, Managed-Config, Annahmen, Rollback-Hinweis und einem Dry-Run-Skript. Der Dry-Run führt kein `sudo` aus und schreibt nicht nach `/etc`. Das ist implementiert und lokal getestet. Es ist keine Installation.

## Manuelle Abnahme am Mac mini

Diese Liste ist nicht ausgeführt. Ausführen am Gerät ist Konrads Schritt.

Pilot 1 ist ohne Senden und ohne Computer Use: Hermes bekommt keine Bedienungshilfen- und keine Bildschirmaufnahme-Freigabe; ein Sendewerkzeug ist nicht freigeschaltet.

- Admin nur Konrad, Kunde ohne Admin und ohne `sudo`.
- FileVault an. Wiederherstellungsschlüssel nur bei den Beteiligten.
- `hermes doctor` zeigt das von Konrad festgelegte Managed-Verzeichnis.
- Der Standardbenutzer kann die Managed-Datei nicht ändern.
- `hermes config set` auf `approvals.mode` wird abgelehnt.
- Der Kunde kann `HERMES_MANAGED_DIR` nicht setzen.
- Die Schritte aus `MANUAL-MAC-CHECKS.md` des Pakets in dieser Reihenfolge: `verify-hermes-dump`, Geräteproben G1–G3, Stopp-Probe S1–S9 (Kunde kann stoppen, Aufheben nur berechtigte Rolle), `gate-audit` mit `--anchor`, Backup-Probe B1–B3, Abschluss `verify-device`.
- Nachweise in `ACCEPTANCE-RECORD.md` des Pakets eintragen, Kopie außerhalb des Geräts.
- Backup und Wiederherstellung der Kern-Datenbank: Backup-Probe B1–B3 aus `MANUAL-MAC-CHECKS.md` (`gate-backup`, `gate-restore`; lokal getestet, am Gerät offen).
- Ein Hermes-Update läuft nicht von allein.

## Neuaufsetzen aus der letzten Release

Diese Anleitung ist nicht am Gerät ausgeführt. Ausführen ist Konrads Schritt.

1. macOS neu einrichten, Konten wie in Manuelle Abnahme: Admin nur Konrad, Kunde ohne Admin, FileVault an.
2. Hermes in der festgelegten Version installieren. Das Update läuft nicht von allein.
3. Die aufbewahrte Paketkopie der letzten aktiven Release einspielen. `verify-install` mit dem `package_hash` aus dem letzten `ACCEPTANCE-RECORD.md` meldet ok true. Ein CLI-Kommando zum Neubau des Pakets gibt es noch nicht.
4. Schritt 1 der Startkette (`gate-init`) entfällt, weil `gate-init` eine vorhandene Datenbank mit `db_exists` ablehnt. Stattdessen `gate-restore --backup <letzte Sicherung> --db <Datenbankpfad laut Paket> --expect-anchor <tenant_id>=<Anker aus dem Manifest>`. Bei `anchor_mismatch` oder `chain_invalid` abbrechen. Die Ausgabe muss den `tenant_id` aus `ACCEPTANCE-RECORD.md` enthalten; `gate-restore` prüft das nicht selbst.
5. `gate-status --tenant <tenant_id>`. War vor dem Ausfall ein Stopp aktiv, der in der Sicherung fehlt, wird `gate-stop` erneut gesetzt.
6. Startkette ab Schritt 2. Den Gmail-Zugang richtet der Kunde auf dem Gerät neu ein, er ist weder im Paket noch in der Sicherung.
7. Vollständige Manuelle Abnahme mit neuem `ACCEPTANCE-RECORD.md`. `verify-device` mit `--audit-anchor <Anker aus Schritt 4>`.

Grenze: Ereignisse nach der letzten Sicherung sind verloren. Wie oft gesichert wird, ist offen.

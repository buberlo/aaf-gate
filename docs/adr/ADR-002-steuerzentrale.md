# ADR-002 · Zweigleisig: Appliance und eigener Kern

Stand 12.11.2026, 17:01 Europe/Berlin. Ersetzt die frühere Fassung vom selben Tag, die Hermes nur als späteren Kanal vorsah. Die Recherche zu Basisplattformen bleibt Datenmaterial. Lizenzen wurden hier nicht erneut abgerufen.

## Entscheidung

Spur A hat Vorrang für den Start. Ein Kunde erhält einen von Konrad fertig eingerichteten Mac mini mit Hermes Agent und einem gesperrten Profil. Computerbedienung und eine Mail-Anbindung sind das Ziel, soweit sie auf dem Gerät geprüft sind. Hermes ist dafür die Startplattform.

Spur B läuft daneben. Der eigene Kern bleibt die Quelle für Branchenvorlage, Kundenwerte, Sperre, Release, Hash und Rollback. Dieselbe Release erzeugt das Paket für Spur A. Eine eigene Agentenschleife mit denselben Fähigkeiten ist der spätere Ersatz, nicht die heutige Laufzeit. Hermes wird dann austauschbar.

Keine der geprüften Fremdplattformen mit Weiterverkaufsgrenze wird zur Basis. Das stützt sich auf die Recherche, nicht auf eine neue Lizenzprüfung.

## Belegte Hermes-Grundlage

Geprüft am 12.11.2026 gegen die öffentliche Doku, nicht gegen ein installiertes Programm:

- Managed Scope unter `/etc/hermes`, root-owned, blattweise stärker als die Benutzerkonfiguration. `hermes config set` lehnt einen gepinnten Schlüssel ab. `hermes doctor` zeigt das Verzeichnis.
- Wer `HERMES_MANAGED_DIR` setzen kann, hebt die Sperre auf. Die Doku verlangt, dass der Administrator diese Variable festlegt.
- Eine defekte Managed-Datei blockiert den Start nicht.
- Die Managed-`.env` ist für alle lokal lesbar. Sie taugt nicht für Geheimnisse.
- Profile Distribution ist ein Git-Repo mit `distribution.yaml`, `SOUL.md` und `config.yaml`. Versionen sind Tags, Branches oder Commit-SHAs. `auth.json` und `.env` bleiben beim Benutzer.
- `approvals.mode` kennt `manual`, `smart` und `off`. `cron_mode` und `unattended_mode` kennen `deny` und `approve`.
- Computer Use ist für macOS beschrieben. Bedienungshilfen und Bildschirmaufnahme gelten für CuaDriver. Zerstörende Bedienung läuft über dieselbe Freigabe wie gefährliche Befehle.
- Der Google-Workspace-Skill beschreibt Gmail. Senden soll erst nach Bestätigung erfolgen.

## Offen oder nur Annahme

- Native Managed-Scope-Orte für macOS sind in v1 ausdrücklich nicht enthalten. Ob `/etc/hermes` auf einem Mac mini gleich wirkt, ist am Gerät zu prüfen.
- Ob `approvals.mode` den Gmail-Skill zwingend erfasst, ist nicht belegt.
- Hermes sperrt `SOUL.md` nicht. Texte dort sind ein Prompt.
- FileVault, Benutzerkonten, Fernwartung und Backup sind macOS, nicht Hermes.
- Die Zuordnung „kein unbeaufsichtigtes Senden“ auf `approvals.mode: manual` ist unsere Nutzung belegter Schlüssel, keine Hermes-Funktion dieses Namens.

## Folge

K1 bleibt das Vorlagenmodell. Dazu erzeugt der Kern ein Appliance-Paket und einen Dry-Run ohne Systemeingriff. Ein echter Mac mini ist damit nicht abgenommen.

# v0.5 — umgesetzte Code-Schritte (Index)

Dieses Verzeichnis ergänzt v0.4 um **Nachweise und Teil-Spezifikationen** zu bereits umgesetztem Code. v0.4 bleibt die Produktrichtung und wird hier nicht umgeschrieben. Stand 22.11.2026.

## Einordnung

- **lokal getestet:** `cd core && python3 -m unittest discover -s tests -q` → derzeit `Ran 719 tests`, `OK`
- **am Gerät getestet:** nein (Mac-mini-Appliance noch nicht abgenommen)
- **produktiv:** nein

## Themenblöcke (Commits, Auszug)

| Bereich | Beispiel-Commits / Inhalt |
|---|---|
| K1 & Release | Release-Nummerierung, Feldhoheit, Principals, Policy-Kern, Audit schreiben, Idempotenz |
| Gerätemodus & Install | device-mode, verify-install, Installationsabgleich, Paket-Hashes (Dateien 96–98, 100–101) |
| Gate-Kern | Gate-Entscheid, GateKeeper, Hermes-Adapter, Unix-Socket, gate-serve |
| Hermes-Plugin | Paket `adapters/hermes/aaf-gate`, Digest, verify-plugin, Ledger/Observations, Socket aus Plugin-Settings (152a), Managed-Scope-Socket (152b) |
| Appliance-Spur A | Paketbau, Launchd-Plist, gate-init, Checklisten (Gate, Pilot, Freigaben, Fremde Hooks), pin-audit, Relay/Telemetrie, Hermes-Commit, verify-device (Install, Managed, Dump, Launchd, Plugin, Profile, active_profile) |
| Pin & Hooks | verify-hooks, pin-audit, raft-platform/disabled, Profil-Sperrlisten |
| Stopp-Schalter (V1-1) | Kern 41575e0, CLI 17831ee, Gerätecheckliste und [168-stopp-schalter.md](168-stopp-schalter.md) |
| Kostenlimit (V1-2) | Kern 58dccbe, Pilot-1-Policy und [171-call-limits.md](171-call-limits.md), dieser Commit |

Einzeldateien in diesem Ordner (z. B. `96-geraetemodus.md`, `97-paket-geraetemodus.md`) beschreiben jeweilige Inkremente im Entwurfswortlaut; maßgeblich für „was läuft“ bleibt der Code und [EVIDENCE-I1.md](../EVIDENCE-I1.md).

## Nicht in diesem Baum

- Grafische Bedien-Konsole: nicht in diesem Baum
- Agenten-Schleife, Provider-Anbindung, produktiver Hermes-Betrieb am Kunden-Mac
# Roadmap · v0.4

Stand 12.11.2026 Europe/Berlin. Kleine Inkremente. Abnahme heißt: der genannte Testlauf ist grün und der Nachweis trennt Entwurf, implementiert und getestet. Produktiv ist keines davon.

| Inkrement | Inhalt | Abnahme |
|---|---|---|
| K1 | Schema, Validierung, Sperre, Release mit Hash, Rollback, Audit, synthetische Werkstattvorlage | Tests grün, T01–T15 grün |
| A1, dieser Lauf | Dieselbe Release erzeugt ein Hermes-Paket und einen Dry-Run ohne Systemeingriff | Pakettest grün, keine Mac-Abnahme |
| A2 | Manuelle Abnahme auf einem echten Mac mini | Liste in der Appliance-Beschreibung, heute offen |
| K2 | Stopp-Schalter und einfaches Budget | Test verweigert die Aktion und schreibt Audit, ohne Netz |
| K3 | Wunsch für ein gesperrtes Feld bleibt liegen, bis der Autor eine neue Release freigibt | Kunde allein ändert das Feld nicht |
| B1 | Eigene Agentenschleife mit denselben Fähigkeiten, Hermes danach austauschbar | Dieselben Freigabe- und Sperrtests ohne Versand |

Spur A hat für den Start Vorrang. Spur B bleibt die gemeinsame Vorlagenquelle.

# Policy-Kern v0 (Entwurf v0.5, Schritt 27)

Der Floor liegt in `core/policy/floor.json` als einzige Quelle; `FLOOR_FORBIDDEN` wird von Rules-Validierung und Service genutzt. Regelsätze dürfen `forbidden_actions` nur verschärfen (Obermenge des Floors); zur Laufzeit gilt Reihenfolge Rechte → Policy → Replay → Handler. Lockerung über `allow_operations` ist verboten (`policy_loosen`). `policy_hash` bereitet Releases vor (Schritt 28).

Offen: Policy auf Lesepfad, `policy.json` je Vorlage unter `templates/<id>`, Schlüsselablauf (68), Budget (48).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
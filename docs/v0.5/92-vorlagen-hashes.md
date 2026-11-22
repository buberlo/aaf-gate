# Erwartete Vorlagen-Hashes (Entwurf v0.5, Schritt 92)

`templates/HASHES.json` listet pro `template_id@template_version` den freigegebenen `body_hash` nach `validate_template` und `hash_value`. Tests (`check_template`) und optional `build_appliance_package(..., expected=...)` vergleichen registrierte Vorlagen bzw. Release-Dokumente mit dieser Liste. Abweichungen ergeben `TEMPLATE_HASH_MISMATCH`, unbekannte Versionen `TEMPLATE_UNLISTED`.

Jede inhaltliche Vorlagenänderung braucht eine neue Versionsnummer und einen aktualisierten Hash-Eintrag. Die Datei ist nicht signiert; sie schützt vor Versehen und Drift im Repo, nicht vor einem Schreibberechtigten am Repository (Signatur: geplanter Punkt 23).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
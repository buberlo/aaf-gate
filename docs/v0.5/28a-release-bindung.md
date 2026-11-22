# Release-Bindung (Entwurf v0.5, Schritt 28a)

Das Release-Dokument enthält `template_body_hash`, `core_schema_version` und `core_floor_hash` neben Werten und Metadaten. Gleich benannte Vorlagen mit unterschiedlichem Inhalt oder Grenzen ergeben verschiedene `release_hash`. Rollback aktiviert ein gespeichertes Dokument unverändert inklusive der Bindungsfelder.

Offen (28b): Lauf bindet `release_hash` bei `create_run`, `STALE_RELEASE` bei Freigabe/Abschluss nach neuem Release, Regelsatz-Hash im Release.

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
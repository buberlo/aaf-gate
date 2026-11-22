# Paket liefert Gerätemodus mit (Entwurf v0.5, Schritt 97)

`build_appliance_package(..., expected=...)` schreibt bei gesetzter Hash-Liste `device/device-mode.json` und `device/HASHES.json` ins Paket. `provenance.json` trägt `device_mode` (`appliance` oder `unlisted`) und `hashes_file_hash` (Hash der Liste oder `null`). Auf dem Gerät kann `ConfigPlane.for_device` die mitgelieferten Dateien nach Rechteanpassung nutzen.

Offen: Installation setzt sichere Dateirechte und Eigentümer (Punkt 22); Liste ist weiterhin unsigniert (Punkt 23).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
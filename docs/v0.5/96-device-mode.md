# Gerätemodus fail-closed (Entwurf v0.5, Schritt 96)

`device-mode.json` legt `appliance` oder `development` fest. `ConfigPlane.for_device(store, mode_path)` lädt die Datei über `devicemode.load_mode`: fehlerhafte oder fehlende Konfiguration wirft `DEVICE_MODE_INVALID` (kein stiller Rückfall). Im Appliance-Modus wird `expected` aus der referenzierten `HASHES.json` geladen; Schreibbarkeit der Modus- und Hash-Datei für Gruppe oder andere wird abgelehnt. Development-Modus setzt `expected=None`.

Offen: Eigentümer-/Kontoprüfung (Punkt 22), Bindung des Modus in `provenance.json` (Punkt 97).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
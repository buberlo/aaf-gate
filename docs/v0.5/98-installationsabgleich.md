# Installationsabgleich (Entwurf v0.5, Schritt 98)

`installcheck.verify_installation` vergleicht ein installiertes Verzeichnis mit dem gebauten Appliance-Paket: fehlende oder geänderte Dateien, unerwartete Dateien in dokumentierten Top-Level-Bereichen (`device/`, `distribution/`, `managed/`), optionaler Abgleich des `package_hash`, und im Appliance-Modus eine fail-closed-Prüfung der mitgelieferten `device-mode.json`. Leere Ergebnisliste bedeutet bestandenen Dateiabgleich.

Grenzen: keine Prüfung von Eigentümern, Konten oder Hermes-Laufzeit (Punkt 22); Vertrauen hängt vom ausführenden Konto ab.

Status: implementiert und getestet im PR-Branch, nicht am Gerät erprobt, nicht produktiv freigegeben.
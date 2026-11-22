# Mandant, Update, Stopp · v0.4

Stand 12.11.2026 Europe/Berlin. Entwurf. Der Code dieses Laufs trennt nur logisch nach `tenant_id`.

## Mandant

Ziel für einen echten Kunden: eigene Laufzeit, eigene Zugangsdaten, eigene Datenbank. Nichts davon ist gebaut. Profile, Rollen oder eine zweite Kennung in derselben SQLite-Datei sind keine Isolation. T12 hat das für den früheren Kern schon gezeigt. Das Konfigurationsmodell verhält sich genauso: eine fremde Kennung sieht die Release nicht, die Datei enthält sie aber.

## Update

Eine neue Vorlage oder eine neue Kundenfassung wird nur als neue Release aktiv. Es gibt kein stilles Überschreiben der laufenden Nummer. Ein Update ist damit derselbe Weg wie eine Freigabe: bauen, hashen, Zeiger umsetzen, Audit.

## Stopp und Fallback

Bevor eine Hilfe später nach außen wirkt, muss der Kunde sie ohne Konrad anhalten und die Aufgabe von Hand weiterführen können. Dieser Schalter ist in diesem Inkrement nicht implementiert. Der Prototyp sendet nichts. Der Schalter ist das nächste Verhaltensinkrement, nicht eine Behauptung über den heutigen Code.

Nachtrag 21.11.2026: Der Schalter ist inzwischen im Kern und in der CLI umgesetzt und lokal getestet, am Gerät noch nicht. Stand und Grenzen: [v0.5/168-stopp-schalter.md](../v0.5/168-stopp-schalter.md).

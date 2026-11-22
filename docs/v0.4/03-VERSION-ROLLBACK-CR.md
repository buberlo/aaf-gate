# Version, Freigabe, Rollback, Änderung · v0.4

Stand 12.11.2026 Europe/Berlin.

## Release

Eine Release ist die aufgelöste Konfiguration: Vorlage, Fassung, Werte, welche Felder gesperrt sind. Sie bekommt eine fortlaufende Nummer und einen SHA-256 über die kanonische Form aus I1 (`canon-1`). Dieselbe Nummer und dieselben Werte ergeben denselben Hash. Die Zeile wird nicht nachträglich geändert.

Die erste Release gibt der Autor frei. Sie ist der Stand, der gilt.

## Freigegebene Felder

Ändert der Kunde ein freigegebenes Feld, entsteht eine neue Release. Die vorherige bleibt gespeichert. Das ist die im ersten Inkrement umgesetzte Änderung, kein stilles Überschreiben.

## Rollback

Der Autor setzt den aktiven Zeiger auf eine ältere Release. Die jüngeren Zeilen bleiben liegen. Der Hash der älteren Release gilt wieder. Ein Kunde kann nicht zurückrollen.

## Änderungswunsch darüber hinaus

Will der Kunde etwas an einem gesperrten Feld, ist das in diesem Stand kein Auftrag, sondern eine Ablehnung. Ein späterer Ablauf kann daraus einen Wunsch machen, den Konrad prüft und nur als neue Release freigibt. Bis dahin gilt: gesperrt bleibt gesperrt.

Jedes Weitergeben nach außen, das die Vorlage nicht vorsieht, bleibt unzulässig. Die synthetische Werkstattvorlage setzt `allow_unattended_send` auf falsch und sperrt das Feld.

# Schichten, Sperre und Rechte · v0.4

Stand 12.11.2026 Europe/Berlin. Entwurf, außer wo der Nachweis einen Test nennt.

## Drei Schichten

1. Kern. Er prüft die Fassung, lehnt verbotene Änderungen ab, schreibt Audit und kennt keine Branchenbesonderheit.
2. Branchenvorlage. Sie benennt die Felder einer Aufgabenart und je Feld, ob es gesperrt oder für den Kunden freigegeben ist. Die Vorlage ist versioniert und wird nicht still überschrieben.
3. Kundenkonfiguration. Sie füllt die Felder für einen synthetischen oder späteren echten Kunden. Wirksam ist nur eine freigegebene Release, nicht ein Entwurf.

## Wer was ändert

| Rolle | Darf |
|---|---|
| Autor, Konrad | Vorlage registrieren, jede Feldänderung in eine neue Release geben, zurückrollen |
| Kunde | Nur Felder mit `customer_editable` ändern. Das erzeugt eine neue Release. Gesperrte Felder werden abgelehnt. |
| Ohne Rolle | Nichts |

Eine Änderung an einem gesperrten Feld durch die Kundenrolle ändert die aktive Fassung nicht. Sie wird als Ablehnung protokolliert. Der Versuchstext steht nicht in der Fehlermeldung.

Im ersten Inkrement gibt es keine Warteschlange für gesperrte Änderungswünsche. Sie werden abgelehnt. Eine spätere Prüfung durch Konrad ist ein eigenes Inkrement.

## Was nicht gilt

Eine Rolle oder eine Kennung in derselben Datei ist keine eigene Kundenumgebung. Das steht auch in ADR-002.

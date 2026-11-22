# Nachweis I1

Hinweis zum öffentlichen Stand (22.11.2026): Die historischen Entwurfspakete `docs/v0.1`–`docs/v0.3` und die Marketing-Website unter `web/` sind in diesem öffentlichen Repositorium nicht enthalten. Der nachstehende Text ist der ursprüngliche lokale I1-Nachweis und bleibt als Beleg unverändert.


Stand 08.11.2026 Europe/Berlin. Dieser Nachweis ist getrennt von den Entwürfen unter `v0.1/` und `v0.2/`. Deren Statusformulierungen bleiben unverändert: dort ist I1 nicht implementiert und die Tests sind geplant. Hier steht nur, was in diesem Repository ausgeführt wurde.

## Einordnung

Implementiert: ein lokaler Einzelprozess `material-prep-1` mit SQLite, CLI und den in ADR-001 beschriebenen Entscheidungen.

Getestet: die fünfzehn Muss-Tests T01–T15, zweimal nacheinander erfolgreich.

Produktiv: nein. Kein Kundenzugriff, kein Scheduler, kein Modell, kein Netz- oder Versandadapter, keine HTTP-Schnittstelle.

T12 belegt nur die logische Abweisung in einem gemeinsamen Datenbankfile. Dieselbe Datei enthält die Inhalte beider Demo-Kennungen. Das ist keine Kundenisolation.

Nicht ausgeführt: T16–T19, S01–S04 und die Dokumentprüfung DOC als Softwaretest. I2, I3 und I4 sind nicht begonnen.

## Befehl

```bash
cd core && python3 -m unittest discover -s tests -v
cd core && python3 -m framework.cli
```

## Beobachtetes Ergebnis

Erster Lauf: `Ran 15 tests`, `OK`, etwa 0,58 s.
Zweiter Lauf direkt danach: `Ran 15 tests`, `OK`, etwa 0,59 s.

Der CLI-Demolauf endet im Zustand `REVIEW`, zeigt den Konflikt der Mengen 20 und 24, die offene Frage `lieferdatum`, keine belegten Fakten und `network: disabled`. Eine Freigabe spricht der CLI nicht selbst aus.

| Test | Beobachtung in diesem Lauf |
|---|---|
| T01 | Hash, Locator und synthetic bleiben erhalten; Änderung derselben Revision wird abgelehnt; neue Fassung nur als neue SourceRevision |
| T02 | Fehlender Locator, unbekannte Revision, manipulierter Hash und fremde Quelle werden nicht als Fakt bestätigt; keine Fachmutation; Lieferdatum bleibt offen |
| T03 | Konflikt 20/24 mit beiden Belegen, keine übernommene Menge, kein erfundenes Datum; CLI zeigt dasselbe |
| T04 | D3 ändert Regelhash, Zustand und Rollen nicht und ruft kein Werkzeug auf |
| T05 | Commit aus ERFASST, unbekanntes Kommando und Resume ohne Block bzw. ohne Auflösung lassen den Fachzustand unverändert; Ablehnung wird auditiert, soweit der Speicher trägt |
| T06 | Rolle ohne Reviewrecht schreibt keine Freigabe; fremde Kennung sieht Approval und Receipt nicht |
| T07 | Abweichender Payload-Hash, Payload-Revision, Regelhash, Regelversion oder Manifest ergibt APPROVAL_INVALID ohne Speicherung; widerrufene Freigabe ebenfalls |
| T08 | Gleicher Schlüssel und Hash: ein Commit, eine Ergebnis-ID, auch nach verlorener Antwort und bei zwei gleichzeitigen Aufrufen |
| T09 | Gleicher Schlüssel, anderer Hash: IDEMPOTENCY_CONFLICT, altes Ergebnis bleibt |
| T10 | Zwei Schlüssel, gleiche Ausgangsversion: ein Erfolg, ein STALE_REVISION |
| T11 | Fehler nach Zustand, nach Audit und vor Commit stellen den vorherigen Stand wieder her; nach Commit liefert der Retry das Receipt |
| T12 | Fremde Run-, Receipt-, Approval-, Export-, Such- und Cachezugriffe liefern keinen B-Inhalt. Die Rohdatei enthält ihn dennoch |
| T13 | Feedback lässt Regeln und Entscheidungen unverändert; eine neue Regelversion gilt nur für einen neuen Run |
| T14 | Gleiche Snapshots ergeben gleiche Claim- und Guardentscheidungen; Resume kehrt nach REVIEW zurück und überspringt die Freigabe nicht |
| T15 | Fehlende Datenbank, fehlendes Audit, fehlender Actor und verbotene Netzaktion enden ohne Erfolg und ohne Socket |

## Grenzen des Nachweises

Die Tests laufen auf einem lokalen Python 3.12 gegen eine temporäre SQLite-Datei. Sie sind keine fachliche Kundenabnahme, keine Lastprüfung und kein Nachweis für Hosttrennung, Backups, Restore oder Löschung.

## Konfigurationsmodell K1

Stand 12.11.2026 Europe/Berlin. Implementiert und lokal getestet. Nicht produktiv.

Eine synthetische Branchenvorlage `auto-service-appointment-intake` und eine synthetische Kundenkonfiguration durchlaufen Registrierung, Release mit Hash, Ablehnung eines gesperrten Feldes, Änderung eines freigegebenen Feldes und Rollback. Die ältere Release bleibt unverändert. Eine fremde Kennung sieht die Fassung nicht. Das ist keine eigene Kundenumgebung.

Befehl: `cd core && python3 -m unittest discover -s tests -v`. Ergebnis 12.11.2026, erster K1-Lauf: `Ran 21 tests`, `OK`. Der Appliance-Dry-Run kommt im selben Befehl dazu und ist unten nur beschrieben, wenn der Lauf ihn enthält.

Spur A erzeugt aus dieser Release ein Hermes-Paket. Es schreibt nichts auf das System. Managed Scope auf macOS ist nicht als native Lage belegt und im Paket als offen gekennzeichnet. Der gemeinsame Testlauf nach den Profilen ist `Ran 28 tests`, `OK`: T01–T15, sieben Konfigurationstests einschließlich array, enum und min/max, vier Pakettests und zwei Profiltests.

## Verständlichkeitsdurchgang der Homepage

Stand 08.11.2026 Europe/Berlin. Nur die öffentliche Seite, die Inhaltsprüfung und dieser Absatz. `docs/v0.1`, `docs/v0.2`, `docs/v0.3` und der Prototypcode sind nicht geändert.

Die Seite sagt in Alltagssprache, was erleichtert wird, zeigt drei frei erfundene Beispiele mit Ausgangslage, Hilfe, Ergebnis und verbleibender Verantwortung und beschreibt den Weg von der ersten Nachricht bis zur Übergabe. Agentic AI steht erst weiter unten und ist dort in einem Satz erklärt. Eine Textprüfung sichert den Hero. Sie ist keine Nutzerstudie.

Prüfung dieses Durchgangs: Typecheck, Build, angepasste Inhaltsprüfung und Browser auf Desktop 1440×900 sowie Mobil 390×844, einschließlich geöffneter Frage, axe ohne schwere oder kritische Befunde, keine Formulare und HTTP 404 für interne Dokumente. I1-Regression bei unverändertem `core/`: `Ran 15 tests`, `OK`, etwa 0,52 s. Kein Deployment.

## Homepage und v0.3, getrennt vom Prototyp

Stand 08.11.2026 Europe/Berlin. Der Prototypcode ist in diesem Inkrement nicht geändert. v0.1 und v0.2 sind nicht umgeschrieben. Neu sind `docs/v0.3/` und die geschärfte Seite.

Die Seite beschreibt wiederkehrende, digital zugängliche Informationsabläufe für Unternehmen unterschiedlicher Branchen und Größen. Sie legt keine Preisstrategie fest und kennzeichnet die interne Demonstration als frei erfunden. Eine wirtschaftliche Tragfähigkeit ist nicht behauptet.

Verständlichkeitsprüfung: Textprüfung des Hero, keine Nutzerstudie. Der Hero nennt das Angebot, die branchenübergreifende Zielgruppe, das Vorstrukturieren von Anfragen und den Start über den Ablauf-Check. Der Messhinweis steht bei der Demonstration, nicht im Hero.

Regression der unveränderten I1-Tests am 08.11.2026: `python3 -m unittest discover -s core/tests -q` aus dem Repository, `Ran 15 tests`, `OK`, etwa 0,58 s. Homepage danach: Typecheck, Build, Inhaltsprüfung einschließlich der neuen Dokumentlinks, Browser Desktop 1440×900 und Mobil 390×844, axe ohne schwere oder kritische Befunde, HTTP 404 für interne Dokumente. Kein Deployment.

## Gesamtlauf Kern und Doku-Update

Stand 15.11.2026 Europe/Berlin. README, `docs/v0.5/README.md` und Homepage-Stand (`web/stand.html`) an den Code auf Branch `cursor/homepage-i1-v01-a1f9` angeglichen. Historische Abschnitte oben unverändert.

Befehl: `cd core && python3 -m unittest discover -s tests -q`

Beobachtetes Ergebnis: `Ran 467 tests`, `OK`, etwa 11,4 s.

Am Gerät getestet: nein. Produktiv: nein. Kein Deployment.

# Pilot 1: Messung und pilot-baseline

Stand November 2026. Ergänzung zu den Abnahmezeilen im Appliance-Paket (Pilot-1-Checkliste, Punkte 4 und 5) und zum CLI-Befehl `pilot-baseline`.

## gate-audit --summary

Am Ende der Pilotphase wird eine Zusammenfassung mit `gate-audit --summary` erzeugt. Darin stehen unter anderem `summary.total`, `summary.by_class`, `summary.by_tool` und seit der Gate-Beobachtungszählung `summary.observations` mit `gate_mismatch`, `gate_unseen` und `other`.

## pilot-baseline

Der Befehl `python3 -m framework.cli pilot-baseline --summary <Datei> --timesheet <Datei>` rechnet aus der bestätigten Summary und einer Zeiterfassungsdatei (`schema_version` 1 oder 2, Einheit `minutes`) Median- und Handarbeitsminuten je Werkzeugklasse. Optional `--min-samples` (Standard 5).

Die Ausgabe enthält unter `baseline` unter anderem `manual_minutes_total`, `net_minutes_total` (bei schema_version 2 mit ausreichenden Review-Stichproben) sowie `observations` und `observations_clean`, sofern die Summary `summary.observations` enthält.

## Vorbedingung für Minutenangaben nach außen

Minutenzahlen aus `pilot-baseline` (Einzelklassen, `manual_minutes_total`, `net_minutes_total`) dürfen nur verwendet oder an Kunden weitergegeben werden, wenn `baseline.observations_clean` genau `true` ist. Ist `observations_clean` `false` oder `null`, werden keine Minutenzahlen nach außen gegeben; die Summary und das Audit sind dann zuerst zu klären (z. B. mit `gate-audit` ohne `--summary` wie in Punkt 4 der Checkliste).

## --require-clean

`--require-clean` beendet `pilot-baseline` mit Exit-Code 1 und `{"error": "observations_not_clean", "ok": false}`, wenn `observations_clean` nicht `true` ist (einschließlich fehlender oder alter Summaries ohne `observations`). Die Rechenfelder werden in diesem Fall nicht als erfolgreiche Abnahme ausgegeben.

## Freigegebene Ausführungen

Enthält die Summary `summary.approvals_by_class`, liefert `pilot-baseline` je Klasse zusätzlich `approved_executed`, `manual_minutes_approved` und `net_minutes_approved` sowie auf Top-Level `approved_incomplete`, `manual_minutes_approved_total` und `net_minutes_approved_total`. Diese Approved-Minuten sind getrennt von `manual_minutes_total` und `net_minutes_total`; sie sind eine Obergrenze, weil `approval_granted` auch fehlgeschlagene Ausführungen (`post_tool_call` mit `status` `error`) umfasst. Die Approved-Felder sind nur intern; sie werden nicht an Kunden weitergegeben. Dafür gilt zusätzlich die Regel zu `observations_clean`.
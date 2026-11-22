# 168 Stopp-Schalter (V1-1)

## Status

Implementiert und lokal getestet (Kern 41575e0, CLI 17831ee, Doku und Gerätecheckliste in diesem Commit). Nicht am Gerät getestet. Nicht produktiv.

## Wirkung

Der Stopp gilt je Tenant. Solange er aktiv ist, lehnt das Gate jede Anfrage mit dem Grund `stopped` ab und schreibt einen Audit-Eintrag mit `operation` `gate_decide` und `error_code` `stopped`.

## Rollen

Stoppen darf jeder menschliche Principal des Tenants, der mindestens eine der Rollen `operator`, `reviewer`, `author` oder `customer` hat. Aufheben darf nur ein menschlicher Principal mit Rolle `author`. Agent- und Service-Principals dürfen weder stoppen noch aufheben. Stopp und Aufheben werden als `gate_stop` bzw. `gate_resume` protokolliert, ebenso abgelehnte Versuche.

## Befehle

- `gate-principal-add`: legt einen menschlichen Principal mit Rollen an.
- `gate-stop`: setzt den Stopp für den Tenant des Principals.
- `gate-resume`: hebt den Stopp auf (nur Rolle `author`).
- `gate-status`: liest den Stopp-Zustand (nur lesend, ohne Migration).
- `gate-audit --summary`: enthält `summary.stops` mit `stop`, `resume`, `rejected`, `denied_while_stopped` und dem aktuellen `stopped`.

## Exit-Codes (gate-stop / gate-resume)

- `0`, wenn `ok` true ist (auch bei `noop` mit `changed` false).
- `1` bei `principal_unknown`, `principal_kind_forbidden` oder `role_forbidden`.
- `2` bei Vorprüfungs-, Speicher- oder Auditfehlern.

## Offene Grenzen

Das Anlegen von Principals und Rollenbindungen über `gate-principal-add` wird nicht auditiert und ist nicht atomar (bekannt, nach v1). Die Zustimmung des Kunden-Admins beim Aufheben ist organisatorisch. Wie Hermes die Ablehnung im Stopp anzeigt, ist am Gerät zu prüfen. Die Gerätecheckliste enthält die Stopp-Probe im Abschnitt Pilot 1.

Nicht am Gerät getestet.
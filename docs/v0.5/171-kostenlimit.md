# 171 Kostenlimit (V1-2)

Status: implementiert und lokal getestet. Nicht am Gerät getestet. Nicht produktiv.

## Annahme E48-1

Datum: 22.11.2026 22:16 Europe/Vienna. Der Rechenweg ist eine Annahme ohne Messung: 20 Mails pro Stunde, mit Aufrufen je Mail read 4, draft 1, internal_write 2 und Faktor 2 für das gleitende Fenster von 3600 Sekunden.

## Startwerte Pilot 1

| Klasse | Fenster (s) | max_calls |
|--------|-------------|-----------|
| read | 3600 | 160 |
| draft | 3600 | 40 |
| internal_write | 3600 | 80 |

Nach einer Pilotwoche werden die Werte aus `gate_calls` neu festgelegt.

## Grenzen

E125-1: Das Kostenlimit ersetzt die unbeaufsichtigte Sperre nicht (`internal_write` bleibt ohne Freigabe auf `level_L2` begrenzt).

F429: Eine Policy mit `limits` auf einem älteren Kern ohne Limit-Unterstützung führt zu `invalid_policy`.
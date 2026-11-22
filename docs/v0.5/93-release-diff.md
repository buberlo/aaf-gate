# Release-Diff für Prüfer (Entwurf v0.5, Schritt 93)

Die Leseoperation `release_diff` vergleicht das beim Laufstart gebundene Konfigurations-Release mit dem aktuell aktiven Release. Nach `STALE_RELEASE` sieht ein Prüfer mit Operator- oder Reviewer-Rolle, welche Felder sich geändert haben. Volle Werte erscheinen nur für `customer_editable`-Felder; gesperrte oder feste Felder nur als Name in `changed`. Die Antwort wird nicht gecacht, weil sich das aktive Release laufend ändern kann.

Offen: Darstellung in der UI, Pflicht zur Kenntnisnahme vor erneutem `approve` (noch zu entscheiden).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.
# Sicherheitslisten mit Mindestinhalt (Entwurf v0.5)

- Vorlagen können Array-Felder mit `contains` (Pflichtelemente), `unique` und `item_type: domain` führen.
- Pflichtelemente kann der Autor ergänzen, aber nicht entfernen. Entfernen geht nur über eine neue Vorlagenversion. Das ist schwächer als `fixed` und erlaubt projektspezifische Ergänzungen.
- Domains werden nicht normalisiert, sondern bei falschem Format abgelehnt. So bleibt der gespeicherte Wert gleich dem eingegebenen.
- `b2b-lead-recherche` 0.3.0 verlangt `linkedin.com` in `blocked_domains`. `ecommerce-produktverkauf` 0.3.0 verlangt eine nicht leere Domainliste ohne festgelegte Pflichteinträge (offene Entscheidung).
- Die Listen haben im Kern noch keine Laufzeitwirkung. Sie wirken erst über den Paketbau (Hermes website_blocklist) und das Konnektor-Gate.

Status: implementiert und getestet nur im PR-Branch, nicht produktiv freigegeben.

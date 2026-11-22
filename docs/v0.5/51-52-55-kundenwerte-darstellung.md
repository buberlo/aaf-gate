# Kundenwerte im Agenten-Kontext (Entwurf v0.5)

- Jeder Wert erscheint in SOUL.md als JSON-Darstellung in einer Zeile (canon.render_value). Zeilenumbrüche sind maskiert, Kundenwerte erzeugen keine Überschriften, Listen oder Codeblöcke.
- Die Maskierung macht fremden Text für ein Modell nicht harmlos. Sie trennt Daten und Anweisung im Dokument eindeutig. Die Sicherung bleibt bei Policy-Floor, Gate und fehlender Sendeoperation.
- Kern-Floor für Texte: nicht leer, keine Steuer- und Formatzeichen außer Zeilenumbruch und Tab, höchstens 4000 Zeichen je Text, 200 Elemente je Liste, 500 Zeichen je Element. Das sind Annahmen, keine Messwerte. Vorlagen dürfen nur verschärfen.
- Bezeichner haben ein enges Muster: template_id aus a-z, 0-9 und Bindestrich, Feldnamen aus a-z, 0-9 und Unterstrich, je bis 63 Zeichen.

Status: implementiert und getestet nur im PR-Branch, nicht produktiv freigegeben.

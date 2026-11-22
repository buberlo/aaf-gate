# Feldhoheit fixed (Entwurf v0.5)

- Vorlagen können Felder mit `access: fixed` und Pflicht `value` führen. Der Wert fließt beim Release in das Dokument und in `release_hash`.
- Profil- und Konfigurationswerte dürfen keine `fixed`-Felder setzen; der Autor kann sie nicht per Release oder Änderung überschreiben. Nur eine neue Vorlagenversion ändert den Wert.
- `allow_*`-Sicherheitsfelder stehen in den Repo-Vorlagen kfz `1.1.0`, b2b-lead-research und ecommerce-product-sales (ab `0.2.0`, Listenregeln ab `0.3.0`) auf `fixed: false`. Der Kern prüft den Policy-Floor für alle `allow_*`-Werte im aufgelösten Dokument.
- Der Paketbau verlässt sich auf das aufgelöste Release, nicht auf eine eigene Floor-Prüfung.

Status: implementiert und getestet nur im PR-Branch, nicht produktiv freigegeben.

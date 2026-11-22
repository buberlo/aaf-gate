# Lauf an Konfigurations-Release binden (Entwurf v0.5, Schritt 28b)

`create_run` kann optional `config_id` mitgeben; der Lauf speichert dann `config_id` und das aktive `release_hash` zum Startzeitpunkt. `approve` und `commit` vergleichen per `releases.active_release_hash` mit dem gebundenen Hash und lehnen mit `STALE_RELEASE` ab, wenn ein anderes Release aktiv ist. Rollback auf ein inhaltsgleiches Release (gleicher Hash) gilt nicht als veraltet.

Offen: `rules_hash`/`policy_hash` in die Freigabe binden (30), Regelsatz-Scope (88).

Status: implementiert und getestet im PR-Branch, nicht produktiv freigegeben.